"""测试执行与记录：命令、退出码、通过数、代码版本。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .minimax import code_version

PASSED_RE = re.compile(r"(\d+) passed")
FAILED_RE = re.compile(r"(\d+) failed")
ERROR_RE = re.compile(r"(\d+) error")


@dataclass
class TestRun:
    label: str
    command: str
    exit_code: int
    passed: int
    failed: int
    errors: int
    duration_s: float
    code_version: str
    log_path: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def as_dict(self) -> dict:
        return asdict(self) | {"ok": self.ok}


def run_pytest(
    workspace: Path,
    targets: list[str],
    *,
    label: str,
    log_dir: Path,
    code_version_value: str | None = None,
) -> TestRun:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{label}.log"
    command = [sys.executable, "-m", "pytest", *targets, "-q", "--tb=short", "-p", "no:cacheprovider"]

    started = time.time()
    completed = subprocess.run(
        command,
        cwd=str(workspace),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    duration = round(time.time() - started, 2)
    output = (completed.stdout or "") + (completed.stderr or "")

    header = (
        f"# command: {' '.join(command)}\n"
        f"# cwd: {workspace}\n"
        f"# code_version: {code_version_value or code_version(workspace)}\n"
        f"# exit_code: {completed.returncode}\n"
        f"# duration_s: {duration}\n\n"
    )
    log_path.write_text(header + output, encoding="utf-8")

    passed = int(PASSED_RE.search(output).group(1)) if PASSED_RE.search(output) else 0
    failed = int(FAILED_RE.search(output).group(1)) if FAILED_RE.search(output) else 0
    errors = int(ERROR_RE.search(output).group(1)) if ERROR_RE.search(output) else 0

    return TestRun(
        label=label,
        command=" ".join(command),
        exit_code=completed.returncode,
        passed=passed,
        failed=failed,
        errors=errors,
        duration_s=duration,
        code_version=code_version_value or code_version(workspace),
        log_path=str(log_path),
    )


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
