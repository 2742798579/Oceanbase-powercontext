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

"""Propagate Source availability through the evidence graph of one scope.

A Source that stops being usable must not keep supporting Memory or Topic Memory
content. This service stores an explicit dependency graph per scope, propagates
one Source lifecycle event through every descendant under its ``all``/``any``
policy, and reflects the result in the native Memory and Topic Memory views.
Withdrawing only changes availability: bodies and historical citations stay
readable for audit.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory.models import MemoryCitation
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_TABLE
from powercontext.builtin.runtime.errors import InvalidRuntimeRequestError
from powercontext.sources import SourceRef

LifecyclePolicy = Literal["all", "any"]
LifecycleKind = Literal["source", "memory", "topic"]
LifecyclePurpose = Literal["generation", "audit"]

_KINDS = frozenset({"source", "memory", "topic"})
_POLICIES = frozenset({"all", "any"})
_PURPOSES = frozenset({"generation", "audit"})


class SourceLifecycleError(ValueError):
    """Raised when a lifecycle registration, event or read violates the contract."""


class SourceLifecycleEvent(BaseModel):
    """One validated Source availability transition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    revision: int = Field(gt=0, strict=True)
    sequence: int = Field(gt=0, strict=True)
    available: bool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("lifecycle text fields must not be empty or whitespace only")  # noqa: TRY003
        return value


class _Node:
    """One registered node and its current availability."""

    __slots__ = ("available", "kind", "node_id", "payload", "policy", "parents", "revision", "sequence")

    def __init__(
        self,
        node_id: str,
        kind: str,
        payload: dict[str, Any],
        parents: tuple[str, ...],
        policy: str,
        available: bool,
        revision: int,
        sequence: int,
    ) -> None:
        self.node_id = node_id
        self.kind = kind
        self.payload = payload
        self.parents = parents
        self.policy = policy
        self.available = available
        self.revision = revision
        self.sequence = sequence

    @property
    def binding(self) -> tuple[str, dict[str, Any], tuple[str, ...], str]:
        return (self.kind, self.payload, self.parents, self.policy)

    def to_row(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "payload": json.dumps(self.payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            "parents": json.dumps(list(self.parents), separators=(",", ":")).encode("utf-8"),
            "policy": self.policy,
            "available": self.available,
            "revision": self.revision,
            "sequence": self.sequence,
        }


class ScopedSourceLifecycle:
    """Register one evidence graph and propagate Source availability through it."""

    def __init__(self, runtime: Any, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = scope_id

    async def register_source(self, node_id: str, source_ref: SourceRef, /, *, revision: int) -> None:
        _require_node_id(node_id)
        if type(revision) is not int or revision < 1:
            raise SourceLifecycleError("revision must be a positive integer")
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("source_ref must be a SourceRef")  # noqa: TRY003, TRY004
        await self._register(
            _Node(
                node_id=node_id,
                kind="source",
                payload={"source_ref": source_ref.model_dump(mode="json")},
                parents=(),
                policy="all",
                available=True,
                revision=revision,
                sequence=0,
            )
        )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        /,
        *,
        parents: Sequence[str],
        policy: LifecyclePolicy = "all",
    ) -> None:
        _require_node_id(node_id)
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("citation must be a MemoryCitation")  # noqa: TRY003, TRY004
        await self._register(
            _Node(
                node_id=node_id,
                kind="memory",
                payload={"citation": citation.model_dump(mode="json")},
                parents=_require_parents(node_id, parents, policy),
                policy=policy,
                available=False,
                revision=1,
                sequence=0,
            )
        )

    async def register_topic(
        self,
        node_id: str,
        artifact: ArtifactRef,
        /,
        *,
        parents: Sequence[str],
        policy: LifecyclePolicy = "all",
    ) -> None:
        _require_node_id(node_id)
        if not isinstance(artifact, ArtifactRef):
            raise SourceLifecycleError("artifact must be an ArtifactRef")  # noqa: TRY003, TRY004
        await self._register(
            _Node(
                node_id=node_id,
                kind="topic",
                payload={"artifact": artifact.model_dump(mode="json")},
                parents=_require_parents(node_id, parents, policy),
                policy=policy,
                available=False,
                revision=1,
                sequence=0,
            )
        )

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        """Apply one Source event and return every currently available node id."""

        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("event must be a SourceLifecycleEvent")  # noqa: TRY003, TRY004
        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            nodes = await self._load()
            node = nodes.get(event.node_id)
            if node is None or node.kind != "source":
                raise SourceLifecycleError(f"unknown Source node {event.node_id!r}")  # noqa: TRY003
            if event.revision != node.revision:
                raise SourceLifecycleError("event revision does not match the registered Source")  # noqa: TRY003
            if event.sequence < node.sequence:
                raise SourceLifecycleError("event sequence must not move backwards")  # noqa: TRY003
            if event.sequence == node.sequence:
                if event.available != node.available:
                    raise SourceLifecycleError("event sequence was reused with a different state")  # noqa: TRY003
                return _active_ids(nodes)
            previous = {name: item.available for name, item in nodes.items()}
            node.sequence = event.sequence
            node.available = event.available
            _propagate(nodes)
            await self._save(nodes)
        await self._sync_native(nodes, previous)
        return _active_ids(nodes)

    async def resolve(self, node_id: str, /, *, purpose: LifecyclePurpose = "generation") -> Any:
        """Return the original reference, or refuse generation from withdrawn evidence."""

        if purpose not in _PURPOSES:
            raise SourceLifecycleError(f"unknown resolve purpose {purpose!r}")  # noqa: TRY003
        nodes = await self._load()
        node = nodes.get(node_id)
        if node is None:
            raise SourceLifecycleError(f"unknown lifecycle node {node_id!r}")  # noqa: TRY003
        if purpose == "generation" and not node.available:
            raise SourceLifecycleError(f"node {node_id!r} is not available for generation")  # noqa: TRY003
        if node.kind == "source":
            return SourceRef.model_validate(node.payload["source_ref"])
        if node.kind == "memory":
            return MemoryCitation.model_validate(node.payload["citation"])
        return ArtifactRef.model_validate(node.payload["artifact"])

    async def hidden_topics(self) -> frozenset[str]:
        """Return Topic artifact ids this graph currently withholds."""

        nodes = await self._load()
        return frozenset(
            node.payload["artifact"]["artifact_id"]
            for node in nodes.values()
            if node.kind == "topic" and not node.available
        )

    async def _register(self, node: _Node) -> None:
        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            nodes = await self._load()
            existing = nodes.get(node.node_id)
            if existing is not None:
                if existing.binding != node.binding:
                    raise SourceLifecycleError(f"node {node.node_id!r} is already registered differently")  # noqa: TRY003
                return
            for parent in node.parents:
                if parent not in nodes:
                    raise SourceLifecycleError(f"unknown parent {parent!r}")  # noqa: TRY003
            before = {name: item.available for name, item in nodes.items()}
            if node.kind != "source":
                node.available = _derive(nodes, node)
            before[node.node_id] = not node.available
            nodes[node.node_id] = node
            _propagate(nodes)
            await self._save(nodes)
        await self._sync_native(nodes, before)

    async def _load(self) -> dict[str, _Node]:
        table = SOURCE_LIFECYCLE_TABLE
        async with self._runtime._lifecycle_database.transaction() as connection:
            rows = (
                await connection.execute(select(table).where(table.c.scope_id == self.scope_id))
            ).mappings()
            return {str(row["node_id"]): _decode(row) for row in rows}

    async def _save(self, nodes: dict[str, _Node]) -> None:
        table = SOURCE_LIFECYCLE_TABLE
        async with self._runtime._lifecycle_database.transaction() as connection:
            await connection.execute(delete(table).where(table.c.scope_id == self.scope_id))
            if nodes:
                await connection.execute(
                    table.insert(),
                    [{"scope_id": self.scope_id, **node.to_row()} for node in nodes.values()],
                )

    async def _sync_native(self, nodes: dict[str, _Node], previous: dict[str, bool]) -> None:
        """Reflect changed Memory availability in the native Memory artifact."""

        changed = [
            node
            for node in nodes.values()
            if node.kind == "memory" and previous.get(node.node_id) != node.available
        ]
        if not changed:
            return
        async with self._runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            current = await service.head(context.artifacts.memory_artifact_id)
            if current is None:
                return
            versions = {entry.entry_id: entry for entry in await service.entries(current)}
            withdraw = [
                versions[node.payload["citation"]["entry_id"]]
                for node in changed
                if not node.available and node.payload["citation"]["entry_id"] in versions
            ]
            restore = [
                versions[node.payload["citation"]["entry_id"]]
                for node in changed
                if node.available and node.payload["citation"]["entry_id"] in versions
            ]
            if not withdraw and not restore:
                return
            if withdraw:
                await service.forget(current, entries=withdraw, reason="source lifecycle")
                current = await service.head(context.artifacts.memory_artifact_id)
                versions = {entry.entry_id: entry for entry in await service.entries(current)}
                restore = [
                    versions[node.payload["citation"]["entry_id"]]
                    for node in changed
                    if node.available and node.payload["citation"]["entry_id"] in versions
                ]
            if restore:
                await service.reactivate(current, entries=restore, reason="source lifecycle")


class SourceLifecycleApplication:
    """Select the lifecycle service of one scope."""

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self._runtime, scope_id)


def _derive(nodes: dict[str, _Node], node: _Node) -> bool:
    support = [nodes[parent].available for parent in node.parents]
    return all(support) if node.policy == "all" else any(support)


def _propagate(nodes: dict[str, _Node]) -> None:
    """Recompute every derived node until the graph stops changing."""

    for _round in range(len(nodes) + 1):
        changed = False
        for node in nodes.values():
            if node.kind == "source":
                continue
            derived = _derive(nodes, node)
            if derived != node.available:
                node.available = derived
                changed = True
        if not changed:
            return


def _active_ids(nodes: dict[str, _Node]) -> list[str]:
    return sorted(name for name, node in nodes.items() if node.available)


def _require_node_id(node_id: object) -> str:
    if not isinstance(node_id, str) or not node_id.strip():
        raise SourceLifecycleError("node_id must be a non-empty string")  # noqa: TRY003, TRY004
    return node_id


def _require_parents(node_id: str, parents: Iterable[str], policy: str) -> tuple[str, ...]:
    if policy not in _POLICIES:
        raise SourceLifecycleError(f"unknown policy {policy!r}")  # noqa: TRY003
    ordered = tuple(parents)
    if not ordered:
        raise SourceLifecycleError("derived nodes need at least one parent")  # noqa: TRY003
    if len(set(ordered)) != len(ordered):
        raise SourceLifecycleError("parents must not repeat")  # noqa: TRY003
    if node_id in ordered:
        raise SourceLifecycleError("a node cannot depend on itself")  # noqa: TRY003
    return ordered


def _text(value: Any) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8")
    return str(value)


def _decode(row: Any) -> _Node:
    return _Node(
        node_id=str(row["node_id"]),
        kind=str(row["kind"]),
        payload=json.loads(_text(row["payload"])),
        parents=tuple(json.loads(_text(row["parents"]))),
        policy=str(row["policy"]),
        available=bool(row["available"]),
        revision=int(row["revision"]),
        sequence=int(row["sequence"]),
    )


__all__ = [
    "ScopedSourceLifecycle",
    "SourceLifecycleApplication",
    "SourceLifecycleError",
    "SourceLifecycleEvent",
]
