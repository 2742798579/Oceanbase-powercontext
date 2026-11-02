#!/usr/bin/env python
"""根据 records/ 下的运行证据生成 docs/usage-record.md（技术使用记录）。

用法：python scripts/build_usage_record.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORDS = ROOT / "records"
OUT = ROOT / "docs" / "usage-record.md"


def load(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def latest(pattern: str) -> dict | None:
    files = sorted(RECORDS.glob(pattern))
    return load(files[-1]) if files else None


def main() -> None:
    ingest = load(RECORDS / "db" / "ingest.json")
    minimax = latest("minimax/*.json")
    context = latest("context/T1-amount-filter-verify.json")
    tests = {
        label: load(RECORDS / "tests" / f"{label}.json")
        for label in ("before-target", "before-regression", "after-target", "after-regression")
    }
    state = load(RECORDS / "state" / "T1-amount-filter-saved.json")
    report = load(RECORDS / "verification" / "verification-report.json")

    lines: list[str] = [
        "# 技术使用记录",
        "",
        "> 本文件由 `scripts/build_usage_record.py` 从 `records/` 下的运行证据自动生成，",
        "> 每一项都可以在对应 JSON / 日志里逐条核对。",
        "",
    ]
    if not minimax:
        lines += [
            "> ⚠️ **当前状态：尚未执行真实运行**（`records/` 为空），本文件是模板。",
            "> 配好 `MINIMAX_API_KEY` 并跑完 `python -m sddctx verify` 后，重新执行",
            "> `python scripts/build_usage_record.py` 即可替换为真实记录。",
            "",
        ]
    lines += [
        "## 1. MiniMax 开发记录",
        "",
    ]

    if minimax:
        request_chars = sum(len(m.get("content", "")) for m in minimax["request"]["messages"])
        lines += [
            f"- 模型：`{minimax['model']}`，接口：OpenAI 兼容 `/chat/completions`",
            f"- 调用时间：{minimax['timestamp']}，耗时 {minimax['latency_ms']} ms",
            f"- 用量：`{json.dumps(minimax.get('usage') or {}, ensure_ascii=False)}`",
            f"- 请求消息 {len(minimax['request']['messages'])} 条，共 {request_chars} 字符（含规格、代码、测试、历史状态）",
            f"- 响应 {len(minimax['response']['content'])} 字符，含 `edits`（整文件内容）与 `spec_coverage`",
            f"- 记录文件：`records/minimax/{minimax['timestamp'].replace(':', '').replace('-', '')}`（同目录下最新一份）",
            "",
            "请求上下文里包含的来源（用于核对模型实际看到了什么）：",
            "",
        ]
        for source in minimax.get("context_sources", []):
            lines.append(f"- `{source}`")
        lines.append("")
    else:
        lines += [
            "（尚未执行真实调用：请配置 `MINIMAX_API_KEY` 后运行 `python -m sddctx verify`，",
            "本文件会自动重写为真实记录。）",
            "",
        ]

    lines += ["## 2. seekdb 数据库使用记录", ""]
    if ingest:
        lines += [
            f"- 后端：`{ingest['backend']}`，collection：`{ingest['collection']}`，数据库：`sdd_ctx`",
            f"- 入库文档：{ingest['documents']} 条（规格 {ingest['by_type']['spec']} / 代码 {ingest['by_type']['code']} / 测试 {ingest['by_type']['test']}）",
            f"- 入库时间：{ingest['ingested_at']}",
            "- chunk 划分：规格按 `### SPEC-*` 标题切；代码按 AST 符号切（含文件与行号）；测试按 `test_*` 函数切",
            "",
        ]
    if context:
        log = context["store"].get("query_log") or []
        lines += [
            f"- 本次上下文构造执行检索 {len(log)} 次（向量 + fulltext 各半），示例：",
            "",
            "```text",
            *[f"{item}" for item in log[:12]],
            "```",
            "",
            "- 检索结果直接进入上下文：",
            "",
            "| 类型 | 命中来源 |",
            "| --- | --- |",
        ]
        for item in context["specs"]:
            lines.append(f"| 规格 | `{item['id']}` ← `{item['source']}:{item['lineno']}` |")
        for item in context["code"]:
            lines.append(f"| 代码 | `{item['symbol']}` ← `{item['source']}:{item['lineno']}` |")
        for item in context["tests"]:
            lines.append(f"| 测试 | `{item['name']}` ← `{item['source']}:{item['lineno']}` |")
        lines.append("")

    lines += ["## 3. PowerContext 状态写入与复用记录", ""]
    if state:
        result = state.get("powercontext", {})
        source = result.get("source") or {}
        lines += [
            f"- scope：`{state['scope_id']}`（`POST /v1/scopes`，幂等键 `order-service-sdd-scope`）",
            f"- 状态快照：`POST /v1/sources/content` → source `{source.get('source_id', 'n/a')}`",
            f"- Memory 写入：`POST /v1/memory/remember` × {len(result.get('memories') or [])}"
            "（kind = `sdd-task-completed` / `sdd-task-todo` / `sdd-task-verification`）",
            f"- 保存时间：{state['saved_at']}",
            "",
        ]
        saved_state = state.get("state", {})
        for label, key in (("已完成", "completed"), ("待办", "todo"), ("验证结果", "verification")):
            values = saved_state.get(key) or []
            lines.append(f"- {label}：" + ("；".join(values) if values else "（空）"))
        lines += [
            "",
            "- 复用路径：新进程执行 `python -m sddctx state-resume`，通过",
            "  `POST /v1/memory/entries/list` + `POST /v1/memory/search` + `POST /v1/context/prepare`",
            "  读回上述状态，并写入新生成的上下文（`records/context/T1-amount-filter-*.json` 的 `state` 字段）。",
            "",
        ]
    else:
        lines += ["（尚未写入状态）", ""]

    lines += ["## 4. 测试执行记录", "", "| 轮次 | 命令 | 退出码 | 结果 | 代码版本 | 日志 |", "| --- | --- | --- | --- | --- | --- |"]
    for label, payload in tests.items():
        if not payload:
            continue
        lines.append(
            f"| {label} | `{payload['command']}` | {payload['exit_code']} | "
            f"{payload['passed']} passed / {payload['failed']} failed / {payload['errors']} error | "
            f"`{payload['code_version']}` | `{Path(payload['log_path']).name}` |"
        )
    lines.append("")

    if report:
        lines += ["## 5. 验收结果", "", "| 验收项 | 结果 | 证据 |", "| --- | --- | --- |"]
        for check in report["checks"]:
            lines.append(
                f"| {check['title']} | {'通过' if check['passed'] else '未通过'} | {check['evidence']} |"
            )
        lines.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"written: {OUT}")


if __name__ == "__main__":
    main()
