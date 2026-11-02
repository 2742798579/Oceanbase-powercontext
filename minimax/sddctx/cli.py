"""命令行入口：sddctx <command>。

调用入口即本 CLI（README 中的"调用入口"一节对应这些命令）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .builder import TaskSpec, build_context, render_markdown
from .config import MODULE_ROOT, get_settings
from .pipeline import (
    build_task_context,
    ensure_scope,
    generate_patch,
    ingest,
    resume_state,
    restore_before,
    run_tests,
    save_context,
    save_state,
    snapshot_after,
    snapshot_before,
    write_patch_diff,
)
from .store import open_store
from .verify import Verification

DEFAULT_TASK = MODULE_ROOT / "samples" / "verification" / "task.json"


def _load_task(path: str | None) -> TaskSpec:
    return TaskSpec.from_file(Path(path) if path else DEFAULT_TASK)


def cmd_init_db(args) -> int:
    settings = get_settings()
    store = open_store(settings, backend=args.backend)
    store.reset()
    print(json.dumps({"backend": store.backend, "documents": store.count()}, ensure_ascii=False))
    store.close()
    return 0


def cmd_ingest(args) -> int:
    settings = get_settings()
    summary = ingest(settings, backend=args.backend)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_context(args) -> int:
    settings = get_settings()
    task = _load_task(args.task)
    bundle = build_task_context(settings, task, with_state=args.with_state, backend=args.backend)
    json_path, md_path = save_context(settings, bundle)
    if args.json:
        print(json.dumps(bundle, ensure_ascii=False))
    else:
        print(render_markdown(bundle))
        print(f"\n已保存：{json_path}\n已保存：{md_path}")
    return 0


def cmd_patch(args) -> int:
    settings = get_settings()
    task = _load_task(args.task)
    bundle = build_task_context(settings, task, with_state=args.with_state)
    result = generate_patch(settings, bundle, name=args.name)
    snapshot_after(settings)
    diff_path = write_patch_diff(settings)
    result["diff"] = str(diff_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_test(args) -> int:
    settings = get_settings()
    result = run_tests(settings, label=args.label, targets=args.targets)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_snapshot(args) -> int:
    settings = get_settings()
    if args.which == "before":
        files = snapshot_before(settings)
    else:
        files = snapshot_after(settings)
    print(json.dumps({"snapshot": args.which, "files": files}, ensure_ascii=False, indent=2))
    return 0


def cmd_restore(args) -> int:
    settings = get_settings()
    files = restore_before(settings)
    print(json.dumps({"restored": files}, ensure_ascii=False, indent=2))
    return 0


def cmd_state_save(args) -> int:
    settings = get_settings()
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    result = save_state(settings, state)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_state_resume(args) -> int:
    settings = get_settings()
    task = _load_task(args.task)
    result = resume_state(settings, task, backend=args.backend)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def cmd_scope(args) -> int:
    settings = get_settings()
    print(json.dumps({"scope_id": ensure_scope(settings)}, ensure_ascii=False))
    return 0


def cmd_verify(args) -> int:
    settings = get_settings()
    task = _load_task(args.task)
    verification = Verification(
        settings, task, backend=args.backend, reuse_patch=args.reuse_patch
    )
    report = verification.run()
    print()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_passed"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sddctx",
        description="规格驱动开发的工程上下文模块（seekdb + PowerContext + MiniMax）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--backend", choices=["seekdb", "memory"], default="seekdb")
    common.add_argument("--task", default=None, help="任务定义 JSON，默认 samples/verification/task.json")

    p = sub.add_parser("init-db", parents=[common], help="重建 seekdb collection")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("ingest", parents=[common], help="导入规格 / 代码 / 测试到 seekdb")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("context", parents=[common], help="按任务构造开发上下文")
    p.add_argument("--with-state", action="store_true", help="合并 PowerContext 中的历史任务状态")
    p.add_argument("--json", action="store_true", help="输出 JSON 而不是 Markdown")
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("patch", parents=[common], help="调用 MiniMax 生成并落地代码修改")
    p.add_argument("--name", default="patch")
    p.add_argument("--with-state", action="store_true")
    p.set_defaults(func=cmd_patch)

    p = sub.add_parser("test", parents=[common], help="运行 pytest 并记录日志")
    p.add_argument("--label", required=True)
    p.add_argument("--targets", nargs="+", required=True)
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("snapshot", parents=[common], help="生成修改前 / 后代码快照")
    p.add_argument("--which", choices=["before", "after"], required=True)
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("restore", parents=[common], help="恢复到修改前快照")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("state-save", parents=[common], help="把任务状态写入 PowerContext")
    p.add_argument("--state", required=True, help="状态 JSON 文件")
    p.set_defaults(func=cmd_state_save)

    p = sub.add_parser("state-resume", parents=[common], help="新进程读取并复用任务状态")
    p.add_argument("--json", action="store_true", help="以 JSON 输出（默认行为，保留该参数便于脚本调用）")
    p.set_defaults(func=cmd_state_resume)

    p = sub.add_parser("scope", parents=[common], help="创建 / 复用 PowerContext scope")
    p.set_defaults(func=cmd_scope)

    p = sub.add_parser("verify", parents=[common], help="执行六项必做验收并生成报告")
    p.add_argument("--reuse-patch", action="store_true", help="复用 patches/after，不再调用模型")
    p.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
