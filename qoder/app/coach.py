"""复习教练。

两条数据流在这里汇合：
  seekdb  ——  卡片从哪来（chunk 表）+ 谁该被复习（review_log 聚合出的薄弱主题）
  PowerContext  ——  这个学习者是谁（偏好、每日上限、历史薄弱主题），跨会话复用

卡片正面的生成方式是可解释的完形填空：把该块的「主题词」挖空，让学习者回忆，
背面就是原文和出处。不需要调用大模型，评审也不用配 API Key。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from .config import DEFAULT_CARD_LIMIT
from .memory import PROFILE_QUERY, PowerContextMemory
from .store import SeekDB

log = logging.getLogger("recall.coach")

BLANK = "＿＿＿＿"


def _front(topic: str, content: str) -> str:
    """完形填空：优先挖掉整个主题词，其次挖掉主题里能在正文找到的最长片段。"""
    if topic and topic in content:
        return content.replace(topic, BLANK, 1)
    for size in range(min(len(topic), 8), 1, -1):
        for i in range(len(topic) - size + 1):
            frag = topic[i : i + size]
            if frag.strip() and frag in content:
                return content.replace(frag, BLANK, 1)
    return f"【{topic}】请回忆这个知识点的要点"


def build_card(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": row["id"],
        "front": _front(row.get("topic", ""), row["content"]),
        "back": row["content"],
        "topic": row.get("topic", ""),
        "course": row.get("course", ""),
        "doc_title": row.get("doc_title", ""),
        "source_path": row.get("source_path", ""),
        "miss": int(row.get("miss") or 0),
        "hit": int(row.get("hit") or 0),
    }


def build_queue(
    memory: PowerContextMemory,
    limit: int | None = None,
    course: str | None = None,
    session_query: str = PROFILE_QUERY,
) -> dict[str, Any]:
    """生成今日复习队列。

    队列长度和课程优先级来自 PowerContext 召回的记忆，而不是写死的常量。
    """
    store = SeekDB.instance()
    profile = memory.profile_directives()
    directives = profile["directives"]
    prepared = memory.prepare(session_query, max_bytes=3000)

    effective_limit = limit or directives.get("card_limit") or DEFAULT_CARD_LIMIT
    effective_course = course or directives.get("preferred_course")

    rows = store.card_pool(limit=max(effective_limit * 3, 20), course=effective_course)
    weak = {t for t in directives.get("weak_topics", [])}
    cards = [build_card(r) for r in rows[:effective_limit]]
    for c in cards:
        c["from_memory_weak"] = c["topic"] in weak

    return {
        "cards": cards,
        "memory": {
            "status": profile["status"],
            "content": prepared.get("content", ""),
            "directives": directives,
            "entries": profile.get("entries", []),
            "applied": {
                "limit": effective_limit,
                "course": effective_course,
                "weak_topics": sorted(weak),
            },
        },
    }


def grade(
    memory: PowerContextMemory,
    chunk_id: int,
    known: bool,
) -> dict[str, Any]:
    """记录一次复习结果，并把它写进长期记忆。

    写之前先读一次：同一个薄弱点不重复写，只有「原本薄弱、这次答对」才记为进展，
    避免记忆里堆满重复条目。
    """
    store = SeekDB.instance()
    chunk = store.get_chunk(chunk_id)
    if chunk is None:
        raise KeyError(f"chunk {chunk_id} 不存在")

    store.log_review(chunk_id, chunk["topic"], chunk["course"], known)

    profile = memory.profile_directives()
    already_weak = set(profile["directives"]["weak_topics"])

    memory_write: dict[str, Any]
    try:
        if known and chunk["topic"] in already_weak:
            memory_write = {
                "action": "written",
                "text": f"复习进度：{date.today().isoformat()} 已掌握主题 {chunk['topic']}",
                "result": memory.remember(
                    f"复习进度：{date.today().isoformat()} 已掌握主题 {chunk['topic']}",
                    kind="next-step",
                    reason="原先记录为薄弱，本次复习答对",
                ),
            }
        elif known:
            memory_write = {"action": "skipped", "reason": "该主题不在薄弱列表里，无需记录进展"}
        elif chunk["topic"] in already_weak:
            memory_write = {"action": "skipped", "reason": "该主题已在薄弱列表里，不重复写入"}
        else:
            memory_write = {
                "action": "written",
                "text": f"薄弱主题：{chunk['topic']}",
                "result": memory.remember(
                    f"薄弱主题：{chunk['topic']}",
                    kind="fact",
                    reason="复习卡片标记为还不熟",
                ),
            }
    except Exception as exc:  # noqa: BLE001 - 记忆失败不应阻断复习流程
        log.warning("写入记忆失败: %s", exc)
        memory_write = {"action": "failed", "error": str(exc)}

    return {
        "chunk_id": chunk_id,
        "topic": chunk["topic"],
        "course": chunk["course"],
        "known": known,
        "memory_write": memory_write,
        "weak_topics": store.weak_topics(6),
    }


def save_preference(
    memory: PowerContextMemory, course: str | None, card_limit: int | None
) -> dict[str, Any]:
    """把学习偏好写进 PowerContext，供后续会话的检索排序和排队复用。

    改偏好是「替换」不是「追加」：先把旧的偏好条目 retire 掉，再写新的，
    否则召回时会同时看到新旧两条，排序权重就说不清了。
    """
    parts = []
    if course:
        parts.append(f"优先复习科目={course}")
    if card_limit:
        parts.append(f"每日卡片上限={card_limit}")
    if not parts:
        raise ValueError("course 与 card_limit 至少提供一个")
    text = "学习偏好：" + "；".join(parts)

    retired = []
    try:
        # retire 会推进 Memory revision，citation 立刻失效，所以必须一次一条、每次都重新读
        for _ in range(10):
            olds = [
                e
                for e in memory.list_entries()
                if str(e.get("text", "")).startswith("学习偏好：")
            ]
            if not olds:
                break
            memory.retire(olds[0]["citation"], reason="用户修改了学习偏好")
            retired.append(olds[0].get("text", ""))
    except Exception as exc:  # noqa: BLE001 - 清理失败不阻断写入
        log.warning("清理旧偏好失败: %s", exc)

    result = memory.remember(text, kind="constraint", reason="用户在设置页确认的偏好")
    return {"memory_text": text, "result": result, "retired": retired}
