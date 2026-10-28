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

"""Page-level PDF Sources and offline evidence-page retrieval.

One physical PDF page becomes one ``CAPTURED`` Source so a retrieval citation
can be traced back to the original file revision and physical page number. The
parser keeps every page, including blank ones, and never returns a partial
document: an unreadable file fails as a whole.

Retrieval stays inside the caller's Memory: it only issues public searches and
turns the hits it can re-read into exact citations. Query rewriting and local
re-ranking are offline and deterministic.
"""

from __future__ import annotations

import hashlib
import io
import math
import re
from collections.abc import Iterable, Sequence
from typing import Any, cast
from urllib.parse import quote, unquote

from pydantic import BaseModel
from pypdf import PdfReader

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_SOURCE_VERSION = "1"
PDF_PAGE_IDENTITY_PREFIX = "pdf"

_TERM_PATTERN = re.compile(r"[a-z]+|\d+")

# Function words never discriminate a financial page.
_FUNCTION_WORDS = frozenset({
    "a", "an", "the", "and", "or", "for", "of", "to", "in", "on", "at", "by", "from", "with", "as",
    "is", "are", "was", "were", "be", "been", "being", "what", "which", "who", "whom", "whose",
    "how", "why", "when", "where", "do", "does", "did", "can", "could", "should", "would", "will",
    "shall", "may", "might", "must", "i", "we", "you", "it", "its", "our", "your", "this", "that",
    "these", "those", "there", "their", "they", "he", "she", "his", "her", "them", "than", "then",
    "so", "such", "any", "all", "both", "each", "other", "some", "more", "most", "much", "many",
    "if", "into", "over", "under", "about", "after", "before", "between", "during", "also", "not",
    "no", "nor", "but", "up", "down", "out", "off", "again", "further", "once", "only", "own",
    "same", "too", "very", "just", "now", "please", "per", "via", "within", "without", "upon",
    "has", "have", "had", "having", "fy", "whether", "been", "being", "get", "got",
})

# Instruction boilerplate that would otherwise dominate a long FinanceBench question.
_BOILERPLATE_WORDS = frozenset({
    "question", "answer", "answers", "respond", "response", "assume", "assuming", "assumption",
    "perspective", "analyst", "investment", "investor", "details", "detail", "shown", "show",
    "define", "defined", "definition", "calculate", "calculated", "calculation", "compute",
    "state", "stated", "statement", "report", "reported", "reports", "reporting", "disclose",
    "disclosed", "explicitly", "outlined", "otherwise", "unknown", "absent", "missing",
    "use", "using", "used", "given", "provided", "supplied", "available", "context", "evidence",
    "information", "data", "figure", "figures", "value", "values", "number", "numbers",
})

# Modifiers that read as evidence but are boilerplate in a filing.
_GENERIC_MODIFIERS = frozenset({
    "material", "materially", "important", "significant", "significantly", "substantial",
    "substantially", "certain", "various", "primarily", "principally", "directly", "generally",
    "currently", "previously", "approximately", "related", "relating", "resulting", "expected",
    "continue", "continued", "continuing", "include", "includes", "including", "overall",
    "general", "key", "major", "recent", "recently", "existing", "current", "future",
    "potential", "potentially", "possible", "likely", "necessary", "appropriate", "additional",
    "ongoing", "overall", "certain", "new", "main", "large", "small", "high", "low",
})

# Question forms that carry no lexical evidence.
_GENERIC_QUESTION_WORDS = frozenset({
    "quantity", "amount", "total", "percentage", "percent", "ratio", "rate", "change",
    "increase", "decrease", "difference", "average", "geography", "geographies", "region",
    "regions", "segment", "segments", "product", "products", "service", "services",
})

class PDFDocumentInput(BaseModel):
    """One complete original PDF addressed by a caller-stable document id."""

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """One physical page of a PDF revision."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """A captured PDF page that keeps document identity and physical page number."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def _require_document_id(document_id: object) -> str:
    if not isinstance(document_id, str):
        raise ValueError("document_id must be a string")  # noqa: TRY003, TRY004
    if not document_id or not document_id.strip():
        raise ValueError("document_id must not be empty or whitespace only")  # noqa: TRY003
    return document_id


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Build the stable identity of one page inside one document revision."""

    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
        raise ValueError("page_number must be a positive integer")  # noqa: TRY003
    encoded = quote(document_id, safe="")
    identity = f"{PDF_PAGE_IDENTITY_PREFIX}:{page_number}:{encoded}:{document_sha256}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        # Very long ids keep a marker instead of the readable document id.
        encoded = "~" + hashlib.sha256(document_id.encode("utf-8")).hexdigest()[:32]
        identity = f"{PDF_PAGE_IDENTITY_PREFIX}:{page_number}:{encoded}:{document_sha256}"
    return identity


def parse_page_source_id(identity: str) -> tuple[str | None, int] | None:
    """Read back ``(document_id, page_number)`` from a page Source name."""

    parts = identity.split(":")
    if len(parts) != 4 or parts[0] != PDF_PAGE_IDENTITY_PREFIX:
        return None
    try:
        page_number = int(parts[1])
    except ValueError:
        return None
    document_id = unquote(parts[2])
    if document_id.startswith("~"):
        return None
    return document_id, page_number


def _open_reader(raw: bytes) -> PdfReader:
    try:
        reader = PdfReader(io.BytesIO(raw), strict=False)
    except Exception as error:  # noqa: BLE001 - pypdf raises several unrelated types
        raise ValueError(f"unreadable PDF: {error}") from error
    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception as error:  # noqa: BLE001
            raise ValueError(f"encrypted PDF cannot be opened without a password: {error}") from error
        if not unlocked:
            raise ValueError("PDF requires a user password")  # noqa: TRY003
    return reader


def _extract_pages(reader: PdfReader) -> list[str]:
    pages: list[str] = []
    try:
        total = len(reader.pages)
    except Exception as error:  # noqa: BLE001
        raise ValueError(f"PDF page tree is unreadable: {error}") from error
    for index in range(total):
        try:
            extracted = reader.pages[index].extract_text()
        except Exception as error:  # noqa: BLE001
            raise ValueError(f"PDF page {index + 1} cannot be parsed: {error}") from error
        pages.append(extracted if isinstance(extracted, str) else "")
    return pages


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Parse every physical page of one PDF or fail without partial output."""

    document_id = _require_document_id(value.document_id)
    content = value.content
    if not isinstance(content, (bytes, bytearray, memoryview)):
        raise ValueError("PDF content must be bytes")  # noqa: TRY003, TRY004
    raw = bytes(content)
    if not raw:
        raise ValueError("PDF content must not be empty")  # noqa: TRY003

    digest = hashlib.sha256(raw).hexdigest()
    texts = _extract_pages(_open_reader(raw))
    if not texts:
        raise ValueError("PDF must contain at least one page")  # noqa: TRY003
    return tuple(
        PDFPageInput(
            document_id=document_id,
            document_sha256=digest,
            page_number=index,
            text=text,
        )
        for index, text in enumerate(texts, start=1)
    )


class PDFPageSourceAdapter:
    """Resolve and read one captured PDF page."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        document_id = _require_document_id(value.document_id)
        if not isinstance(value.page_number, int) or value.page_number < 1:
            raise ValueError("page_number must be a positive integer")  # noqa: TRY003
        if len(value.document_sha256) != 64:
            raise ValueError("document_sha256 must be a lowercase SHA-256 digest")  # noqa: TRY003
        identity = pdf_page_source_id(document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=identity,
            materialization=SourceMaterialization.CAPTURED,
            source_id=identity,
            document_id=document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose one page as ``DOCUMENT_ID`` newline ``PAGE_TEXT`` text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_ADAPTER = PDFPageSourceAdapter()
PDF_PAGE_SOURCE_DEFINITION: SourceDefinition[PDFPageInput, PDFPageSource, PDFPageInput] = cast(
    SourceDefinition[PDFPageInput, PDFPageSource, PDFPageInput],
    AdapterSourceDefinition(
        PDF_PAGE_SOURCE_ADAPTER,
        version=PDF_PAGE_SOURCE_VERSION,
        projections=(PDFPageTextEvidenceProjection(),),
    ),
)


# ---------------------------------------------------------------------------
# Offline question analysis and retrieval
# ---------------------------------------------------------------------------


def question_terms(question: str) -> tuple[str, ...]:
    """Return the ordered distinct evidence terms of one question."""

    lowered = question.casefold()
    terms: list[str] = []
    seen: set[str] = set()
    for term in _TERM_PATTERN.findall(lowered):
        if len(term) < 2 and not term.isdigit():
            continue
        if term in _FUNCTION_WORDS or term in _BOILERPLATE_WORDS or term in _GENERIC_MODIFIERS:
            continue
        if term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return tuple(terms)


# A small, domain-general lexicon. A filing rarely repeats the question's
# wording, so one evidence word maps onto the words the document actually uses.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "battle": ("litigation", "proceedings", "lawsuit"),
    "battles": ("litigation", "proceedings", "lawsuit"),
    "lawsuit": ("litigation", "proceedings"),
    "lawsuits": ("litigation", "proceedings"),
    "litigation": ("proceedings", "lawsuit"),
    "penalty": ("penalties", "fine", "settlement"),
    "penalties": ("penalty", "fines", "settlements"),
    "fine": ("penalty", "settlement"),
    "fines": ("penalties", "settlements"),
    "geography": ("geographic", "region", "segment"),
    "geographies": ("geographic", "region", "segment"),
    "country": ("countries", "region", "geographic"),
    "countries": ("country", "region", "geographic"),
    "employee": ("employees", "personnel", "workforce"),
    "employees": ("personnel", "workforce"),
    "headcount": ("employees", "personnel"),
    "layoff": ("severance", "restructuring"),
    "layoffs": ("severance", "restructuring"),
    "restructuring": ("severance", "impairment"),
    "severance": ("restructuring", "termination"),
    "debt": ("borrowings", "obligations", "notes"),
    "capex": ("capital", "expenditures"),
    "buyback": ("repurchase", "repurchases"),
    "lease": ("leases", "rent"),
    "pension": ("pensions", "retirement"),
    "tax": ("taxes", "taxation"),
    "supplier": ("suppliers", "vendors"),
    "customer": ("customers", "retailers"),
}

# Section headers that tell a page is the statement a question refers to.
_STATEMENT_HINTS: tuple[tuple[tuple[str, ...], re.Pattern[str]], ...] = (
    (
        (
            "income statement", "income statements", "statement of income", "statements of income",
            "operating income", "net income", "operating profit", "gross profit", "income",
        ),
        re.compile(r"consolidated statements? of income|statements? of income", re.IGNORECASE),
    ),
    (
        ("cash flow", "cash flows", "cash flow statement", "operating activities"),
        re.compile(r"consolidated statements? of cash flows|statements? of cash flows", re.IGNORECASE),
    ),
    (
        ("balance sheet", "balance sheets"),
        re.compile(r"consolidated balance sheets?|balance sheets?", re.IGNORECASE),
    ),
)

_SECTION_MARKER = re.compile(r"item\s+\d+[a-z]?\.", re.IGNORECASE)
_QUERY_YEAR = re.compile(r"^fy(\d{4})$")
_STEM_SUFFIXES = ("ations", "ation", "ings", "ing", "ies", "ied", "es", "ed", "s")
_RRF_K = 60.0
_MAX_POOL = 160
_MIN_POOL = 40


def _stem(term: str) -> str:
    """Fold light English inflection so query and page share one key."""

    folded = term
    for suffix in _STEM_SUFFIXES:
        if folded.endswith(suffix) and len(folded) - len(suffix) >= 3:
            folded = folded[: -len(suffix)]
            break
    if len(folded) > 4 and folded.endswith("e"):
        folded = folded[:-1]
    if len(folded) > 4 and folded.endswith("y"):
        folded = folded[:-1]
    return folded


def _aliases(term: str) -> tuple[str, ...]:
    """Return extra evidence words that may stand in for one query term."""

    aliases: list[str] = []
    year = _QUERY_YEAR.match(term)
    if year is not None:
        aliases.append(year.group(1))
    aliases.extend(_SYNONYMS.get(term, ()))
    return tuple(aliases)


def _distinctive_terms(terms: Sequence[str]) -> tuple[str, ...]:
    """Keep terms that are long, numeric or otherwise rare in financial prose."""

    selected = [
        term
        for term in terms
        if len(term) >= 7 or any(character.isdigit() for character in term) or "_" in term
    ]
    return tuple(selected)


def _query_variants(question: str) -> tuple[str, ...]:
    """Build a few short, independent queries instead of one long one.

    A long question raises the lexical admission floor, so a page that carries the
    answer in the filing's own words can be dropped before it is ever ranked. Short
    concept queries keep that floor low, and the caller fuses the results.
    """

    terms = question_terms(question)
    variants: list[str] = [question.strip()]
    if terms:
        variants.append(" ".join(terms))
        distinctive = _distinctive_terms(terms)
        if distinctive and distinctive != terms:
            variants.append(" ".join(distinctive))

    groups = 0
    for term in terms:
        aliases = _SYNONYMS.get(term)
        if not aliases or groups >= 3:
            continue
        variants.append(" ".join((term, *aliases)))
        groups += 1

    unique: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        normalized = " ".join(variant.split())
        if not normalized or normalized.casefold() in seen:
            continue
        seen.add(normalized.casefold())
        unique.append(normalized)
    return tuple(unique)


def _tokens(text: str) -> list[str]:
    return _TERM_PATTERN.findall(text.casefold())


def _stemmed_tokens(text: str) -> set[str]:
    return {_stem(token) for token in _tokens(text)}


class _Candidate:
    """One distinct Memory entry observed in the merged candidate pool."""

    __slots__ = ("citation", "rrf", "text")

    def __init__(self, citation: Any, text: str) -> None:
        self.citation = citation
        self.text = text
        self.rrf = 0.0


def _rrf_weight(rank: int) -> float:
    return 1.0 / (_RRF_K + rank)


def _statement_bonus(question: str, text: str) -> float:
    """Reward the page that actually carries a statement the question names."""

    lowered = question.casefold()
    for triggers, pattern in _STATEMENT_HINTS:
        if any(trigger in lowered for trigger in triggers) and pattern.search(text):
            return 1.0
    return 0.0


def _section_bonus(concepts: Sequence[tuple[str, tuple[str, ...]]], text: str) -> float:
    """Reward a page whose section heading names a query concept."""

    lowered = text.casefold()
    for match in _SECTION_MARKER.finditer(lowered):
        window = _stemmed_tokens(lowered[match.start() : match.start() + 120])
        for key, aliases in concepts:
            if key in window or any(alias in window for alias in aliases):
                return 1.0
    return 0.0


def _coverage_score(
    concepts: Sequence[tuple[str, tuple[str, ...]]],
    document_frequency: dict[str, int],
    pool_size: int,
    page_stems: set[str],
) -> float:
    """Weighted share of query concepts evidenced on one page."""

    total = 0.0
    matched = 0.0
    for key, aliases in concepts:
        weight = math.log(1.0 + (pool_size + 1.0) / (1.0 + document_frequency.get(key, 0)))
        total += weight
        if key in page_stems or any(alias in page_stems for alias in aliases):
            matched += weight
    return matched / total if total else 0.0


def _phrase_bonus(terms: Sequence[str], lowered_text: str) -> float:
    """Reward pages containing adjacent query term pairs verbatim."""

    if len(terms) < 2:
        return 0.0
    hits = 0
    pairs = 0
    for left, right in zip(terms, terms[1:], strict=False):
        pairs += 1
        if f"{left} {right}" in lowered_text:
            hits += 1
    return hits / pairs if pairs else 0.0


async def _collect_candidates(memory: Any, question: str, limit: int) -> list[_Candidate]:
    from powercontext.artifacts import MemoryCitation  # local import avoids a cycle
    from powercontext.builtin.runtime import SearchMemoryRequest

    pool_limit = max(_MIN_POOL, min(_MAX_POOL, limit * 8))
    merged: dict[tuple[str, str, str, str], _Candidate] = {}
    for variant in _query_variants(question):
        try:
            page = await memory.search(SearchMemoryRequest(query=variant, mode="fts", limit=pool_limit))
        except Exception:  # noqa: BLE001 - one unusable variant must not abort retrieval
            continue
        for rank, hit in enumerate(page.hits, start=1):
            key = (hit.memory_ref.family, hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id)
            candidate = merged.get(key)
            if candidate is None:
                candidate = _Candidate(
                    MemoryCitation(
                        memory_ref=hit.memory_ref,
                        entry_id=hit.entry_id,
                        entry_version_id=hit.entry_version_id,
                    ),
                    hit.text,
                )
                merged[key] = candidate
            candidate.rrf += _rrf_weight(rank)
    return list(merged.values())


async def _resolve_page(memory: Any, citation: Any) -> tuple[str, int] | None:
    """Read one citation back and return its ``(document_id, page_number)``."""

    from powercontext.builtin.runtime import GetMemoryEntryRequest  # local import avoids a cycle

    try:
        entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
    except Exception:  # noqa: BLE001 - a stale citation is simply not selectable
        return None
    for reference in entry.sources:
        if reference.source_type != PDF_PAGE_SOURCE_NAME:
            continue
        parsed = parse_page_source_id(reference.source_id)
        if parsed is not None:
            return parsed
        parts = reference.source_id.split(":")
        if len(parts) == 4 and parts[2].startswith("~"):
            head = entry.text.split("\n", 1)[0].strip()
            try:
                return head, int(parts[1])
            except ValueError:
                return None
    return None


async def search_pdf_pages(memory: Any, question: str, limit: int = 10) -> tuple[Any, ...]:
    """Return up to ``limit`` distinct exact page citations for one question."""

    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")  # noqa: TRY003
    if not isinstance(question, str):
        raise ValueError("question must be a string")  # noqa: TRY003, TRY004
    text = question.strip()
    if not text:
        return ()

    candidates = await _collect_candidates(memory, text, limit)
    if not candidates:
        return ()

    terms = question_terms(text)
    concepts = tuple((_stem(term), tuple(_stem(alias) for alias in _aliases(term))) for term in terms)
    pool_size = len(candidates)
    document_frequency: dict[str, int] = {}
    page_stems: dict[int, set[str]] = {}
    for index, candidate in enumerate(candidates):
        stems = _stemmed_tokens(candidate.text)
        page_stems[index] = stems
        for key, aliases in concepts:
            if key in stems or any(alias in stems for alias in aliases):
                document_frequency[key] = document_frequency.get(key, 0) + 1

    peak = max(candidate.rrf for candidate in candidates) or 1.0
    scored: list[tuple[float, int, _Candidate]] = []
    for index, candidate in enumerate(candidates):
        coverage = _coverage_score(concepts, document_frequency, pool_size, page_stems[index])
        phrase = _phrase_bonus(terms, candidate.text.casefold())
        structure = _statement_bonus(text, candidate.text)
        section = _section_bonus(concepts, candidate.text)
        score = (
            0.40 * (candidate.rrf / peak)
            + 0.45 * coverage
            + 0.15 * phrase
            + 0.35 * structure
            + 0.20 * section
        )
        scored.append((score, index, candidate))
    scored.sort(key=lambda item: (-item[0], item[1]))

    citations: list[Any] = []
    seen_pages: set[tuple[str, int]] = set()
    for _score, _index, candidate in scored:
        if len(citations) >= limit:
            break
        page = await _resolve_page(memory, candidate.citation)
        if page is None or page in seen_pages:
            continue
        seen_pages.add(page)
        citations.append(candidate.citation)
    return tuple(citations)


__all__ = [
    "PDF_PAGE_SOURCE_ADAPTER",
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDF_PAGE_SOURCE_VERSION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "PDFPageSourceAdapter",
    "PDFPageTextEvidenceProjection",
    "parse_page_source_id",
    "parse_pdf_document",
    "pdf_page_source_id",
    "question_terms",
    "search_pdf_pages",
]
