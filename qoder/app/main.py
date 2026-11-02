"""知返 · HTTP 服务（FastAPI）。

前端是纯静态页面（web/），由本服务直接托管，评审只需要一条命令就能体验。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import coach, ingest
from .config import SAMPLE_DIR, WEB_DIR
from .memory import PROFILE_QUERY, PowerContextMemory
from .store import SeekDB

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s"
)
log = logging.getLogger("recall")

app = FastAPI(title="知返 KnowReturn", version="1.0.0")
memory = PowerContextMemory()


# ---------- 请求模型 ----------


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    k: int = 6
    course: str | None = None


class IngestRequest(BaseModel):
    title: str
    course: str = "未分类"
    tags: str = ""
    text: str


class GradeRequest(BaseModel):
    chunk_id: int
    known: bool


class PreferenceRequest(BaseModel):
    course: str | None = None
    card_limit: int | None = None


# ---------- 启动 ----------


@app.on_event("startup")
def _startup() -> None:
    store = SeekDB.instance()
    stats = store.stats()
    log.info("seekdb 就绪：%s 篇资料 / %s 个片段", stats["docs"], stats["chunks"])
    if stats["chunks"] == 0 and Path(SAMPLE_DIR).exists():
        log.info("检测到空库，自动导入 data/samples 下的示例资料")
        ingest.ingest_dir(Path(SAMPLE_DIR))
    health = memory.health()
    if health.get("ok"):
        log.info("PowerContext 就绪：%s", memory.base_url)
    else:
        log.warning("PowerContext 未连接，记忆功能降级：%s", health.get("error"))


# ---------- 基础 ----------


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "seekdb": {
            "path": os.environ.get("SEEKDB_PATH", "data/seekdb"),
            "database": SeekDB.instance().database,
            "distance_fn": SeekDB.instance().distance_fn,
            "stats": SeekDB.instance().stats(),
        },
        "powercontext": memory.health(),
    }


@app.get("/api/stats")
def stats() -> dict:
    return SeekDB.instance().stats()


@app.get("/api/library")
def library() -> dict:
    return {"documents": SeekDB.instance().documents()}


@app.post("/api/ingest")
def api_ingest(req: IngestRequest) -> dict:
    if not req.text.strip():
        raise HTTPException(400, "正文为空")
    return ingest.ingest_text(
        title=req.title.strip(),
        course=req.course.strip() or "未分类",
        text=req.text,
        tags=req.tags.strip(),
    )


@app.post("/api/seed")
def api_seed() -> dict:
    """重新导入 data/samples 下的示例资料，方便评审从干净状态开始验证。"""
    results = ingest.ingest_dir(Path(SAMPLE_DIR))
    return {"imported": results, "stats": SeekDB.instance().stats()}


# ---------- 检索 ----------


@app.post("/api/search")
def api_search(req: SearchRequest) -> dict:
    store = SeekDB.instance()
    # 行为参数来自全部 active 记忆（确定性，不受查询词影响）
    profile = memory.profile_directives()
    directives = profile["directives"]
    # 再按本次查询做一次带字节预算的上下文召回，展示给用户看
    prepared = memory.prepare(req.query, max_bytes=1500)

    outcome = store.search_hybrid(
        query=req.query,
        k=max(1, min(req.k, 20)),
        course=req.course,
        weak_topics=set(directives.get("weak_topics", [])),
        boost_course=directives.get("preferred_course"),
    )

    # 顺带跑一次「单条 SQL 混合检索」做对照，证明向量+全文+标量确实压在同一条语句里
    single_sql, single_rows = store.search_hybrid_one_sql(req.query, 5, req.course)
    outcome["debug"]["single_sql"] = single_sql
    outcome["debug"]["single_sql_hits"] = len(single_rows)

    outcome["memory"] = {
        "status": profile["status"],
        "entries": [
            {"text": e.get("text", ""), "kind": e.get("kind", "")}
            for e in profile.get("entries", [])
        ],
        "directives": directives,
        "prepared": {
            "status": prepared.get("status"),
            "content": prepared.get("content", ""),
        },
    }
    return outcome


# ---------- 复习教练 ----------


@app.get("/api/coach/queue")
def api_queue(limit: int | None = None, course: str | None = None) -> dict:
    return coach.build_queue(memory, limit=limit, course=course)


@app.post("/api/coach/grade")
def api_grade(req: GradeRequest) -> dict:
    try:
        return coach.grade(memory, req.chunk_id, req.known)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/profile")
def api_profile() -> dict:
    profile = memory.profile_directives()
    prepared = memory.prepare(PROFILE_QUERY, max_bytes=4000)
    return {
        "memory": prepared,
        "status": profile["status"],
        "entries": [
            {"text": e.get("text", ""), "kind": e.get("kind", ""), "state": e.get("state", "")}
            for e in profile.get("entries", [])
        ],
        "directives": profile["directives"],
        "weak_topics": SeekDB.instance().weak_topics(8),
    }


@app.post("/api/profile/preference")
def api_preference(req: PreferenceRequest) -> dict:
    try:
        return coach.save_preference(memory, req.course, req.card_limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ---------- 静态页面 ----------


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.exception_handler(Exception)
def _unhandled(request, exc):  # pragma: no cover
    log.exception("未处理异常")
    return JSONResponse({"error": str(exc)}, status_code=500)
