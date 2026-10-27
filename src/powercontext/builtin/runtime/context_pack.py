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

"""Reuse an already delivered context by sending only its new suffix.

A Context Pack is the complete prepared context split into UTF-8-safe chunks
plus a digest that identifies the exact chunk sequence. A client that already
holds a pack receives a delta: how many leading chunks of the base pack still
apply, and the chunks that must be appended. The receiver rebuilds the target
pack from the base pack and the delta alone.

The wire form is deliberately fixed: canonical JSON (sorted keys, no padding),
Base64 chunk strings and a SHA-256 digest over the chunk sequence.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import Any

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
DEFAULT_CHUNK_BYTES = 512
DIGEST_PREFIX = "sha256:"

_PACK_FIELDS = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})
_JSON_SEPARATORS = (",", ":")


class ContextPackError(ValueError):
    """Raised when a pack or delta violates the wire contract."""


@dataclass(frozen=True, slots=True)
class ContextPack:
    """One complete context split into ordered UTF-8-safe chunks."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...], /) -> ContextPack:
        """Build a complete pack from already chunked content."""

        chunks = _require_chunks(items)
        digest = _pack_digest(chunks)
        size = len(_encode_pack(chunks, digest, 0))
        while True:
            payload = _encode_pack(chunks, digest, size)
            if len(payload) == size:
                return cls(items=chunks, digest=digest, byte_count=size)
            size = len(payload)

    @classmethod
    def from_content(cls, content: str, /, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> ContextPack:
        """Split text into deterministic chunks that each fit the byte bound."""

        if not isinstance(content, str):
            raise ContextPackError("content must be text")  # noqa: TRY003, TRY004
        return cls.build(_chunk_text(content, chunk_bytes))

    @classmethod
    def from_bytes(cls, payload: bytes | bytearray | memoryview, /) -> ContextPack:
        """Decode and fully validate one complete pack."""

        body = _load_json(payload, "context pack")
        _require_fields(body, _PACK_FIELDS, "context pack")
        _require_schema(body, PACK_SCHEMA, "context pack")
        chunks = _decode_chunks(body["items"], "context pack items")
        digest = _require_digest(body["digest"], "context pack digest")
        if digest != _pack_digest(chunks):
            raise ContextPackError("context pack digest does not match its items")
        size = body["byte_count"]
        if type(size) is not int or size < 0:
            raise ContextPackError("context pack byte_count must be a non-negative integer")
        pack = cls(items=chunks, digest=digest, byte_count=size)
        if len(pack.to_bytes()) != size:
            raise ContextPackError("context pack byte_count does not match its encoding")
        return pack

    def to_bytes(self) -> bytes:
        """Return the canonical wire encoding of this pack."""

        return _encode_pack(self.items, self.digest, self.byte_count)


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """The suffix a receiver must append to a known base pack."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack, /) -> ContextPackDelta:
        """Keep the longest usable common prefix and carry the target suffix."""

        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("delta endpoints must be context packs")  # noqa: TRY003, TRY004
        prefix = 0
        shared = min(len(base.items), len(target.items))
        while prefix < shared and base.items[prefix] == target.items[prefix]:
            prefix += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix,
            appended=tuple(target.items[prefix:]),
        )

    @classmethod
    def from_bytes(cls, payload: bytes | bytearray | memoryview, /) -> ContextPackDelta:
        """Decode and fully validate one delta."""

        body = _load_json(payload, "context pack delta")
        _require_fields(body, _DELTA_FIELDS, "context pack delta")
        _require_schema(body, DELTA_SCHEMA, "context pack delta")
        prefix = body["prefix_items"]
        if type(prefix) is not int or prefix < 0:
            raise ContextPackError("delta prefix_items must be a non-negative integer")
        return cls(
            base_digest=_require_digest(body["base_digest"], "delta base_digest"),
            target_digest=_require_digest(body["target_digest"], "delta target_digest"),
            prefix_items=prefix,
            appended=_decode_chunks(body["appended"], "delta appended"),
        )

    @property
    def byte_count(self) -> int:
        """Return the exact number of bytes this delta occupies on the wire."""

        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Return the canonical wire encoding of this delta."""

        return _dump({
            "schema": DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": [_encode_chunk(item) for item in self.appended],
        })


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta, /) -> ContextPack:
    """Rebuild the target pack from a base pack and one delta."""

    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("delta application needs a pack and a delta")  # noqa: TRY003, TRY004
    if base.digest != delta.base_digest:
        raise ContextPackError("delta does not apply to this base pack")
    if delta.prefix_items < 0 or delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix_items is outside the base pack")
    target = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if target.digest != delta.target_digest:
        raise ContextPackError("rebuilt pack does not match the delta target digest")
    return target


def _chunk_text(content: str, chunk_bytes: int) -> tuple[bytes, ...]:
    if not isinstance(chunk_bytes, int) or isinstance(chunk_bytes, bool) or chunk_bytes < 1:
        raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003, TRY004
    chunks: list[bytes] = []
    current = bytearray()
    for character in content:
        encoded = character.encode("utf-8")
        if len(encoded) > chunk_bytes:
            raise ContextPackError("a single character does not fit the chunk limit")
        if len(current) + len(encoded) > chunk_bytes:
            chunks.append(bytes(current))
            current = bytearray()
        current.extend(encoded)
    if current:
        chunks.append(bytes(current))
    return tuple(chunks)


def _require_chunks(items: object) -> tuple[bytes, ...]:
    if not isinstance(items, tuple):
        raise ContextPackError("pack items must be a tuple of bytes")  # noqa: TRY003, TRY004
    for item in items:
        if not isinstance(item, (bytes, bytearray, memoryview)):
            raise ContextPackError("pack items must be bytes")  # noqa: TRY003, TRY004
    return tuple(bytes(item) for item in items)


def _pack_digest(items: tuple[bytes, ...]) -> str:
    unsigned = {"schema": PACK_SCHEMA, "items": [_encode_chunk(item) for item in items]}
    return DIGEST_PREFIX + hashlib.sha256(_dump(unsigned)).hexdigest()


def _encode_pack(items: tuple[bytes, ...], digest: str, byte_count: int) -> bytes:
    return _dump({
        "schema": PACK_SCHEMA,
        "items": [_encode_chunk(item) for item in items],
        "digest": digest,
        "byte_count": byte_count,
    })


def _encode_chunk(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode_chunks(value: object, label: str) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError(f"{label} must be a list of Base64 strings")  # noqa: TRY003
    chunks: list[bytes] = []
    for entry in value:
        if not isinstance(entry, str):
            raise ContextPackError(f"{label} must be a list of Base64 strings")  # noqa: TRY003
        try:
            chunks.append(base64.b64decode(entry.encode("ascii"), validate=True))
        except (binascii.Error, UnicodeEncodeError, ValueError) as error:
            raise ContextPackError(f"{label} contains invalid Base64") from error  # noqa: TRY003
    return tuple(chunks)


def _dump(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=_JSON_SEPARATORS).encode("utf-8")


def _load_json(payload: object, label: str) -> dict[str, Any]:
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise ContextPackError(f"{label} must be bytes")  # noqa: TRY003, TRY004
    try:
        body = json.loads(bytes(payload))
    except (ValueError, UnicodeDecodeError) as error:
        raise ContextPackError(f"{label} is not valid JSON") from error  # noqa: TRY003
    if not isinstance(body, dict):
        raise ContextPackError(f"{label} must be a JSON object")  # noqa: TRY003, TRY004
    return body


def _require_fields(body: dict[str, Any], expected: frozenset[str], label: str) -> None:
    if set(body) != expected:
        raise ContextPackError(f"{label} must contain exactly {sorted(expected)}")  # noqa: TRY003


def _require_schema(body: dict[str, Any], expected: str, label: str) -> None:
    if body["schema"] != expected:
        raise ContextPackError(f"{label} schema must be {expected}")  # noqa: TRY003


def _require_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.startswith(DIGEST_PREFIX):
        raise ContextPackError(f"{label} must be a sha256 digest")  # noqa: TRY003, TRY004
    hex_part = value[len(DIGEST_PREFIX) :]
    if len(hex_part) != 64 or any(character not in "0123456789abcdef" for character in hex_part):
        raise ContextPackError(f"{label} must be a lowercase sha256 digest")  # noqa: TRY003
    return value


__all__ = [
    "DEFAULT_CHUNK_BYTES",
    "DELTA_SCHEMA",
    "PACK_SCHEMA",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
