"""上下文构造：把任务翻译成"该遵守哪些规格、该改哪些代码、该跑哪些测试"。

检索策略（seekdb 上执行两类真实查询）：
1. 向量检索：任务描述 + 关键词 → HNSW 近邻
2. 全文检索：规格编号、符号名、关键词 → fulltext `$contains`
两路结果用 RRF（Reciprocal Rank Fusion）融合，再按"任务显式声明的期望来源"加权，
最后按规格 / 代码 / 测试分桶输出，每条都带真实文件路径与行号。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .store import Hit, InMemoryStore, SeekdbStore

RRF_K = 60


@dataclass
class TaskSpec:
    task_id: str
    title: str
    description: str
    keywords: list[str] = field(default_factory=list)
    expected_specs: list[str] = field(default_factory=list)
    expected_code: list[str] = field(default_factory=list)
    expected_tests: list[str] = field(default_factory=list)
    target_tests: list[str] = field(default_factory=list)
    regression_tests: list[str] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: Path) -> "TaskSpec":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            task_id=data["task_id"],
            title=data["title"],
            description=data["description"],
            keywords=list(data.get("keywords", [])),
            expected_specs=list(data.get("expected_specs", [])),
            expected_code=list(data.get("expected_code", [])),
            expected_tests=list(data.get("expected_tests", [])),
            target_tests=list(data.get("target_tests", [])),
            regression_tests=list(data.get("regression_tests", [])),
        )

    @property
    def query_text(self) -> str:
        return f"{self.title}\n{self.description}\n" + " ".join(self.keywords)


def _rrf(ranked_lists: list[list[Hit]]) -> dict[str, tuple[float, Hit]]:
    fused: dict[str, tuple[float, Hit]] = {}
    for hits in ranked_lists:
        for rank, hit in enumerate(hits, start=1):
            score, _ = fused.get(hit.chunk_id, (0.0, hit))
            fused[hit.chunk_id] = (score + 1.0 / (RRF_K + rank), hit)
    return fused


def _collect(
    store,
    doc_type: str,
    queries: list[str],
    anchors: list[str],
    limit: int,
) -> list[tuple[float, Hit]]:
    ranked_lists: list[list[Hit]] = []

    for query in queries:
        if not query.strip():
            continue
        ranked_lists.append(store.search_vector(query, limit=limit * 2, doc_type=doc_type))
        ranked_lists.append(store.search_contains(query, limit=limit * 2, doc_type=doc_type))

    for anchor in anchors:
        ranked_lists.append(store.search_contains(anchor, limit=limit, doc_type=doc_type))

    fused = _rrf(ranked_lists)

    results: list[tuple[float, Hit]] = []
    for score, hit in fused.values():
        boost = 0.0
        anchor_value = str(hit.metadata.get("anchor", ""))
        spec_ids = str(hit.metadata.get("spec_ids", ""))
        for expected in anchors:
            if expected and (expected == anchor_value or expected in spec_ids or expected == hit.chunk_id):
                boost += 0.05
        results.append((score + boost, hit))

    results.sort(key=lambda item: item[0], reverse=True)
    return results[:limit]


def _excerpt(text: str, limit: int = 1200) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _spec_item(score: float, hit: Hit) -> dict:
    return {
        "id": str(hit.metadata.get("anchor", hit.chunk_id)),
        "title": str(hit.metadata.get("title", "")),
        "source": str(hit.metadata.get("source", "")),
        "lineno": int(hit.metadata.get("lineno", 0) or 0),
        "score": round(score, 4),
        "excerpt": _excerpt(hit.document),
    }


def _code_item(score: float, hit: Hit) -> dict:
    return {
        "symbol": str(hit.metadata.get("anchor", hit.chunk_id)),
        "source": str(hit.metadata.get("source", "")),
        "lineno": int(hit.metadata.get("lineno", 0) or 0),
        "spec_ids": [item for item in str(hit.metadata.get("spec_ids", "")).split(",") if item],
        "score": round(score, 4),
        "excerpt": _excerpt(hit.document, 2000),
    }


def _test_item(score: float, hit: Hit) -> dict:
    return {
        "name": str(hit.metadata.get("anchor", hit.chunk_id)),
        "source": str(hit.metadata.get("source", "")),
        "lineno": int(hit.metadata.get("lineno", 0) or 0),
        "spec_ids": [item for item in str(hit.metadata.get("spec_ids", "")).split(",") if item],
        "score": round(score, 4),
        "excerpt": _excerpt(hit.document, 1600),
    }


def build_context(
    store: SeekdbStore | InMemoryStore,
    task: TaskSpec,
    *,
    max_specs: int = 6,
    max_code: int = 4,
    max_tests: int = 6,
    state: dict | None = None,
) -> dict:
    queries = [task.query_text] + task.keywords

    specs = _collect(store, "spec", queries, task.expected_specs, max_specs)
    code = _collect(store, "code", queries, task.expected_code, max_code)
    tests = _collect(store, "test", queries, task.expected_tests, max_tests)

    bundle = {
        "schema": "sdd-context.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "task": {
            "task_id": task.task_id,
            "title": task.title,
            "description": task.description,
            "target_tests": task.target_tests,
            "regression_tests": task.regression_tests,
        },
        "store": {
            "backend": getattr(store, "backend", "unknown"),
            "documents": store.count(),
            "queries": len(getattr(store, "query_log", [])),
            "query_log": list(getattr(store, "query_log", [])),
        },
        "specs": [_spec_item(score, hit) for score, hit in specs],
        "code": [_code_item(score, hit) for score, hit in code],
        "tests": [_test_item(score, hit) for score, hit in tests],
        "state": state or {},
    }
    bundle["sources"] = sorted(
        {item["source"] for key in ("specs", "code", "tests") for item in bundle[key]}
    )
    return bundle


def render_markdown(bundle: dict) -> str:
    task = bundle["task"]
    lines: list[str] = [
        f"# 开发上下文 · {task['title']}",
        "",
        f"- 任务 ID：`{task['task_id']}`",
        f"- 生成时间：{bundle['generated_at']}",
        f"- 检索后端：`{bundle['store']['backend']}`（库内文档 {bundle['store']['documents']} 条）",
        "",
        "## 任务描述",
        "",
        task["description"],
        "",
        f"## 适用规格（{len(bundle['specs'])} 条）",
        "",
    ]
    for item in bundle["specs"]:
        lines.append(f"### {item['id']} {item['title']}")
        lines.append(f"来源：`{item['source']}:{item['lineno']}`（相关度 {item['score']}）")
        lines.append("")
        lines.append("```text")
        lines.append(item["excerpt"])
        lines.append("```")
        lines.append("")

    lines.append(f"## 需要修改的代码（{len(bundle['code'])} 个符号）")
    lines.append("")
    for item in bundle["code"]:
        lines.append(f"- `{item['symbol']}` — `{item['source']}:{item['lineno']}`（相关度 {item['score']}）")
    lines.append("")

    lines.append(f"## 用于验证的测试（{len(bundle['tests'])} 个）")
    lines.append("")
    for item in bundle["tests"]:
        spec_text = f" → {', '.join(item['spec_ids'])}" if item["spec_ids"] else ""
        lines.append(f"- `{item['name']}` — `{item['source']}:{item['lineno']}`{spec_text}")
    lines.append("")

    state = bundle.get("state") or {}
    lines.append("## 任务状态")
    lines.append("")
    lines.append(f"- 状态来源：{state.get('source', '（无历史状态）')}")
    for label, key in (("已完成", "completed"), ("待办", "todo"), ("已有验证结果", "verification")):
        values = state.get(key) or []
        lines.append(f"- {label}：" + ("；".join(values) if values else "（空）"))
    lines.append("")
    return "\n".join(lines)


SYSTEM_PROMPT = """你是一名严格遵守规格驱动开发（SDD）约束的资深 Python 工程师。

工作方式：
1. 只依据给定的规格条目、代码和测试进行修改，不得引入规格之外的行为。
2. 必须保持"兼容性"规格要求的所有既有行为不变。
3. 输出必须是可直接落盘的完整文件内容，禁止使用 diff、省略号或伪代码。
4. 只修改必要的文件，不得改动测试文件。

输出格式（必须是合法 JSON，不要包裹 markdown 代码块）：
{
  "summary": "一句话说明改了什么",
  "edits": [
    {"path": "src/order_service/service.py", "content": "完整文件内容"}
  ],
  "spec_coverage": [
    {"spec_id": "SPEC-ORD-009", "how": "在该函数中如何满足"}
  ],
  "notes": "兼容性说明与风险点"
}
"""


def build_prompt(bundle: dict) -> list[dict]:
    task = bundle["task"]
    sections: list[str] = [
        "## 任务",
        f"{task['title']}",
        "",
        task["description"],
        "",
        "## 必须遵守的规格",
    ]
    for item in bundle["specs"]:
        sections.append(f"### {item['id']} {item['title']}")
        sections.append(f"（来源：{item['source']}:{item['lineno']}）")
        sections.append(item["excerpt"])
        sections.append("")

    sections.append("## 相关代码")
    for item in bundle["code"]:
        sections.append(f"### {item['symbol']} — {item['source']}")
        sections.append("```python")
        sections.append(item["excerpt"])
        sections.append("```")
        sections.append("")

    sections.append("## 相关测试（不得修改）")
    for item in bundle["tests"]:
        sections.append(f"### {item['name']} — {item['source']}")
        sections.append("```python")
        sections.append(item["excerpt"])
        sections.append("```")
        sections.append("")

    state = bundle.get("state") or {}
    if state:
        sections.append("## 历史任务状态（来自 PowerContext）")
        for label, key in (("已完成", "completed"), ("待办", "todo"), ("已有验证结果", "verification")):
            values = state.get(key) or []
            if values:
                sections.append(f"- {label}：" + "；".join(values))
        sections.append("")

    sections.append("请按系统提示要求的 JSON 格式输出修改结果。")

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(sections)},
    ]
