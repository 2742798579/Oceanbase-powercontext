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

"""Score Memory candidates with one HTTP cross-encoder service.

The service caps how many candidates it scores per call, so the client splits
the pool into batches. Every candidate keeps its position in the complete input
(its global one-based ``rank``), which is what lets the scores of all batches
take part in one global selection afterwards. Identity never changes: the
decision only carries original ranks.
"""

from __future__ import annotations

import math
from typing import Any

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision, _validate_bounds
from powercontext.builtin.inference import InferenceUsage

HTTP_RERANK_POLICY_ID = "http-cross-encoder-v1"
_DEFAULT_BATCH_SIZE = 32
_RESPONSE_FIELDS = frozenset({"scores"})
_SCORE_FIELDS = frozenset({"rank", "score"})


class HTTPMemoryReranker:
    """Ask a remote scoring service for one score per candidate."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        policy_id: str = HTTP_RERANK_POLICY_ID,
    ) -> None:
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("rerank endpoint must be a non-empty URL")  # noqa: TRY003, TRY004
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError("rerank batch_size must be a positive integer")  # noqa: TRY003, TRY004
        if not isinstance(policy_id, str) or not policy_id.strip():
            raise ValueError("rerank policy_id must be a non-empty string")  # noqa: TRY003, TRY004
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Score every candidate exactly once and select the global best ranks."""

        _validate_bounds(len(candidates), limit)

        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = candidates[start : start + self._batch_size]
            ranks = tuple(range(start + 1, start + 1 + len(batch)))
            response = await self._client.post(
                self._endpoint,
                json={
                    "query": query,
                    "candidates": [
                        {"rank": rank, "text": candidate.text}
                        for rank, candidate in zip(ranks, batch, strict=True)
                    ],
                },
            )
            requests += 1
            response.raise_for_status()
            scores.update(_read_scores(response, ranks))

        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return MemoryRerankDecision(
            selected_ranks=tuple(rank for rank, _score in ordered[:limit]),
            usage=InferenceUsage(requests=requests),
        )


def _read_scores(response: httpx.Response, expected_ranks: tuple[int, ...]) -> dict[int, float]:
    """Validate one batch response and return ``rank -> score``."""

    try:
        body: Any = response.json()
    except ValueError as error:
        raise ValueError(f"rerank response is not valid JSON: {error}") from error
    if not isinstance(body, dict) or set(body) != _RESPONSE_FIELDS:
        raise ValueError("rerank response must contain exactly the scores field")  # noqa: TRY003
    entries = body["scores"]
    if not isinstance(entries, list):
        raise ValueError("rerank scores must be a list")  # noqa: TRY003, TRY004

    scores: dict[int, float] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _SCORE_FIELDS:
            raise ValueError("every rerank score must contain exactly rank and score")  # noqa: TRY003
        rank = entry["rank"]
        score = entry["score"]
        if type(rank) is not int:
            raise ValueError("rerank rank must be an integer")  # noqa: TRY003, TRY004
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("rerank score must be a number")  # noqa: TRY003, TRY004
        if not math.isfinite(float(score)):
            raise ValueError("rerank score must be finite")  # noqa: TRY003
        if rank in scores:
            raise ValueError(f"rerank rank {rank} was scored twice")  # noqa: TRY003
        scores[rank] = float(score)

    if set(scores) != set(expected_ranks):
        raise ValueError("rerank response must cover exactly this batch's ranks")  # noqa: TRY003
    return scores


__all__ = ["HTTP_RERANK_POLICY_ID", "HTTPMemoryReranker"]
