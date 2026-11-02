"""极简 .env 加载器（不引入 python-dotenv）。

只做三件事：跳过注释和空行、去掉包裹的引号、不覆盖已存在的环境变量。
在 app.py / seed.py 里最早期调用，保证 `cp .env.example .env` 之后直接
`python -m uvicorn app:app` 也能读到配置。
"""

from __future__ import annotations

import os


def load_env_file(path: str = ".env") -> None:
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.startswith("export "):
                key = key[7:].strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value
