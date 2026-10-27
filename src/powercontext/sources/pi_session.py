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

"""Capture the raw visible events of one selected Pi session branch.

A Pi session file is a JSONL tree: every record has a unique ``id`` and an
optional ``parentId``. The selected leaf and its ancestors form the branch that
the user actually saw, so this module turns exactly that path into one
``CAPTURED`` Source per visible event. Control records keep the graph connected
but produce no text evidence, and compaction summaries never remove the raw
events that precede them.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel
from urllib.parse import quote

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
)
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PI_EVENT_SOURCE_NAME = "pi-event"
PI_EVENT_SOURCE_VERSION = "1"
PI_CONNECTOR_NAME = "pi-session-history"
PI_CONNECTOR_VERSION = "1"
PI_SESSION_VERSION = 3

# Checkpoint layout: {"version": 1, "events": {event_id: "<sha256 of record>"}}
PI_CHECKPOINT_VERSION = 1

_UTF8_BOM_AWARE = "utf-8-sig"
_COMPACT_SEPARATORS = (",", ":")


class PiSessionInput(BaseModel):
    """One raw Pi session archive plus the branch selected for capture."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event on the selected branch, in root-to-leaf order."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, Any]


class PiEventSource(Source):
    """A captured Pi event that keeps its raw record and rendered text."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, Any]


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Build a stable identity that depends only on the session and event ids."""

    readable = "pi:%s:%s" % (quote(session_id, safe=""), quote(event_id, safe=""))
    if len(readable) <= MAX_SOURCE_ID_LENGTH:
        return readable
    digest = hashlib.sha256(f"{session_id}\x00{event_id}".encode()).hexdigest()
    return "pi:%s" % digest


def _decode_archive(content: object) -> str:
    if not isinstance(content, (bytes, bytearray, memoryview)):
        raise ValueError("Pi session content must be bytes")  # noqa: TRY003, TRY004
    try:
        return bytes(content).decode(_UTF8_BOM_AWARE, errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"Pi session must be strict UTF-8: {error}") from error  # noqa: TRY003


def _read_records(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"malformed Pi session JSON: {error}") from error  # noqa: TRY003
        if not isinstance(value, dict):
            raise ValueError("every Pi session line must be a JSON object")  # noqa: TRY003, TRY004
        records.append(cast(dict[str, Any], value))
    if not records:
        raise ValueError("Pi session archive must not be empty")  # noqa: TRY003
    return records


def _validate_header(header: Mapping[str, Any]) -> str:
    if header.get("type") != "session":
        raise ValueError("Pi session archive must start with a session record")  # noqa: TRY003
    if header.get("version") != PI_SESSION_VERSION:
        raise ValueError(f"Pi session version must be {PI_SESSION_VERSION}")  # noqa: TRY003
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("Pi session id must be a non-empty string")  # noqa: TRY003, TRY004
    return session_id


def _validate_graph(records: Sequence[Mapping[str, Any]]) -> dict[str, str | None]:
    """Return ``id -> parentId`` for a well-formed parent-linked tree."""

    parents: dict[str, str | None] = {}
    for record in records:
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("every Pi record needs a non-empty string id")  # noqa: TRY003, TRY004
        if event_id in parents:
            raise ValueError(f"duplicate Pi record id {event_id!r}")  # noqa: TRY003
        parent_id = record.get("parentId")
        if parent_id is not None:
            if not isinstance(parent_id, str) or not parent_id.strip():
                raise ValueError("Pi parentId must be a non-empty string or null")  # noqa: TRY003, TRY004
            if parent_id == event_id:
                raise ValueError(f"Pi record {event_id!r} cannot be its own parent")  # noqa: TRY003
            if parent_id not in parents:
                raise ValueError(f"Pi record {event_id!r} references unknown parent {parent_id!r}")  # noqa: TRY003
        parents[event_id] = parent_id
    return parents


def _compact_json(value: object) -> str:
    """Serialize tool arguments with sorted keys, Unicode and no padding."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=_COMPACT_SEPARATORS)


def _text_blocks(content: object) -> list[str]:
    if isinstance(content, str):
        return [content] if content else []
    if not isinstance(content, list):
        return []
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        elif kind == "toolCall":
            name = block.get("name")
            call_id = block.get("id")
            arguments = block.get("arguments")
            parts.append(
                "[tool_call %s %s] %s"
                % (
                    name if isinstance(name, str) else "",
                    call_id if isinstance(call_id, str) else "",
                    _compact_json(arguments),
                )
            )
    return parts


def _message_event(record: Mapping[str, Any]) -> tuple[str, str] | None:
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    if not isinstance(role, str) or not role.strip():
        return None
    if role == "toolResult":
        tool_name = message.get("toolName")
        call_id = message.get("toolCallId")
        body = "\n".join(_text_blocks(message.get("content")))
        return role, "[tool_result %s %s] %s" % (
            tool_name if isinstance(tool_name, str) else "",
            call_id if isinstance(call_id, str) else "",
            body,
        )
    if role == "bashExecution":
        command = message.get("command")
        output = message.get("output")
        head = "$ %s" % (command if isinstance(command, str) else "")
        if isinstance(output, str) and output:
            return role, "%s\n%s" % (head, output)
        return role, head
    body = "\n".join(_text_blocks(message.get("content")))
    return role, body


def _summary_event(record: Mapping[str, Any], event_type: str) -> tuple[str, str] | None:
    summary = record.get("summary")
    return event_type, summary if isinstance(summary, str) else ""


def _render_event(record: Mapping[str, Any]) -> tuple[str, str] | None:
    """Return ``(role, content)`` for one visible record, or ``None``."""

    event_type = record.get("type")
    if not isinstance(event_type, str):
        return None
    if event_type == "message":
        rendered = _message_event(record)
    elif event_type in {"compaction", "branch_summary"}:
        rendered = _summary_event(record, event_type)
    elif event_type == "custom_message":
        rendered = ("custom", "\n".join(_text_blocks(record.get("content"))))
    else:
        # Control records (model_change, thinking_level_change, ...) stay in the
        # graph but never become text evidence.
        return None
    if rendered is None:
        return None
    role, body = rendered
    if not body:
        return None
    return role, body


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Parse the selected branch into visible events ordered root to leaf."""

    text = _decode_archive(value.content)
    records = _read_records(text)
    session_id = _validate_header(records[0])
    body = records[1:]
    parents = _validate_graph(body)

    leaf_id = value.leaf_id
    if leaf_id is None:
        if not body:
            return ()
        leaf_id = cast(str, body[-1]["id"])
    elif leaf_id not in parents:
        raise ValueError(f"Pi leaf {leaf_id!r} is not present in the session")  # noqa: TRY003

    by_id = {cast(str, record["id"]): record for record in body}
    chain: list[str] = []
    cursor: str | None = leaf_id
    while cursor is not None:
        chain.append(cursor)
        cursor = parents[cursor]
    chain.reverse()

    events: list[PiEventInput] = []
    for event_id in chain:
        record = by_id[event_id]
        rendered = _render_event(record)
        if rendered is None:
            continue
        role, content = rendered
        events.append(
            PiEventInput(
                session_id=session_id,
                event_id=event_id,
                parent_id=parents[event_id],
                event_type=cast(str, record["type"]),
                role=role,
                content=f"{role}: {content}",
                record=dict(record),
            )
        )
    return tuple(events)


class PiEventSourceAdapter:
    """Resolve and read one captured Pi event."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        if not value.session_id.strip() or not value.event_id.strip():
            raise ValueError("Pi event identity parts must be non-empty")  # noqa: TRY003
        identity = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=identity,
            materialization=SourceMaterialization.CAPTURED,
            source_id=identity,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=dict(value.record),
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=dict(source.record),
        )


class PiEventTextEvidenceProjection:
    """Expose the complete event text as standard text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_ADAPTER = PiEventSourceAdapter()
PI_EVENT_SOURCE_DEFINITION: SourceDefinition[PiEventInput, PiEventSource, PiEventInput] = cast(
    SourceDefinition[PiEventInput, PiEventSource, PiEventInput],
    AdapterSourceDefinition(
        PI_EVENT_SOURCE_ADAPTER,
        version=PI_EVENT_SOURCE_VERSION,
        projections=(PiEventTextEvidenceProjection(),),
    ),
)


def event_digest(record: Mapping[str, Any]) -> str:
    """Fingerprint one raw record so later rewrites can be detected."""

    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=_COMPACT_SEPARATORS)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _load_checkpoint(checkpoint: object) -> dict[str, str]:
    if checkpoint is None:
        return {}
    if not isinstance(checkpoint, dict):
        raise ValueError("Pi checkpoint must be a JSON object")  # noqa: TRY003, TRY004
    version = checkpoint.get("version")
    events = checkpoint.get("events")
    if version != PI_CHECKPOINT_VERSION or not isinstance(events, dict):
        raise ValueError("Pi checkpoint is not a supported version")  # noqa: TRY003
    known: dict[str, str] = {}
    for event_id, digest in cast(dict[str, Any], events).items():
        if not isinstance(event_id, str) or not isinstance(digest, str):
            raise ValueError("Pi checkpoint entries must map ids to digests")  # noqa: TRY003
        known[event_id] = digest
    return known


class PiSessionConnector:
    """Import only the visible events of the selected branch that are new."""

    name = PI_CONNECTOR_NAME
    version = PI_CONNECTOR_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        raw = self._path.read_bytes()
        events = parse_pi_session(PiSessionInput(content=raw, leaf_id=self._leaf_id))
        known = _load_checkpoint(session.checkpoint)

        digests: dict[str, str] = dict(known)
        for event in events:
            digest = event_digest(event.record)
            previous = known.get(event.event_id)
            if previous is not None:
                if previous != digest:
                    raise ValueError(  # noqa: TRY003
                        f"Pi event {event.event_id!r} was rewritten after it was captured"
                    )
                continue
            outcome = await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)
            if outcome.status is not ConnectorSubmissionStatus.ACCEPTED:
                return ConnectorRunCompletion(status=ConnectorRunStatus.INCOMPLETE, checkpoint=session.checkpoint)
            digests[event.event_id] = digest

        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"version": PI_CHECKPOINT_VERSION, "events": digests},
        )


def iter_event_ids(events: Iterable[PiEventInput]) -> tuple[str, ...]:
    """Return the captured event ids in branch order."""

    return tuple(event.event_id for event in events)


__all__ = [
    "PI_CONNECTOR_NAME",
    "PI_CONNECTOR_VERSION",
    "PI_EVENT_SOURCE_ADAPTER",
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_EVENT_SOURCE_VERSION",
    "PiEventInput",
    "PiEventSource",
    "PiEventSourceAdapter",
    "PiEventTextEvidenceProjection",
    "PiSessionConnector",
    "PiSessionInput",
    "event_digest",
    "iter_event_ids",
    "parse_pi_session",
    "pi_event_source_id",
]
