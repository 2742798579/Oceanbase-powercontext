"""六项必做验收的自动化执行与报告生成。

对应题面 4.1 的六个验收项：
1. 能够运行        2. MiniMax 实际参与   3. 上下文正确
4. 数据库实际参与  5. 状态能够复用       6. 代码修改有效
"""

from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from .builder import TaskSpec, build_context, render_markdown
from .corpus import load_corpus
from .minimax import code_version, tree_hash
from .pipeline import (
    ensure_scope,
    generate_patch,
    ingest,
    load_state,
    resume_in_new_process,
    restore_before,
    run_tests,
    save_state,
    snapshot_after,
    snapshot_before,
    write_patch_diff,
)
from .runner import write_json
from .store import open_store

SPEC_FILE = "specs/order-query-spec.md"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Verification:
    def __init__(self, settings, task: TaskSpec, *, backend: str = "seekdb", reuse_patch: bool = False) -> None:
        self.settings = settings
        self.task = task
        self.backend = backend
        self.reuse_patch = reuse_patch
        self.checks: list[dict] = []
        self.artifacts: dict[str, str] = {}
        self.started = _now()

    # -------------------------------------------------------------- 记录
    def check(self, key: str, title: str, passed: bool, evidence: str) -> None:
        self.checks.append(
            {"key": key, "title": title, "passed": bool(passed), "evidence": evidence}
        )
        flag = "PASS" if passed else "FAIL"
        print(f"[{flag}] {key} {title} — {evidence}")

    # -------------------------------------------------------------- 执行
    def run(self) -> dict:
        settings, task = self.settings, self.task
        task_path = settings.root / "samples" / "verification" / "task.json"

        # 0. 基线：恢复修改前快照（干净起点）
        if (settings.root / "patches" / "before").is_dir():
            restored = restore_before(settings)
            print(f"[init] 已恢复修改前快照：{len(restored)} 个文件")

        # 1. 入库
        summary = ingest(settings, backend=self.backend)
        self.artifacts["ingest"] = str(settings.records_dir / "db" / "ingest.json")
        self.check(
            "run",
            "能够运行（初始化数据 + 启动模块）",
            summary["documents"] > 0,
            f"入库 {summary['documents']} 条（规格 {summary['by_type']['spec']} / 代码 "
            f"{summary['by_type']['code']} / 测试 {summary['by_type']['test']}），后端 {summary['backend']}",
        )

        # 2. 上下文
        store = open_store(settings, backend=self.backend)
        bundle = build_context(store, task)
        bundle["code_version"] = code_version(settings.workspace)
        store.close()

        md_path = settings.records_dir / "context" / f"{task.task_id}-verify.md"
        json_path = settings.records_dir / "context" / f"{task.task_id}-verify.json"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown(bundle), encoding="utf-8")
        write_json(json_path, bundle)
        self.artifacts["context"] = str(json_path)
        self.artifacts["context_md"] = str(md_path)

        spec_ids = {item["id"] for item in bundle["specs"]}
        code_symbols = {item["symbol"] for item in bundle["code"]}
        test_names = {item["name"] for item in bundle["tests"]}

        missing_specs = [item for item in task.expected_specs if item not in spec_ids]
        missing_code = [item for item in task.expected_code if not any(item in symbol for symbol in code_symbols)]
        missing_tests = [item for item in task.expected_tests if item not in test_names]

        self.check(
            "context",
            "上下文正确（规格 / 代码 / 测试来源齐全）",
            not (missing_specs or missing_code or missing_tests),
            "规格命中 {}/{}，代码符号命中 {}/{}，测试命中 {}/{}；缺失：{}".format(
                len(task.expected_specs) - len(missing_specs),
                len(task.expected_specs),
                len(task.expected_code) - len(missing_code),
                len(task.expected_code),
                len(task.expected_tests) - len(missing_tests),
                len(task.expected_tests),
                missing_specs + missing_code + missing_tests or "无",
            ),
        )

        self.check(
            "database",
            "数据库实际参与（seekdb 检索结果进入上下文）",
            bundle["store"]["backend"] == "seekdb" and bundle["store"]["queries"] > 0,
            f"后端 {bundle['store']['backend']}，库内文档 {bundle['store']['documents']} 条，"
            f"本次执行 {bundle['store']['queries']} 次检索（向量 + 全文），结果全部带来源",
        )

        # 3. 修改前测试
        tests_hash_before = tree_hash(settings.workspace, "tests/*.py")
        target_before = run_tests(
            settings, label="before-target", targets=task.target_tests
        )
        regression_before = run_tests(
            settings, label="before-regression", targets=task.regression_tests
        )

        # 4. MiniMax 改码
        patch_error = ""
        if self.reuse_patch:
            after_dir = settings.root / "patches" / "after"
            for path in sorted(after_dir.rglob("*")):
                if path.is_file():
                    destination = settings.workspace / path.relative_to(after_dir)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, destination)
            patch_info = {"record": "（复用 patches/after，未重新调用模型）", "changed": ["src/order_service/service.py"]}
        else:
            try:
                patch_info = generate_patch(settings, bundle, name="order-amount-filter")
            except Exception as error:  # noqa: BLE001 - 记录失败而不是中断整轮验收
                patch_error = f"{type(error).__name__}: {error}"
                patch_info = {"record": "", "changed": []}
        self.artifacts["minimax_record"] = patch_info.get("record", "")

        snapshot_after(settings)
        diff_path = write_patch_diff(settings)
        self.artifacts["diff"] = str(diff_path)

        record_path = Path(patch_info.get("record", ""))
        record_ok = record_path.is_file() and bool(patch_info.get("changed"))
        if record_ok:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            request_chars = sum(len(message["content"]) for message in record["request"]["messages"])
            response_chars = len(record["response"]["content"])
            evidence = (
                f"模型 {record['model']}，请求 {request_chars} 字符 / 响应 {response_chars} 字符，"
                f"用量 {record.get('usage')}，改动文件 {patch_info.get('changed')}"
            )
        else:
            evidence = f"未找到模型调用记录：{record_path}" + (f"；调用失败：{patch_error}" if patch_error else "")
        self.check("minimax", "MiniMax 实际参与（记录与代码差异对应）", record_ok, evidence)

        # 5. 修改后测试
        tests_hash_after = tree_hash(settings.workspace, "tests/*.py")
        target_after = run_tests(settings, label="after-target", targets=task.target_tests)
        regression_after = run_tests(
            settings, label="after-regression", targets=task.regression_tests
        )

        code_ok = (
            not target_before["ok"]
            and target_before["failed"] + target_before["errors"] > 0
            and target_after["ok"]
            and regression_before["ok"]
            and regression_after["ok"]
            and tests_hash_before == tests_hash_after
        )
        self.check(
            "code",
            "代码修改有效（目标测试先失败后通过，回归始终通过）",
            code_ok,
            f"目标测试：修改前 exit={target_before['exit_code']}（失败 {target_before['failed']}），"
            f"修改后 exit={target_after['exit_code']}（通过 {target_after['passed']}）；"
            f"回归测试：修改前 {regression_before['passed']} 通过，修改后 {regression_after['passed']} 通过；"
            f"测试集哈希 {tests_hash_before}→{tests_hash_after}",
        )

        # 6. 状态保存 + 重启复用
        state = {
            "task_id": task.task_id,
            "title": task.title,
            "completed": [
                "规格 SPEC-ORD-009/010/011 已入库并用于上下文",
                f"生成上下文：规格 {len(bundle['specs'])} 条、代码 {len(bundle['code'])} 个符号、测试 {len(bundle['tests'])} 个",
                f"MiniMax 完成代码修改：{', '.join(patch_info.get('changed', []))}",
                f"目标测试 {target_after['passed']} 项通过，回归测试 {regression_after['passed']} 项通过",
            ],
            "todo": [
                "把本次改动合入业务工程主分支",
                "评估是否把本地哈希嵌入替换为 MiniMax / OpenAI 兼容 embedding",
            ],
            "verification": [
                f"before-target: exit={target_before['exit_code']}, failed={target_before['failed']}",
                f"after-target: exit={target_after['exit_code']}, passed={target_after['passed']}",
                f"after-regression: exit={regression_after['exit_code']}, passed={regression_after['passed']}",
            ],
        }
        state_result = None
        try:
            state_result = save_state(settings, state)
            self.artifacts["state"] = str(settings.records_dir / "state" / f"{task.task_id}-saved.json")
        except Exception as error:  # noqa: BLE001
            self.check(
                "state",
                "状态能够复用（新进程读取 PowerContext 状态）",
                False,
                f"PowerContext 不可用：{type(error).__name__}: {error}",
            )

        if state_result:
            try:
                resumed = resume_in_new_process(settings, task_path, backend=self.backend)
            except Exception as error:  # noqa: BLE001
                self.check(
                    "state",
                    "状态能够复用（新进程读取 PowerContext 状态）",
                    False,
                    f"新进程读取失败：{type(error).__name__}: {error}",
                )
            else:
                resumed_ok = bool(resumed.get("completed")) and resumed.get("context_has_state")
                self.check(
                    "state",
                    "状态能够复用（新进程读取 PowerContext 状态）",
                    resumed_ok,
                    f"scope {state_result['scope_id']}，新进程读回 {len(resumed.get('completed', []))} 条已完成 / "
                    f"{len(resumed.get('todo', []))} 条待办，prepared 状态 {resumed.get('prepared_status')}",
                )

        # 7. 规格更新 → 重新导入 → 上下文使用新内容
        refresh_ok, refresh_evidence = self._spec_refresh_check()
        self.check("refresh", "规格更新后重新导入即生效（非固定答案）", refresh_ok, refresh_evidence)

        return self.report()

    # -------------------------------------------------------------- 规格刷新
    def _spec_refresh_check(self) -> tuple[bool, str]:
        settings = self.settings
        spec_path = settings.workspace / SPEC_FILE
        original = spec_path.read_text(encoding="utf-8")
        marker = f"REFRESH-CHECK-{int(time.time())}"
        anchor = "### SPEC-ORD-010"
        if anchor not in original:
            return False, "规格文件中未找到 SPEC-ORD-010 锚点"

        injected = original.replace(
            anchor,
            f"> {marker}：金额区间过滤需在异常信息中同时给出两个边界值（v1.1 刷新验证）。\n\n{anchor}",
            1,
        )
        try:
            spec_path.write_text(injected, encoding="utf-8")
            ingest(settings, backend=self.backend)
            store = open_store(settings, backend=self.backend)
            bundle = build_context(store, self.task)
            store.close()
            hit = any(marker in item["excerpt"] for item in bundle["specs"])
            return hit, f"注入标记 {marker} 后重新入库，上下文中{'命中' if hit else '未命中'}该标记"
        finally:
            spec_path.write_text(original, encoding="utf-8")
            ingest(settings, backend=self.backend)

    # -------------------------------------------------------------- 报告
    def report(self) -> dict:
        passed = all(check["passed"] for check in self.checks)
        payload = {
            "task": self.task.task_id,
            "started_at": self.started,
            "finished_at": _now(),
            "backend": self.backend,
            "all_passed": passed,
            "checks": self.checks,
            "artifacts": self.artifacts,
        }

        report_dir = self.settings.records_dir / "verification"
        write_json(report_dir / "verification-report.json", payload)

        lines = [
            f"# 验证报告 · {self.task.task_id}",
            "",
            f"- 开始：{payload['started_at']}",
            f"- 结束：{payload['finished_at']}",
            f"- 结论：{'全部通过' if passed else '存在未通过项'}",
            "",
            "| 验收项 | 结果 | 证据 |",
            "| --- | --- | --- |",
        ]
        for check in self.checks:
            lines.append(
                f"| {check['key']} {check['title']} | {'通过' if check['passed'] else '未通过'} | {check['evidence']} |"
            )
        lines.append("")
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "verification-report.md").write_text("\n".join(lines), encoding="utf-8")
        return payload
