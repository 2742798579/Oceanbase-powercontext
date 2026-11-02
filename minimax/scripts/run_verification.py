#!/usr/bin/env python
"""一键复现六项必做验收。

用法：
    python scripts/run_verification.py                 # 完整流程（会调用 MiniMax）
    python scripts/run_verification.py --reuse-patch    # 复用 patches/after，不调用模型
    python scripts/run_verification.py --backend memory # 本地无 seekdb 时冒烟（不用于验收）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sddctx.cli import main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main(["verify", *sys.argv[1:]]))
