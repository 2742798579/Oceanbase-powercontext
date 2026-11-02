"""「不留」当铺 —— Dify 的配套后端。

Dify 负责流程编排与模型调用，本服务负责规则、估值与 seekdb 读写。
所有接口都以 POST + JSON 调用，除 /v1/ledger、/v1/redeem 直接返回
给玩家看的 Markdown 文本（Dify 的 HTTP 节点取 body 即可直接输出）。

启动：uvicorn app:app --host 0.0.0.0 --port 8077
"""

from __future__ import annotations

import difflib
import os
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

import game
import seekdb_store as store_mod
from config import load_env_file
from embedding import build_embedding_from_env

load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

COLLISION_THRESHOLD = float(os.getenv("COLLISION_THRESHOLD", "0.62"))
REDEEM_RATE = float(os.getenv("REDEEM_RATE", "1.2"))
BACKEND_TOKEN = os.getenv("BACKEND_TOKEN", "").strip()

app = FastAPI(title="不留当铺 · 谎言典当", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_ef, EMBEDDING_DESC = build_embedding_from_env()
STORE = store_mod.store_from_env(_ef)


# ------------------------------------------------------------------ 鉴权
async def _check_token(request: Request) -> None:
    if not BACKEND_TOKEN:
        return
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("x-api-key", "")
    if token != BACKEND_TOKEN:
        raise HTTPException(status_code=401, detail="invalid token")


def _pid(raw: str | None) -> str:
    pid = (raw or "").strip()
    return pid or "guest"


# ------------------------------------------------------------------ 模型
class PawnIn(BaseModel):
    player_id: str | None = None
    lie: str = Field(default="", description="玩家这次要当掉的谎言")


class LedgerIn(BaseModel):
    player_id: str | None = None
    keyword: str | None = Field(default=None, description="可选：按关键词翻旧账")


class RedeemIn(BaseModel):
    player_id: str | None = None
    pawn_id: str | None = None


# ------------------------------------------------------------------ 接口
@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "embedding": EMBEDDING_DESC,
        "seekdb": STORE.stats(),
    }


@app.post("/v1/pawn")
async def pawn(body: PawnIn, request: Request) -> JSONResponse:
    await _check_token(request)
    store_mod.trace_reset()

    pid = _pid(body.player_id)
    lie = (body.lie or "").strip()
    if not lie:
        return JSONResponse({"error": "空口无凭，当铺不收空话"}, status_code=400)
    if len(lie) > 500:
        lie = lie[:500]

    name = game.nickname(pid)
    prev = STORE.get_player(pid) or {}
    visits = int(prev.get("visits", 0)) + 1
    caught = int(prev.get("caught", 0))
    wealth = int(prev.get("wealth", 0))
    last_lie = prev.get("last_lie", "")
    last_quote = int(prev.get("last_quote", 0))

    quote, reasons = game.appraise(lie)
    similar = STORE.similar_lies(lie, n=3)
    collision = None
    verdict = "照价入库"
    if similar:
        top = similar[0]
        # 语义相似度由 seekdb 向量检索给出；字面重合度作为兜底，
        # 保证在没有配置嵌入模型（hash 模式）时也能识别"几乎照抄"的旧账。
        lexical = round(difflib.SequenceMatcher(None, lie, top["lie"]).ratio(), 4)
        top["lexical"] = lexical
        if top["similarity"] >= COLLISION_THRESHOLD or lexical >= 0.9:
            collision = top
            quote = max(10, int(quote * 0.7))
            caught += 1
            verdict = "撞当·折价入库"
            reasons = reasons + [f"与旧账撞车，按七折入库（{quote} 文）"]

    pawn_id = store_mod.new_pawn_id()
    wealth += quote
    STORE.add_lie(
        pawn_id=pawn_id,
        lie=lie,
        player_id=pid,
        player_name=name,
        quote=quote,
        verdict=verdict,
        tag=game.tag_of(lie),
        ts=int(time.time()),
    )

    mood_label, mood_desc = game.mood_of(visits - 1, caught)
    profile = game.build_profile(name, visits, caught, wealth, lie, quote)
    STORE.save_player(
        pid,
        profile,
        {
            "player_id": pid,
            "nickname": name,
            "visits": visits,
            "caught": caught,
            "wealth": wealth,
            "last_lie": lie[:80],
            "last_quote": quote,
            "last_seen": int(time.time()),
        },
    )

    payload = {
        "player": {
            "name": name,
            "visits": visits,
            "caught": caught,
            "wealth": wealth,
            "mood_label": mood_label,
            "mood_desc": mood_desc,
            "last_lie": last_lie,
            "last_quote": last_quote,
        },
        "lie": lie,
        "pawn_id": pawn_id,
        "quote": quote,
        "verdict": verdict,
        "collision": collision,
        "similar": similar,
    }
    payload["scene"] = game.scene_for_llm(payload)
    payload["receipt"] = game.receipt_md(
        pawn_id, name, lie, quote, reasons, verdict, collision, wealth
    )
    payload["seekdb_trace"] = store_mod.trace_dump()
    return JSONResponse(payload)


@app.post("/v1/ledger")
async def ledger(body: LedgerIn, request: Request) -> PlainTextResponse:
    await _check_token(request)
    store_mod.trace_reset()
    pid = _pid(body.player_id)
    keyword = (body.keyword or "").strip()

    if keyword:
        rows = STORE.search_lies(keyword, n=6)
        if not rows:
            text = f"账册翻到「{keyword}」这一页，空的。老板把册子合上了：「没这号事。」"
        else:
            lines = [f"**账册 · 检索「{keyword}」**（全文 + 向量混合检索，共 {len(rows)} 条）", ""]
            for r in rows:
                lines.append(f"- `{r['pawn_id']}`　「{r['lie']}」　{r.get('quote', 0)} 文　— {r.get('player_name', '无名氏')}")
            text = "\n".join(lines)
    else:
        rows = STORE.player_lies(pid, limit=10)
        name = game.nickname(pid)
        if not rows:
            text = f"老板翻了三页账册，抬头看你：「{name}，你还没当过东西呢。」"
        else:
            total = sum(int(r.get("quote", 0)) for r in rows)
            lines = [f"**账册 · {name}**　共 {len(rows)} 笔，合计 {total} 文", ""]
            for r in rows:
                lines.append(
                    f"- `{r['pawn_id']}`　「{r['lie']}」　{r.get('quote', 0)} 文　{r.get('verdict', '')}"
                )
            text = "\n".join(lines)
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")


@app.post("/v1/redeem")
async def redeem(body: RedeemIn, request: Request) -> PlainTextResponse:
    await _check_token(request)
    store_mod.trace_reset()
    pid = _pid(body.player_id)
    pawn_id = (body.pawn_id or "").strip()

    row = None
    if pawn_id:
        row = STORE.get_lie(pawn_id)
        if row and row.get("player_id") != pid:
            row = None
    else:
        recent = STORE.player_lies(pid, limit=1)
        row = STORE.get_lie(recent[0]["pawn_id"]) if recent else None

    if not row:
        return PlainTextResponse(
            "老板把手一摊：「这张当票不在我这儿。你要是记不清号，说声『查账』我念给你听。」",
            media_type="text/markdown; charset=utf-8",
        )

    prev = STORE.get_player(pid) or {}
    wealth = int(prev.get("wealth", 0))
    quote = int(row.get("quote", 0))
    cost = int(round(quote * REDEEM_RATE))
    wealth -= cost

    name = game.nickname(pid)
    profile = game.build_profile(
        name,
        int(prev.get("visits", 0)),
        int(prev.get("caught", 0)),
        wealth,
        row["lie"],
        quote,
    )
    STORE.save_player(
        pid,
        profile,
        {
            "player_id": pid,
            "nickname": name,
            "visits": int(prev.get("visits", 0)),
            "caught": int(prev.get("caught", 0)),
            "wealth": wealth,
            "last_lie": row["lie"][:80],
            "last_quote": quote,
            "last_seen": int(time.time()),
        },
    )

    text = "\n".join(
        [
            f"**赎回 {row['pawn_id']}**",
            "",
            f"老板从柜底抽出那张当票，念了一遍：「{row['lie']}」",
            "",
            f"- 赎金：{cost} 文（原估价 {quote} 文，加两成利）",
            f"- 账上余额：{wealth} 文",
            "",
            "「话还你，我这儿当没听过。」他把当票凑到灯上烧了。",
        ]
    )
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")


@app.get("/v1/trace")
async def trace() -> dict[str, Any]:
    return {"trace": store_mod.trace_dump(), "seekdb": STORE.stats()}
