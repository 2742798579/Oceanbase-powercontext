"""端到端流水线：入库 → 检索 → 生成上下文 → MiniMax 改码 → 测试 → 状态复用。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .builder import TaskSpec, build_context, build_prompt, render_markdown
from .corpus import load_corpus
from .minimax import (
    MiniMaxClient,
    MiniMaxError,
    apply_edits,
    code_version,
    extract_json,
    save_record,
    snapshot_workspace,
    write_diff,
)
from .powercontext import PowerContextClient, PowerContextError
from .runner import run_pytest, write_json
from .store import open_store


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ------------------------------------------------------------------ 入库


def ingest(settings, *, backend: str = "seekdb", rebuild: bool = True) -> dict:
    store = open_store(settings, backend=backend)
    chunks = load_corpus(settings.workspace)
    if rebuild:
        store.reset()
    added = store.add(chunks)

    summary = {
        "backend": store.backend,
        "collection": settings.collection,
        "documents": store.count(),
        "added": added,
        "by_type": {
            doc_type: sum(1 for chunk in chunks if chunk.doc_type == doc_type)
            for doc_type in ("spec", "code", "test")
        },
        "sample_ids": [chunk.chunk_id for chunk in chunks[:8]],
        "ingested_at": _now(),
    }
    write_json(settings.records_dir / "db" / "ingest.json", summary)
    store.close()
    return summary


# ------------------------------------------------------------------ 上下文


def build_task_context(settings, task: TaskSpec, *, with_state: bool, backend: str = "seekdb") -> dict:
    store = open_store(settings, backend=backend)
    state = None
    if with_state:
        state = load_state(settings, task)
    bundle = build_context(store, task, state=state)
    bundle["code_version"] = code_version(settings.workspace)
    store.close()
    return bundle


def save_context(settings, bundle: dict) -> tuple[Path, Path]:
    stamp = _now()
    json_path = settings.records_dir / "context" / f"{bundle['task']['task_id']}-{stamp}.json"
    md_path = settings.records_dir / "context" / f"{bundle['task']['task_id']}-{stamp}.md"
    write_json(json_path, bundle)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(bundle), encoding="utf-8")
    return json_path, md_path


# ------------------------------------------------------------------ MiniMax


def generate_patch(settings, bundle: dict, *, name: str = "patch") -> dict:
    client = MiniMaxClient(
        api_key=settings.minimax_api_key,
        base_url=settings.minimax_base_url,
        model=settings.minimax_model,
        temperature=settings.minimax_temperature,
    )
    messages = build_prompt(bundle)
    reply = client.chat(messages)
    record_path = save_record(
        settings.records_dir / "minimax",
        name,
        messages=messages,
        reply=reply,
        extra={
            "task_id": bundle["task"]["task_id"],
            "code_version_before": code_version(settings.workspace),
            "context_sources": bundle["sources"],
        },
    )

    payload = extract_json(reply.content)
    edits = payload.get("edits") or []
    if not edits:
        raise MiniMaxError(f"模型未返回 edits，记录见 {record_path}")

    changed = apply_edits(settings.workspace, edits)
    return {
        "record": str(record_path),
        "summary": payload.get("summary", ""),
        "spec_coverage": payload.get("spec_coverage", []),
        "notes": payload.get("notes", ""),
        "changed": changed,
        "model": reply.model,
        "usage": reply.usage,
        "latency_ms": reply.latency_ms,
    }


# ------------------------------------------------------------------ 快照


def snapshot_before(settings) -> list[str]:
    return snapshot_workspace(settings.workspace, settings.root / "patches" / "before")


def snapshot_after(settings) -> list[str]:
    return snapshot_workspace(settings.workspace, settings.root / "patches" / "after")


def restore_before(settings) -> list[str]:
    before = settings.root / "patches" / "before"
    if not before.is_dir():
        raise FileNotFoundError(f"缺少基线快照：{before}")
    restored: list[str] = []
    for path in sorted(before.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(before)
        destination = settings.workspace / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        restored.append(rel.as_posix())
    return restored


def write_patch_diff(settings) -> Path:
    output = settings.root / "patches" / "order-amount-filter.diff"
    write_diff(settings.root / "patches" / "before", settings.root / "patches" / "after", output)
    return output


# ------------------------------------------------------------------ 测试


def run_tests(settings, *, label: str, targets: list[str]) -> dict:
    run = run_pytest(
        settings.workspace,
        targets,
        label=label,
        log_dir=settings.records_dir / "tests",
    )
    payload = run.as_dict()
    write_json(settings.records_dir / "tests" / f"{label}.json", payload)
    return payload


# ------------------------------------------------------------------ 状态


def _pc(settings) -> PowerContextClient:
    return PowerContextClient(settings.powercontext_url, settings.powercontext_token)


def ensure_scope(settings) -> str:
    if settings.powercontext_scope:
        return settings.powercontext_scope

    cache = settings.records_dir / "state" / "scope.json"
    if cache.is_file():
        cached = json.loads(cache.read_text(encoding="utf-8")).get("scope_id")
        if cached:
            return str(cached)

    scope_id = _pc(settings).ensure_scope(
        title=settings.powercontext_title,
        summary="order-service 规格驱动开发上下文模块的任务状态",
        idempotency_key="order-service-sdd-scope",
    )
    write_json(cache, {"scope_id": scope_id, "created_at": _now()})
    return scope_id


def save_state(settings, state: dict) -> dict:
    scope_id = ensure_scope(settings)
    client = _pc(settings)
    result = client.save_task_state(scope_id, state, run_id=_now())
    write_json(
        settings.records_dir / "state" / f"{state['task_id']}-saved.json",
        {"scope_id": scope_id, "state": state, "powercontext": result, "saved_at": _now()},
    )
    return {"scope_id": scope_id, "powercontext": result}


def load_state(settings, task: TaskSpec) -> dict:
    try:
        scope_id = ensure_scope(settings)
        return _pc(settings).load_task_state(scope_id, task.task_id, task.query_text)
    except PowerContextError as error:
        return {"source": f"PowerContext 不可用：{error}", "completed": [], "todo": [], "verification": []}


def resume_state(settings, task: TaskSpec, backend: str = "seekdb") -> dict:
    """在新进程里读取状态并重建上下文，证明状态可跨进程复用。"""
    state = load_state(settings, task)
    bundle = build_task_context(settings, task, with_state=False, backend=backend)
    bundle["state"] = state
    json_path, md_path = save_context(settings, bundle)
    return {
        "scope_source": state.get("source"),
        "completed": state.get("completed", []),
        "todo": state.get("todo", []),
        "verification": state.get("verification", []),
        "prepared_status": state.get("prepared_status"),
        "context_json": str(json_path),
        "context_md": str(md_path),
        "context_has_state": bool(state.get("completed")),
    }


def resume_in_new_process(settings, task_path: Path, backend: str = "seekdb") -> dict:
    """用独立进程模拟"重启上下文模块"，避免进程内缓存掩盖问题。"""
    command = [
        sys.executable,
        "-m",
        "sddctx",
        "state-resume",
        "--task",
        str(task_path),
        "--backend",
        backend,
        "--json",
    ]
    completed = subprocess.run(
        command,
        cwd=str(settings.root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(f"重启后读取状态失败：{completed.stderr or completed.stdout}")
    return json.loads(completed.stdout.strip().splitlines()[-1])
