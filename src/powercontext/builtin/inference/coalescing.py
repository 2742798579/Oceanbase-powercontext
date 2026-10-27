# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Share and cache Embedding work across concurrent callers.

Memory writes and vector searches frequently ask for the same text at the same
time. This wrapper keeps one pending computation per distinct text, so every
caller that needs it waits on the same underlying request instead of paying for
it again. Finished vectors go to a bounded LRU cache.

The wrapper never lets one caller's fate decide another's: cancelling a waiter
only stops that waiter, and a failed request fails its waiters without being
cached.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult, InferenceUsage

_DEFAULT_MAX_BATCH_SIZE = 64
_DEFAULT_COALESCE_MS = 5.0
_DEFAULT_CACHE_SIZE = 1024


@dataclass(slots=True)
class _Slot:
    """One in-flight text shared by every caller that needs it."""

    future: asyncio.Future[tuple[float, ...]] = field(default_factory=asyncio.Future)
    batch: int | None = None


class CoalescingEmbeddingModel:
    """Deduplicate, batch, share and cache Embedding requests."""

    def __init__(
        self,
        model: object,
        *,
        max_batch_size: int = _DEFAULT_MAX_BATCH_SIZE,
        coalesce_ms: float = _DEFAULT_COALESCE_MS,
        cache_size: int = _DEFAULT_CACHE_SIZE,
    ) -> None:
        if not isinstance(max_batch_size, int) or isinstance(max_batch_size, bool) or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003, TRY004
        if isinstance(coalesce_ms, bool) or not isinstance(coalesce_ms, (int, float)) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be a non-negative number")  # noqa: TRY003, TRY004
        if not isinstance(cache_size, int) or isinstance(cache_size, bool) or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")  # noqa: TRY003, TRY004

        self._model = model
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = float(coalesce_ms) / 1000.0
        self._cache_size = cache_size
        self._cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._pending: dict[str, _Slot] = {}
        self._batch_usage: dict[int, InferenceUsage] = {}
        self._next_batch = 0
        self._flushing = False

    @property
    def profile(self) -> EmbeddingProfile:
        return self._model.profile  # type: ignore[attr-defined]

    async def embed(self, texts: Sequence[str], /) -> EmbeddingResult:
        """Return one vector per input text, in the caller's original order."""

        ordered = tuple(texts)
        if not ordered:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        unique: list[str] = []
        seen: set[str] = set()
        for text in ordered:
            if text not in seen:
                seen.add(text)
                unique.append(text)

        resolved: dict[str, tuple[float, ...]] = {}
        waited: list[tuple[str, _Slot]] = []
        scheduled = False
        for text in unique:
            cached = self._cache_get(text)
            if cached is not None:
                resolved[text] = cached
                continue
            slot = self._pending.get(text)
            if slot is None:
                slot = _Slot()
                slot.future.add_done_callback(_consume_exception)
                self._pending[text] = slot
                scheduled = True
            waited.append((text, slot))
        if scheduled:
            self._schedule_flush()

        if waited:
            # ``shield`` keeps the shared computation alive when one waiter is
            # cancelled; the batch keeps running for everyone else.
            results = await asyncio.gather(*(asyncio.shield(slot.future) for _text, slot in waited))
            for (text, _slot), vector in zip(waited, results, strict=True):
                resolved[text] = vector

        vectors = tuple(resolved[text] for text in ordered)
        return EmbeddingResult(vectors=vectors, usage=self._usage_for(waited))

    def _cache_get(self, text: str) -> tuple[float, ...] | None:
        if self._cache_size == 0:
            return None
        vector = self._cache.get(text)
        if vector is None:
            return None
        self._cache.move_to_end(text)
        return vector

    def _cache_put(self, text: str, vector: tuple[float, ...]) -> None:
        if self._cache_size == 0:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def _usage_for(self, waited: Sequence[tuple[str, _Slot]]) -> InferenceUsage:
        batches = {slot.batch for _text, slot in waited if slot.batch is not None}
        requests = 0
        input_tokens: int | None = 0
        output_tokens: int | None = 0
        for batch in batches:
            usage = self._batch_usage.get(batch)
            if usage is None:
                continue
            requests += usage.requests
            input_tokens = _add_tokens(input_tokens, usage.input_tokens)
            output_tokens = _add_tokens(output_tokens, usage.output_tokens)
        return InferenceUsage(requests=requests, input_tokens=input_tokens, output_tokens=output_tokens)

    def _schedule_flush(self) -> None:
        if self._flushing:
            return
        self._flushing = True
        asyncio.get_running_loop().create_task(self._flush())

    async def _flush(self) -> None:
        try:
            await asyncio.sleep(self._coalesce_seconds)
            while self._pending:
                pending = list(self._pending.items())
                batches = [
                    pending[index : index + self._max_batch_size]
                    for index in range(0, len(pending), self._max_batch_size)
                ]
                await asyncio.gather(*(self._run_batch(batch) for batch in batches))
        finally:
            self._flushing = False

    async def _run_batch(self, batch: list[tuple[str, _Slot]]) -> None:
        batch_id = self._next_batch
        self._next_batch += 1
        texts = tuple(text for text, _slot in batch)
        for _text, slot in batch:
            slot.batch = batch_id

        try:
            result = await self._model.embed(texts)  # type: ignore[attr-defined]
        except asyncio.CancelledError:
            self._release(batch, None)
            raise
        except BaseException as error:  # noqa: BLE001 - every waiter sees the same failure
            self._release(batch, None)
            for _text, slot in batch:
                if not slot.future.done():
                    slot.future.set_exception(error)
            raise
        else:
            vectors = tuple(result.vectors)
            if len(vectors) != len(texts):
                error = ValueError(
                    f"embedding model returned {len(vectors)} vectors for {len(texts)} texts"
                )
                self._release(batch, None)
                for _text, slot in batch:
                    if not slot.future.done():
                        slot.future.set_exception(error)
                return
            self._batch_usage[batch_id] = result.usage
            self._release(batch, vectors)
            for (_text, slot), vector in zip(batch, vectors, strict=True):
                if not slot.future.done():
                    slot.future.set_result(vector)

    def _release(self, batch: Sequence[tuple[str, _Slot]], vectors: tuple[tuple[float, ...], ...] | None) -> None:
        for index, (text, slot) in enumerate(batch):
            if self._pending.get(text) is slot:
                del self._pending[text]
            if vectors is not None:
                self._cache_put(text, vectors[index])


def _add_tokens(total: int | None, value: int | None) -> int | None:
    if total is None or value is None:
        return None
    return total + value


def _consume_exception(future: asyncio.Future[object]) -> None:
    """Mark a shared failure as observed so a cancelled waiter logs nothing."""

    if future.cancelled():
        return
    future.exception()


__all__ = ["CoalescingEmbeddingModel"]
