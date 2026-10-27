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

"""Row-level CSV Sources with stable primary-key identity and text evidence.

Each physical CSV record becomes one ``CAPTURED`` Source so that a retrieval
citation can be traced back to the complete original row. Row identity is
derived only from the document id and the primary-key value, which keeps the
identity stable across re-ordering and edits to non-key columns.
"""

from __future__ import annotations

import csv
import io
import json
from typing import cast
from urllib.parse import quote

from pydantic import BaseModel, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

CSV_ROW_SOURCE_NAME = "csv-row"
CSV_ROW_SOURCE_VERSION = "1"
CSV_ROW_IDENTITY_PREFIX = "csv"

# Fixed ``csv.reader`` semantics required by the contract.
_CSV_DELIMITER = ","
_CSV_QUOTECHAR = '"'
_CSV_DOUBLED_QUOTECHAR = True
_CSV_STRICT = True
_UTF8_BOM_AWARE = "utf-8-sig"


class CSVDocumentInput(BaseModel):
    """One fully offline CSV document captured by a caller-stable identity."""

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One validated CSV record expressed in the original header order."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """A captured CSV row that keeps its identity fields, columns and values."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_identity(self) -> CSVRowSource:
        if self.name != self.source_id:
            raise ValueError("CSV row Source name must equal its source_id")  # noqa: TRY003
        if len(self.columns) != len(self.values):
            raise ValueError("CSV row Source columns and values must have the same length")  # noqa: TRY003
        return self


def _require_document_id(document_id: object) -> str:
    if not isinstance(document_id, str) or not document_id:
        raise ValueError("document_id must be a non-empty string")  # noqa: TRY003
    if document_id != document_id.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")  # noqa: TRY003
    return document_id


def _require_primary_key(primary_key: object) -> str:
    if not isinstance(primary_key, str) or not primary_key:
        raise ValueError("primary_key must be a non-empty string")  # noqa: TRY003
    return primary_key


def _require_primary_key_value(primary_key_value: object) -> str:
    if not isinstance(primary_key_value, str):
        raise ValueError("primary_key_value must be a string")  # noqa: TRY003, TRY004
    if not primary_key_value.strip():
        raise ValueError("primary_key_value must not be empty or whitespace only")  # noqa: TRY003
    return primary_key_value


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build the stable identity ``csv:{document}:{primary_key_value}``."""

    document = _require_document_id(document_id)
    key_value = _require_primary_key_value(primary_key_value)
    identity = f"{CSV_ROW_IDENTITY_PREFIX}:{quote(document, safe='')}:{quote(key_value, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(  # noqa: TRY003
            f"CSV row identity must not exceed {MAX_SOURCE_ID_LENGTH} characters"
        )
    return identity


def _decode_document(content: object) -> str:
    if not isinstance(content, (bytes, bytearray, memoryview)):
        raise ValueError("CSV document content must be bytes")  # noqa: TRY003, TRY004
    try:
        # ``utf-8-sig`` decodes strict UTF-8 and drops a single leading BOM.
        return bytes(content).decode(_UTF8_BOM_AWARE, errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(f"CSV document must be strict UTF-8: {error}") from error  # noqa: TRY003


def _read_records(text: str) -> list[list[str]]:
    stream = io.StringIO(text, newline="")
    reader = csv.reader(
        stream,
        delimiter=_CSV_DELIMITER,
        quotechar=_CSV_QUOTECHAR,
        doublequote=_CSV_DOUBLED_QUOTECHAR,
        strict=_CSV_STRICT,
    )
    records: list[list[str]] = []
    try:
        for record in reader:
            records.append(record)
    except csv.Error as error:
        raise ValueError(f"malformed CSV document: {error}") from error  # noqa: TRY003
    return records


def _validate_header(header: list[str], primary_key: str) -> int:
    if not header:
        raise ValueError("CSV document must start with a non-empty header")  # noqa: TRY003
    for field in header:
        if not field or not field.strip():
            raise ValueError("CSV header fields must not be empty or whitespace only")  # noqa: TRY003
    if len(set(header)) != len(header):
        raise ValueError("CSV header fields must be unique")  # noqa: TRY003
    try:
        return header.index(primary_key)
    except ValueError:
        raise ValueError(  # noqa: TRY003
            f"primary_key {primary_key!r} does not match any header field exactly"
        ) from None


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse a whole CSV document into row inputs or fail atomically.

    Every record is parsed and validated before anything is returned, so an
    invalid document never yields a partial row set.
    """

    document_id = _require_document_id(value.document_id)
    primary_key = _require_primary_key(value.primary_key)
    text = _decode_document(value.content)

    records = _read_records(text)
    if not records:
        raise ValueError("CSV document must not be empty")  # noqa: TRY003

    header = records[0]
    primary_index = _validate_header(header, primary_key)
    width = len(header)
    columns = tuple(header)

    rows: list[CSVRowInput] = []
    seen_keys: set[str] = set()
    for record in records[1:]:
        if not record:
            # Completely empty physical records carry no data.
            continue
        if len(record) != width:
            raise ValueError(  # noqa: TRY003
                f"CSV record has {len(record)} fields but the header declares {width}"
            )
        primary_key_value = record[primary_index]
        if not primary_key_value.strip():
            raise ValueError("CSV primary key values must not be empty or whitespace only")  # noqa: TRY003
        if primary_key_value in seen_keys:
            raise ValueError(f"duplicate CSV primary key value {primary_key_value!r}")  # noqa: TRY003
        seen_keys.add(primary_key_value)
        rows.append(
            CSVRowInput(
                document_id=document_id,
                primary_key=primary_key,
                primary_key_value=primary_key_value,
                columns=columns,
                values=tuple(record),
            )
        )
    return tuple(rows)


def _validate_row_input(value: CSVRowInput) -> None:
    _require_document_id(value.document_id)
    primary_key = _require_primary_key(value.primary_key)
    _require_primary_key_value(value.primary_key_value)
    columns = value.columns
    if not columns:
        raise ValueError("CSV row columns must not be empty")  # noqa: TRY003
    for column in columns:
        if not isinstance(column, str) or not column or not column.strip():
            raise ValueError("CSV row columns must not be empty or whitespace only")  # noqa: TRY003
    if len(set(columns)) != len(columns):
        raise ValueError("CSV row columns must be unique")  # noqa: TRY003
    if len(value.values) != len(columns):
        raise ValueError("CSV row values must align with its columns")  # noqa: TRY003
    if primary_key not in columns:
        raise ValueError(  # noqa: TRY003
            f"primary_key {primary_key!r} does not match any row column exactly"
        )


def row_mapping(columns: tuple[str, ...], values: tuple[str, ...]) -> dict[str, str]:
    """Zip columns and values while preserving the original header order."""

    return dict(zip(columns, values, strict=True))


class CSVRowSourceAdapter:
    """Resolve and read the row-level CSV Source."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        _validate_row_input(value)
        identity = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=identity,
            materialization=SourceMaterialization.CAPTURED,
            source_id=identity,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    """Expose one CSV row as canonical text evidence without value rewriting."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = row_mapping(source.columns, source.values)
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


CSV_ROW_SOURCE_ADAPTER = CSVRowSourceAdapter()
CSV_ROW_SOURCE_DEFINITION: SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = cast(
    SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput],
    AdapterSourceDefinition(
        CSV_ROW_SOURCE_ADAPTER,
        version=CSV_ROW_SOURCE_VERSION,
        projections=(CSVRowTextEvidenceProjection(),),
    ),
)

__all__ = [
    "CSV_ROW_SOURCE_ADAPTER",
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_NAME",
    "CSV_ROW_SOURCE_VERSION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "CSVRowSourceAdapter",
    "CSVRowTextEvidenceProjection",
    "csv_row_source_id",
    "parse_csv_document",
    "row_mapping",
]
