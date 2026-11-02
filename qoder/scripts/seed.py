"""把 data/samples 下的示例资料导入 seekdb。

    python scripts/seed.py            # 导入示例资料
    python scripts/seed.py --dir 某目录  # 导入指定目录下的 .md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import ingest  # noqa: E402
from app.config import SAMPLE_DIR  # noqa: E402
from app.store import SeekDB  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=str(SAMPLE_DIR))
    args = parser.parse_args()

    directory = Path(args.dir)
    if not directory.exists():
        print(f"目录不存在：{directory}")
        return 1

    results = ingest.ingest_dir(directory)
    for r in results:
        print(f"  {r['title']}（{r['course']}）-> {r['inserted']} 块")
    stats = SeekDB.instance().stats()
    print(f"合计：{stats['docs']} 篇资料 / {stats['chunks']} 个知识块")
    return 0


if __name__ == "__main__":
    sys.exit(main())
