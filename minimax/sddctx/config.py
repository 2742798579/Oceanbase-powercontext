"""配置加载：优先读环境变量，其次读 .env 文件（不覆盖已存在的环境变量）。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]


def load_env_file(path: Path) -> None:
    """极简 .env 解析，避免额外依赖。"""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    root: Path
    workspace: Path
    seekdb_path: Path
    seekdb_database: str
    collection: str
    embed_dim: int
    seekdb_host: str
    seekdb_port: int
    seekdb_tenant: str
    seekdb_user: str
    seekdb_password: str
    minimax_api_key: str
    minimax_base_url: str
    minimax_model: str
    minimax_temperature: float
    powercontext_url: str
    powercontext_token: str
    powercontext_scope: str
    powercontext_title: str
    records_dir: Path

    @property
    def has_minimax(self) -> bool:
        return bool(self.minimax_api_key)


def get_settings(env_file: Path | None = None) -> Settings:
    load_env_file(env_file or MODULE_ROOT / ".env")

    root = MODULE_ROOT
    workspace = Path(os.environ.get("SDD_WORKSPACE", str(root / "business"))).resolve()

    return Settings(
        root=root,
        workspace=workspace,
        seekdb_path=Path(os.environ.get("SEEKDB_PATH", str(root / "data" / "seekdb"))).resolve(),
        seekdb_database=os.environ.get("SEEKDB_DATABASE", "sdd_ctx"),
        collection=os.environ.get("SEEKDB_COLLECTION", "engineering_docs"),
        embed_dim=int(os.environ.get("SDD_EMBED_DIM", "384")),
        seekdb_host=os.environ.get("SEEKDB_HOST", ""),
        seekdb_port=int(os.environ.get("SEEKDB_PORT", "2881")),
        seekdb_tenant=os.environ.get("SEEKDB_TENANT", "sys"),
        seekdb_user=os.environ.get("SEEKDB_USER", "root"),
        seekdb_password=os.environ.get("SEEKDB_PASSWORD", ""),
        minimax_api_key=os.environ.get("MINIMAX_API_KEY", ""),
        minimax_base_url=os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/"),
        minimax_model=os.environ.get("MINIMAX_MODEL", "MiniMax-M2"),
        minimax_temperature=float(os.environ.get("MINIMAX_TEMPERATURE", "0.2")),
        powercontext_url=os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:8000").rstrip("/"),
        powercontext_token=os.environ.get("POWERCONTEXT_TOKEN", ""),
        powercontext_scope=os.environ.get("POWERCONTEXT_SCOPE_ID", ""),
        powercontext_title=os.environ.get("POWERCONTEXT_SCOPE_TITLE", "order-service SDD context"),
        records_dir=Path(os.environ.get("SDD_RECORDS_DIR", str(root / "records"))).resolve(),
    )
