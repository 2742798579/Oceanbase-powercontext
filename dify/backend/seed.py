"""初始化 seekdb：写入预置旧账。

用法：
    python seed.py            # 只补齐预置旧账（幂等）
    python seed.py --reset    # 清空 lies / players 后重新写入

预置数据来源：本作品自拟（data/seed_lies.json），用于让新玩家第一次进店
就有可撞的旧账、可检索的账册。
"""

from __future__ import annotations

import json
import os
import sys

import seekdb_store as store_mod
from config import load_env_file
from embedding import build_embedding_from_env

load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


def main() -> int:
    reset = "--reset" in sys.argv
    ef, desc = build_embedding_from_env()
    store = store_mod.store_from_env(ef)
    print(f"[seed] embedding = {desc}")
    print(f"[seed] seekdb mode = {store.mode}")

    if reset:
        for name in (store_mod.LIES, store_mod.PLAYERS):
            if store.client.has_collection(name):
                store.client.delete_collection(name)
                print(f"[seed] dropped collection: {name}")
        store = store_mod.store_from_env(ef)

    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "data", "seed_lies.json"), encoding="utf-8") as fh:
        rows = json.load(fh)["lies"]

    existing = store.lies.count()
    if existing >= len(rows) and not reset:
        print(f"[seed] lies 已有 {existing} 条，跳过写入")
    else:
        for i, row in enumerate(rows):
            store.add_lie(
                pawn_id=f"SEED-{i + 1:03d}",
                lie=row["lie"],
                player_id=row["player_id"],
                player_name=row["player_name"],
                quote=int(row["quote"]),
                verdict=row["verdict"],
                tag=row["tag"],
                ts=int(row["ts"]),
            )
        print(f"[seed] 写入预置旧账 {len(rows)} 条")

    print(f"[seed] 完成：lies={store.lies.count()} players={store.players.count()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
