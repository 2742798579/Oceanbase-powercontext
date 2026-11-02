"""语料切分：把规格、代码、测试切成带来源定位的 chunk。

三类文档的 chunk 都带真实来源（文件路径 + 行号或规格编号），保证上下文输出里的
"来源"可以被评审逐条核对。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

SPEC_ID_RE = re.compile(r"SPEC-[A-Z]+-\d+")
SPEC_HEADING_RE = re.compile(r"^###\s+(SPEC-[A-Z]+-\d+)\s*(.*)$")


@dataclass
class Chunk:
    chunk_id: str
    doc_type: str  # spec | code | test
    source: str  # 相对 business/ 的路径
    anchor: str  # 规格编号 / 符号名 / 测试函数名
    title: str
    text: str
    spec_ids: list[str] = field(default_factory=list)
    lineno: int = 0

    def metadata(self) -> dict[str, object]:
        return {
            "doc_type": self.doc_type,
            "source": self.source,
            "anchor": self.anchor,
            "title": self.title,
            "spec_ids": ",".join(self.spec_ids),
            "lineno": self.lineno,
        }


# ---------------------------------------------------------------- 规格


def parse_specs(root: Path) -> list[Chunk]:
    """按 `### SPEC-xxx-nnn` 标题切分规格文件。"""
    chunks: list[Chunk] = []
    for path in sorted(root.glob("specs/*.md")):
        rel = path.relative_to(root).as_posix()
        lines = path.read_text(encoding="utf-8").splitlines()

        current_id: str | None = None
        current_title = ""
        current_start = 0
        buffer: list[str] = []

        def flush() -> None:
            if not current_id:
                return
            body = "\n".join(buffer).strip()
            if not body:
                return
            chunks.append(
                Chunk(
                    chunk_id=f"spec:{current_id}",
                    doc_type="spec",
                    source=rel,
                    anchor=current_id,
                    title=current_title,
                    text=f"[{current_id}] {current_title}\n{body}",
                    spec_ids=[current_id],
                    lineno=current_start,
                )
            )

        for index, line in enumerate(lines, start=1):
            match = SPEC_HEADING_RE.match(line.strip())
            if match:
                flush()
                current_id = match.group(1)
                current_title = match.group(2).strip()
                current_start = index
                buffer = []
            elif current_id:
                buffer.append(line)
        flush()

    return chunks


# ---------------------------------------------------------------- 代码


def _symbol_chunk(
    *,
    rel: str,
    qualname: str,
    node: ast.AST,
    source: str,
    extra: str = "",
) -> Chunk:
    lineno = getattr(node, "lineno", 1)
    end = getattr(node, "end_lineno", lineno)
    segment = "\n".join(source.splitlines()[lineno - 1 : end])
    header = f"# symbol: {qualname}\n# file: {rel} (lines {lineno}-{end})\n"
    body = f"{header}{segment}"
    if extra:
        body = f"{header}{extra}\n{segment}"
    spec_ids = sorted(set(SPEC_ID_RE.findall(body)))
    return Chunk(
        chunk_id=f"code:{rel}::{qualname}",
        doc_type="code",
        source=rel,
        anchor=qualname,
        title=qualname,
        text=body,
        spec_ids=spec_ids,
        lineno=lineno,
    )


def parse_code(root: Path) -> list[Chunk]:
    """用 AST 抽取模块、类、函数/方法，按符号切分。"""
    chunks: list[Chunk] = []
    for path in sorted((root / "src").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        module_doc = ast.get_docstring(tree) or ""
        if module_doc:
            chunks.append(
                Chunk(
                    chunk_id=f"code:{rel}::__module__",
                    doc_type="code",
                    source=rel,
                    anchor=rel,
                    title=f"{rel} 模块说明",
                    text=f"# module: {rel}\n{module_doc}",
                    spec_ids=sorted(set(SPEC_ID_RE.findall(module_doc))),
                    lineno=1,
                )
            )

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                methods = [child.name for child in node.body if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))]
                summary = (
                    f"# class: {node.name}\n# methods: {', '.join(methods) if methods else '(none)'}\n"
                    f"# doc: {(ast.get_docstring(node) or '').strip()}\n"
                )
                chunks.append(
                    Chunk(
                        chunk_id=f"code:{rel}::{node.name}",
                        doc_type="code",
                        source=rel,
                        anchor=node.name,
                        title=f"class {node.name}",
                        text=summary,
                        spec_ids=sorted(set(SPEC_ID_RE.findall(summary))),
                        lineno=node.lineno,
                    )
                )
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        chunks.append(
                            _symbol_chunk(
                                rel=rel,
                                qualname=f"{node.name}.{child.name}",
                                node=child,
                                source=source,
                            )
                        )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chunks.append(
                    _symbol_chunk(rel=rel, qualname=node.name, node=node, source=source)
                )

    return chunks


# ---------------------------------------------------------------- 测试


def parse_tests(root: Path) -> list[Chunk]:
    """抽取测试函数及其 docstring 中声明的规格依据。"""
    chunks: list[Chunk] = []
    for path in sorted((root / "tests").rglob("test_*.py")):
        rel = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        names: list[str] = []
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            names.append(node.name)
            doc = (ast.get_docstring(node) or "").strip()
            lineno = node.lineno
            end = getattr(node, "end_lineno", lineno)
            segment = "\n".join(source.splitlines()[lineno - 1 : end])
            text = (
                f"# test: {node.name}\n# file: {rel} (lines {lineno}-{end})\n"
                f"# intent: {doc}\n{segment}"
            )
            chunks.append(
                Chunk(
                    chunk_id=f"test:{rel}::{node.name}",
                    doc_type="test",
                    source=rel,
                    anchor=node.name,
                    title=node.name,
                    text=text,
                    spec_ids=sorted(set(SPEC_ID_RE.findall(text))),
                    lineno=lineno,
                )
            )

        if names:
            summary = (
                f"# test module: {rel}\n# tests: {', '.join(names)}\n"
                f"# doc: {(ast.get_docstring(tree) or '').strip()}\n"
            )
            chunks.append(
                Chunk(
                    chunk_id=f"test:{rel}::__module__",
                    doc_type="test",
                    source=rel,
                    anchor=rel,
                    title=f"{rel} 测试模块",
                    text=summary,
                    spec_ids=sorted(set(SPEC_ID_RE.findall(summary))),
                    lineno=1,
                )
            )

    return chunks


def load_corpus(root: Path) -> list[Chunk]:
    return parse_specs(root) + parse_code(root) + parse_tests(root)
