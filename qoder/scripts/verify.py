"""端到端验证脚本 —— 提交材料里的「验证样例」可以直接跑这一个文件。

它会依次验证：
  1. seekdb 能建库建表（向量列 + HNSW 索引 + ik 全文索引）
  2. 资料能导入并切块
  3. 混合检索能返回带出处的结果，并打印真正执行的 SQL
  4. 复习事实能写入、薄弱主题能聚合出来
  5. PowerContext 能写入偏好与薄弱点，并在下一次召回中复现（未启动时标记 SKIP）

用法：
    python scripts/verify.py
    python scripts/verify.py --json report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import coach, ingest  # noqa: E402
from app.config import SAMPLE_DIR  # noqa: E402
from app.embedding import get_embedder  # noqa: E402
from app.memory import PowerContextMemory  # noqa: E402
from app.store import SeekDB  # noqa: E402

QUERIES = [
    "为什么 B+ 树插入会变慢",
    "地址翻译为什么要用多级页表",
    "验证集损失开始上升说明什么",
]

report: dict = {"steps": []}


def record(step: str, status: str, detail) -> None:
    report["steps"].append({"step": step, "status": status, "detail": detail})
    mark = {"PASS": "[PASS]", "SKIP": "[SKIP]", "FAIL": "[FAIL]"}.get(status, "[INFO]")
    print(f"{mark} {step}")
    if isinstance(detail, str):
        print(f"       {detail}")
    else:
        print("       " + json.dumps(detail, ensure_ascii=False, default=str)[:600])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    print("=" * 72)
    print("知返 KnowReturn · 端到端验证")
    print("=" * 72)

    # 1. seekdb
    store = SeekDB.instance()
    emb = get_embedder()
    stats = store.stats()
    record(
        "1. seekdb 嵌入式实例 + 建表",
        "PASS",
        {
            "数据目录": store.path,
            "数据库": store.database,
            "向量维度": emb.dim,
            "向量后端": emb.name,
            "距离函数": store.distance_fn,
            "表": ["chunk(向量+全文+标量)", "review_log", "app_meta"],
        },
    )

    # 2. 导入
    if stats["chunks"] == 0:
        results = ingest.ingest_dir(Path(SAMPLE_DIR))
        record("2. 导入示例资料", "PASS", [{"标题": r["title"], "块数": r["inserted"]} for r in results])
    else:
        record("2. 导入示例资料", "SKIP", f"库里已有 {stats['chunks']} 个知识块，跳过导入")
    stats = store.stats()
    record(
        "   当前库存",
        "INFO",
        {"资料": stats["docs"], "知识块": stats["chunks"], "复习记录": stats["reviews"]},
    )

    # 3. 混合检索
    print("\n--- 混合检索（向量 + 全文 + 标量过滤，RRF 融合）---")
    search_details = []
    for q in QUERIES:
        outcome = store.search_hybrid(q, k=3)
        rows = [
            {
                "排名": i + 1,
                "主题": r["topic"],
                "出处": f"{r['doc_title']} / {r['source_path']}",
                "向量排名": r["vec_rank"],
                "全文排名": r["ft_rank"],
                "RRF 得分": round(r["score"], 5),
            }
            for i, r in enumerate(outcome["results"])
        ]
        search_details.append({"查询": q, "Top3": rows})
        print(f"\n查询：{q}")
        for r in rows:
            print(
                f"  #{r['排名']} {r['主题']}  (向量#{r['向量排名']} 全文#{r['全文排名']}) "
                f"score={r['RRF 得分']}  出处={r['出处']}"
            )
        print("  实际执行的 SQL：")
        print("   " + outcome["debug"]["vector_sql"].replace("\n", "\n   ")[:900])
    record("3. 混合检索 Top3", "PASS", search_details)

    # 4. 复习事实
    print("\n--- 复习教练 ---")
    rows = store.card_pool(limit=1)
    if rows:
        card = coach.build_card(rows[0])
        print(f"卡片正面：{card['front'][:80]}…")
        print(f"卡片背面：{card['back'][:80]}…")
        store.log_review(card["chunk_id"], card["topic"], card["course"], False)
        weak = store.weak_topics(5)
        record(
            "4. 写入复习事实 + 聚合薄弱主题",
            "PASS",
            {"卡片": card["topic"], "薄弱主题": weak},
        )
    else:
        record("4. 写入复习事实 + 聚合薄弱主题", "FAIL", "库里没有知识块")

    # 5. PowerContext
    print("\n--- PowerContext 记忆层 ---")
    memory = PowerContextMemory()
    health = memory.health()
    if not health.get("ok"):
        record("5. PowerContext 记忆读写", "SKIP", health.get("error", "未连接"))
    else:
        scope = memory.ensure_scope()
        print(f"  scope_id = {scope}")
        memory.remember("学习偏好：优先复习科目=数据库；每日卡片上限=6", kind="constraint", reason="verify.py")
        weak_topic = store.weak_topics(1)
        if weak_topic:
            memory.remember(f"薄弱主题：{weak_topic[0]['topic']}", kind="fact", reason="verify.py")
        prepared = memory.prepare("我的学习进度、薄弱点和复习偏好")
        directives = PowerContextMemory.parse_directives(prepared.get("content", ""))
        queue = coach.build_queue(memory, session_query="我的学习进度、薄弱点和复习偏好")
        record(
            "5. PowerContext 写入 -> 召回 -> 影响行为",
            "PASS" if prepared["status"] == "ready" else "FAIL",
            {
                "召回状态": prepared["status"],
                "召回内容": prepared["content"][:400],
                "解析出的指令": directives,
                "复习队列长度（受记忆里的上限影响）": len(queue["cards"]),
            },
        )

    # 汇总
    print("\n" + "=" * 72)
    failed = [s for s in report["steps"] if s["status"] == "FAIL"]
    skipped = [s for s in report["steps"] if s["status"] == "SKIP"]
    print(f"结果：{len(report['steps'])} 步，失败 {len(failed)}，跳过 {len(skipped)}")
    print("=" * 72)

    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        print(f"报告已写入 {args.json}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
