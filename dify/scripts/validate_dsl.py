"""Dify DSL 静态校验：不依赖 Dify 也能查出手写 DSL 的常见错误。

检查项：
1. 根结构（app / kind / version / workflow.graph）
2. 节点 id 唯一、边引用的节点都存在
3. 恰好一个 start 节点、至少一个 answer 节点
4. 所有节点从 start 可达（无孤儿节点）
5. 变量引用 {{#node.var#}} 指向真实存在的节点与输出
6. env 变量引用在 environment_variables 中已声明
7. if-else 节点的出边句柄与 cases 对应

用法：python scripts/validate_dsl.py dsl/lie-pawnshop.chatflow.yml
"""

from __future__ import annotations

import re
import sys

import yaml

VAR_RE = re.compile(r"\{\{#([A-Za-z0-9_\-.]+)\.([A-Za-z0-9_\-]+)#\}\}")
ENV_RE = re.compile(r"\{\{#env\.([A-Za-z0-9_]+)#\}\}")

BUILTIN_OUTPUTS = {
    "sys": {"query", "user_id", "conversation_id", "files", "dialogue_count", "app_id", "workflow_run_id"},
}


def collect_strings(obj, out: list[str]) -> None:
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            collect_strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_strings(v, out)


def node_outputs(node: dict) -> set[str]:
    data = node.get("data", {})
    t = data.get("type")
    outs: set[str] = set()
    if t == "start":
        outs |= {v.get("variable") for v in data.get("variables", []) if v.get("variable")}
    elif t == "code":
        outs |= set((data.get("outputs") or {}).keys())
    elif t == "http-request":
        outs |= {"body", "status_code", "headers", "files"}
    elif t == "llm":
        outs |= {"text", "usage", "reasoning_content"}
    elif t == "template-transform":
        outs |= {"output"}
    elif t == "if-else":
        outs |= {c.get("case_id") for c in data.get("cases", []) if c.get("case_id")} | {"false"}
    elif t in {"answer", "end"}:
        outs |= {"output"}
    return outs


def main(path: str) -> int:
    with open(path, encoding="utf-8") as fh:
        dsl = yaml.safe_load(fh)

    errors: list[str] = []
    warns: list[str] = []

    for key in ("app", "kind", "version", "workflow"):
        if key not in dsl:
            errors.append(f"根节点缺少 {key}")
    if dsl.get("kind") != "app":
        errors.append("kind 必须为 app")
    if dsl.get("app", {}).get("mode") not in {"advanced-chat", "workflow", "chat", "agent-chat", "completion"}:
        errors.append("app.mode 非法")

    wf = dsl.get("workflow") or {}
    graph = wf.get("graph") or {}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        errors.append("graph.nodes 为空")

    ids = [n.get("id") for n in nodes]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        errors.append(f"节点 id 重复: {sorted(dup)}")
    by_id = {n.get("id"): n for n in nodes}

    starts = [n for n in nodes if n.get("data", {}).get("type") == "start"]
    if len(starts) != 1:
        errors.append(f"start 节点数量应为 1，实际 {len(starts)}")
    if not any(n.get("data", {}).get("type") == "answer" for n in nodes):
        warns.append("没有 answer 节点（workflow 模式下用 end 节点是正常的）")

    for e in edges:
        for end in ("source", "target"):
            if e.get(end) not in by_id:
                errors.append(f"边 {e.get('id')} 的 {end}={e.get(end)} 不存在")

    # 可达性
    adj: dict[str, list[str]] = {}
    for e in edges:
        adj.setdefault(e["source"], []).append(e["target"])
    seen: set[str] = set()
    stack = [starts[0]["id"]] if starts else []
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(adj.get(cur, []))
    orphans = set(by_id) - seen
    if orphans:
        errors.append(f"存在从 start 不可达的节点: {sorted(orphans)}")

    # if-else 句柄
    for n in nodes:
        if n.get("data", {}).get("type") != "if-else":
            continue
        handles = {c.get("case_id") for c in n["data"].get("cases", [])} | {"false"}
        used = {e.get("sourceHandle") for e in edges if e.get("source") == n["id"]}
        missing = handles - used
        extra = used - handles
        if missing:
            warns.append(f"if-else {n['id']} 有未连接的出口: {sorted(missing)}")
        if extra:
            errors.append(f"if-else {n['id']} 使用了不存在的出口: {sorted(extra)}")

    # 变量引用
    texts: list[str] = []
    collect_strings(graph, texts)
    declared_env = {v.get("name") for v in (wf.get("environment_variables") or [])}
    for text in texts:
        for m in VAR_RE.finditer(text):
            nid, var = m.group(1), m.group(2)
            if nid in BUILTIN_OUTPUTS:
                if var not in BUILTIN_OUTPUTS[nid]:
                    warns.append(f"引用了未定义的系统变量: {nid}.{var}")
                continue
            if nid == "env":
                continue
            node = by_id.get(nid)
            if node is None:
                errors.append(f"变量引用指向不存在的节点: {nid}.{var}")
                continue
            if var not in node_outputs(node):
                errors.append(f"变量引用指向不存在的输出: {nid}.{var}（该节点输出: {sorted(node_outputs(node))}）")
        for m in ENV_RE.finditer(text):
            if m.group(1) not in declared_env:
                errors.append(f"env 变量未声明: {m.group(1)}")

    print(f"节点 {len(nodes)} 个，边 {len(edges)} 条")
    for w in warns:
        print(f"[warn] {w}")
    if errors:
        for e in errors:
            print(f"[error] {e}")
        print("校验失败")
        return 1
    print("校验通过")
    return 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "dsl/lie-pawnshop.chatflow.yml"
    raise SystemExit(main(target))
