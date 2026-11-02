"""端到端验证脚本（不依赖 Dify，直接打后端）。

它做两件事：
1. 按 samples/verification.md 里的剧本跑一遍完整交互；
2. 把每一步的真实返回、以及 seekdb 的读写 trace 落到 docs/evidence/ 下，
   作为「seekdb 实际发挥作用」的运行证据。

用法：
    python scripts/verify.py                     # 默认 http://127.0.0.1:8077
    python scripts/verify.py http://host:8077
"""

from __future__ import annotations

import json
import os
import sys
import time

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("BACKEND_BASE", "http://127.0.0.1:8077")).rstrip("/")
# 固定玩家标识，保证每次跑出来的花名、次数、余额一致，便于对照
PLAYER = os.getenv("VERIFY_PLAYER", "verify-demo")
CLIENT = httpx.Client(timeout=60.0)

lines: list[str] = []


def say(msg: str = "") -> None:
    print(msg)
    lines.append(msg)


def call(path: str, payload: dict, as_text: bool = False):
    resp = CLIENT.post(f"{BASE}{path}", json=payload)
    resp.raise_for_status()
    return resp.text if as_text else resp.json()


def step(title: str) -> None:
    say()
    say("=" * 78)
    say(f"# {title}")
    say("=" * 78)


def main() -> int:
    say(f"后端地址：{BASE}")
    say(f"测试玩家：{PLAYER}")

    step("0. 健康检查：确认 seekdb 已就绪、嵌入模式")
    health = CLIENT.get(f"{BASE}/health").json()
    say(json.dumps(health, ensure_ascii=False, indent=2))

    step("1. 首次进店，当掉一句原创谎言（账册里没有，应照价入库）")
    r1 = call("/v1/pawn", {"player_id": PLAYER, "lie": "我奶奶那把蒲扇一扇，整条巷子的暑气就退了三里地"})
    say(f"当票号：{r1['pawn_id']}")
    say(f"估价：{r1['quote']} 文　判定：{r1['verdict']}")
    say(f"熟客档案：{r1['player']['name']}，第 {r1['player']['visits']} 次，"
        f"被识破 {r1['player']['caught']} 次，账上 {r1['player']['wealth']} 文")
    say(f"老板态度：{r1['player']['mood_label']}——{r1['player']['mood_desc']}")
    say("向量检索召回（seekdb 语义比对）：")
    for s in r1["similar"]:
        say(f"  - {s['pawn_id']} 「{s['lie']}」 相似度 {s['similarity']:.3f}")

    step("2. 再当一句，和预置旧账几乎一样（验证撞当判定）")
    r2 = call("/v1/pawn", {"player_id": PLAYER, "lie": "我敲更的时候把月亮敲下来过，后来自己搬梯子挂回去了"})
    say(f"当票号：{r2['pawn_id']}　估价：{r2['quote']} 文　判定：{r2['verdict']}")
    if r2.get("collision"):
        c = r2["collision"]
        say(f"撞当对象：{c['pawn_id']}「{c['lie']}」（{c['player_name']}，相似度 {c['similarity']:.3f}）")
    else:
        say("本次未触发撞当（相似度未达阈值），属正常结果")
    say(f"当前账上：{r2['player']['wealth']} 文，被识破 {r2['player']['caught']} 次")

    step("3. 查账：列出该玩家的全部当票（seekdb 结构化条件查询）")
    say(call("/v1/ledger", {"player_id": PLAYER}, as_text=True))

    step("4. 查账 + 关键词：全文 + 向量混合检索（seekdb hybrid_search / RRF）")
    say(call("/v1/ledger", {"player_id": PLAYER, "keyword": "月亮"}, as_text=True))

    step("5. 赎回最近一张当票")
    say(call("/v1/redeem", {"player_id": PLAYER}, as_text=True))

    step("6. 换一个会话再来（同一个玩家标识）——验证跨会话记忆")
    say("说明：Dify 里 conversation_id 会随新会话变化，但 sys.user_id 不变；")
    say("后端按 player_id 读写 players 集合，所以老板仍然记得这位熟客。")
    r3 = call("/v1/pawn", {"player_id": PLAYER, "lie": "我家楼下的煎饼摊其实是我开的，摊主是我雇的"})
    memory_block = r3["scene"].split("<memory>")[1].split("</memory>")[0].strip() if "<memory>" in r3["scene"] else ""
    say("本次喂给模型的 <memory> 片段（即老板「记得」的内容）：")
    say(memory_block)
    say(f"估价：{r3['quote']} 文　判定：{r3['verdict']}　账上：{r3['player']['wealth']} 文")

    step("7. seekdb 读写 trace（本次典当的全部数据库操作）")
    for item in r3["seekdb_trace"]:
        op = item.get("op")
        target = item.get("target")
        rest = {k: v for k, v in item.items() if k not in {"op", "target", "ts"}}
        say(f"  [{op}] {target} {json.dumps(rest, ensure_ascii=False)[:160]}")

    step("8. 收尾统计")
    say(json.dumps(CLIENT.get(f"{BASE}/health").json(), ensure_ascii=False, indent=2))

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(here, "docs", "evidence")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"verify-{time.strftime('%Y%m%d-%H%M%S')}.log")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\n日志已写入：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
