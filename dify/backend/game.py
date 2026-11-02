"""游戏规则层：估值、撞当判定、玩家画像、给模型的结构化上下文。

这里刻意把「估值」做成确定性算法而不是让模型随便报价，
一是保证同一句谎言反复典当价格稳定、可复现，
二是让模型只负责"说话"，规则和数值由后端与 seekdb 负责。
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

# --------------------------------------------------------------------- 词表
HYPERBOLE = ["所有", "永远", "从来", "全世界", "宇宙", "第一", "唯一", "一万", "百万", "亿", "整个", "没人", "谁都", "彻底"]
CONCRETE_MARK = ["公司", "学校", "小区", "楼下", "地铁", "医院", "银行", "食堂", "宿舍", "办公室", "老家", "胡同"]
CLICHE = ["我其实是个", "其实我是", "我认识", "我朋友", "我有一个朋友", "不是我吹", "说出来你可能不信"]
NUMERALS = set("0123456789一二三四五六七八九十百千万亿")

SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
GIVEN = ["青衫", "跛脚", "戴斗笠", "背算盘", "缺颗牙", "爱咳嗽", "左撇子", "穿木屐", "眉心有痣", "袖口带墨"]
ROLE = ["书生", "货郎", "更夫", "说书人", "木匠", "镖师", "账房", "画师", "游医", "戏子"]

MOODS = {
    "first": ("头回见", "把来客当肥羊打量，客气里带试探"),
    "regular": ("熟客", "语气松快，会主动翻旧账打趣"),
    "grudge": ("记仇", "皮笑肉不笑，明显记着上回被糊弄的事"),
    "wary": ("起疑", "每句话都要掂量三遍，懒得给好脸"),
}


def _h(text: str) -> int:
    return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)


def nickname(player_id: str) -> str:
    """由 player_id 稳定生成一个江湖花名。"""
    h = _h(player_id or "anon")
    return SURNAMES[h % len(SURNAMES)] + GIVEN[(h >> 5) % len(GIVEN)] + ROLE[(h >> 11) % len(ROLE)]


def appraise(lie: str) -> tuple[int, list[str]]:
    """给一句谎言估价，返回 (铜钱数, 理由列表)。"""
    lie = (lie or "").strip()
    quote = 120
    reasons: list[str] = ["开门价 120 文"]

    length_bonus = int(min(len(lie), 50) * 2.0)
    if length_bonus:
        quote += length_bonus
        reasons.append(f"篇幅 +{length_bonus}")

    if any(ch in NUMERALS for ch in lie):
        quote += 25
        reasons.append("有确切数目 +25")

    if any(mark in lie for mark in ("《", "「", "“", '"', "『")):
        quote += 20
        reasons.append("引经据典 +20")

    if any(mark in lie for mark in CONCRETE_MARK):
        quote += 15
        reasons.append("地点具体 +15")

    if "我" in lie:
        quote += 20
        reasons.append("亲历口吻 +20")

    hits = [w for w in HYPERBOLE if w in lie]
    if hits:
        bonus = min(90, 30 * len(hits))
        quote += bonus
        reasons.append(f"夸张词({'/'.join(hits[:3])}) +{bonus}")

    cliches = [w for w in CLICHE if w in lie]
    if cliches:
        penalty = 25 * len(cliches)
        quote -= penalty
        reasons.append(f"俗套开头({cliches[0]}) -{penalty}")

    jitter = _h(lie) % 25 - 12
    quote += jitter
    reasons.append(f"临场手感 {jitter:+d}")

    return max(10, min(999, quote)), reasons


def tag_of(lie: str) -> str:
    """给谎言打个粗分类标签，写进 metadata 供检索过滤。"""
    table = [
        ("动物", ["猫", "狗", "鸟", "鱼", "鸡", "鸭", "蛇", "牛", "马"]),
        ("财富", ["钱", "金", "银", "房", "车", "股票", "工资", "彩票"]),
        ("身份", ["我是", "老板", "局长", "明星", "冠军", "博士", "大师"]),
        ("能力", ["会", "能", "做到", "背", "算", "预测", "记得"]),
        ("超自然", ["鬼", "神", "仙", "穿越", "预知", "外星", "轮回"]),
    ]
    for name, words in table:
        if any(w in lie for w in words):
            return name
    return "杂项"


def mood_of(visits: int, caught: int) -> tuple[str, str]:
    if caught >= 3:
        return MOODS["grudge"]
    if visits >= 5:
        return MOODS["regular"]
    if visits == 0:
        return MOODS["first"]
    return MOODS["wary"]


def build_profile(
    name: str,
    visits: int,
    caught: int,
    wealth: int,
    last_lie: str,
    last_quote: int,
) -> str:
    """玩家档案的"记忆正文"，写进 players 集合，下次进店原样读回。"""
    mood_label, _ = mood_of(visits, caught)
    parts = [
        f"来客花名：{name}",
        f"累计来店 {visits} 次，被当场识破 {caught} 次，账上存有 {wealth} 文。",
    ]
    if last_lie:
        parts.append(f"上一次典当的是「{last_lie}」，估价 {last_quote} 文。")
    else:
        parts.append("此前没有典当记录。")
    parts.append(f"老板对其态度：{mood_label}。")
    return " ".join(parts)


def receipt_md(
    pawn_id: str,
    name: str,
    lie: str,
    quote: int,
    reasons: list[str],
    verdict: str,
    collision: dict[str, Any] | None,
    wealth: int,
) -> str:
    lines = [
        f"**当票 {pawn_id}**　持有人：{name}",
        "",
        f"> {lie}",
        "",
        f"- 估价：**{quote} 文**　（{('；'.join(reasons))}）",
        f"- 判定：**{verdict}**",
    ]
    if collision:
        lines.append(
            f"- 撞当：与 {collision['player_name']} 的「{collision['lie']}」"
            f"（语义相似度 {collision['similarity']:.2f}，字面重合 {collision.get('lexical', 0):.2f}）撞了，折价入库"
        )
    lines.append(f"- 账上余额：{wealth} 文")
    return "\n".join(lines)


def scene_for_llm(payload: dict[str, Any]) -> str:
    """拼给 LLM 的上下文块（纯文本，Dify 里作为变量注入）。"""
    p = payload["player"]
    lines = [
        "<scene>",
        f"时间：{time.strftime('%Y年%m月%d日 %H:%M')}，半夜十二点，雨。",
        f"地点：城南「不留」当铺。柜台上只有一盏油灯、一本翻烂的账册。",
        f"来客花名：{p['name']}（第 {p['visits']} 次上门，被识破 {p['caught']} 次，账上 {p['wealth']} 文）",
        f"老板态度：{p['mood_label']}——{p['mood_desc']}",
        "",
        "<memory>",
    ]
    if p.get("last_lie"):
        lines.append(f"上次他当的是：「{p['last_lie']}」，{p['last_quote']} 文。")
    else:
        lines.append("此人是生面孔，账册上没有他的记录。")
    if payload.get("similar"):
        lines.append("账册里语义相近的旧账：")
        for s in payload["similar"]:
            lines.append(f"  - 「{s['lie']}」（{s['player_name']}当的，{s['quote']} 文，相似度 {s['similarity']:.2f}）")
    lines.append("</memory>")
    lines.append("")
    lines.append("<this_turn>")
    lines.append(f"他这次开口：「{payload['lie']}」")
    lines.append(f"当票号：{payload['pawn_id']}；估价：{payload['quote']} 文；判定：{payload['verdict']}")
    if payload.get("collision"):
        c = payload["collision"]
        lines.append(
            f"撞当对象：「{c['lie']}」由 {c['player_name']} 所当，"
            f"语义相似度 {c['similarity']:.2f}、字面重合 {c.get('lexical', 0):.2f}，按规矩折价入库。"
        )
    lines.append("</this_turn>")
    lines.append("</scene>")
    return "\n".join(lines)
