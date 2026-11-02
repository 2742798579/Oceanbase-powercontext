"""全局配置。所有可调项都能用环境变量覆盖，方便评审按自己的环境复现。"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("RECALL_DATA_DIR", ROOT / "data")).resolve()
SAMPLE_DIR = DATA_DIR / "samples"


def _default_seekdb_path() -> str:
    """seekdb 嵌入式在 Windows 上打不开含非 ASCII 字符的目录（seekdb_open code=-1）。

    项目目录本身可能带中文（例如 D:/工作/...），所以默认值先试仓库内 data/seekdb，
    路径不是纯 ASCII 时退到 %LOCALAPPDATA%/KnowReturn/seekdb。
    """
    inside = DATA_DIR / "seekdb"
    if str(inside).isascii():
        return str(inside)
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "KnowReturn" / "seekdb"
    return str(base)


SEEKDB_PATH = os.environ.get("SEEKDB_PATH", _default_seekdb_path())
SEEKDB_DATABASE = os.environ.get("SEEKDB_DATABASE", "recall")

EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "384"))
MODEL_DIR = Path(
    os.environ.get("EMBEDDING_MODEL_DIR", DATA_DIR / "models" / "bge-small-zh-v1.5")
)

# PowerContext（记忆层）。留空 scope 时，程序会自动创建并缓存到 data/powercontext_scope.txt
POWERCONTEXT_URL = os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:8000").rstrip("/")
POWERCONTEXT_SCOPE = os.environ.get("POWERCONTEXT_SCOPE", "").strip()
POWERCONTEXT_TOKEN = os.environ.get("POWERCONTEXT_TOKEN", "").strip()
POWERCONTEXT_TIMEOUT = float(os.environ.get("POWERCONTEXT_TIMEOUT", "8"))

WEB_DIR = ROOT / "web"

# 检索参数
VECTOR_CANDIDATES = int(os.environ.get("RECALL_VECTOR_CANDIDATES", "20"))
FULLTEXT_CANDIDATES = int(os.environ.get("RECALL_FULLTEXT_CANDIDATES", "20"))
RRF_K = int(os.environ.get("RECALL_RRF_K", "60"))

# 记忆对排序的影响权重
COURSE_BOOST = float(os.environ.get("RECALL_COURSE_BOOST", "0.012"))
WEAK_TOPIC_BOOST = float(os.environ.get("RECALL_WEAK_TOPIC_BOOST", "0.008"))

DEFAULT_CARD_LIMIT = int(os.environ.get("RECALL_DEFAULT_CARD_LIMIT", "10"))
