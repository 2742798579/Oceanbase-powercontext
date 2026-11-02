"""下载中文语义向量模型（bge-small-zh-v1.5 的 ONNX 版本）。

默认走 hf-mirror.com（HuggingFace 国内镜像），约 95MB。下载后放在
data/models/bge-small-zh-v1.5/ 下，应用启动时会自动加载。

    python scripts/fetch_model.py
    python scripts/fetch_model.py --mirror https://huggingface.co   # 海外网络
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import MODEL_DIR  # noqa: E402

REPO = "Xenova/bge-small-zh-v1.5"
FILES = ["onnx/model.onnx", "tokenizer.json"]


def download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(tmp, "wb") as fh:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    print(f"\r  {target.name}: {pct}% ({done // 1048576}MB)", end="", flush=True)
    print()
    tmp.replace(target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mirror", default=os.environ.get("HF_ENDPOINT", "https://hf-mirror.com"))
    args = parser.parse_args()
    base = args.mirror.rstrip("/")

    print(f"模型目录: {MODEL_DIR}")
    for name in FILES:
        target = MODEL_DIR / Path(name).name
        if target.exists() and target.stat().st_size > 1024:
            print(f"  已存在，跳过 {target.name} ({target.stat().st_size // 1024}KB)")
            continue
        url = f"{base}/{REPO}/resolve/main/{name}"
        print(f"  下载 {url}")
        download(url, target)

    print("完成。启动应用时会自动加载语义模型。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
