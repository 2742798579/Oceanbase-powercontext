"""探测当前 seekdb 版本在 Windows 嵌入式模式下支持哪些能力。

只用一次，用来确认：VECTOR 类型、HNSW 向量索引、ik 全文索引、混合检索 SQL 的写法。
结果会写进 README 的「能力确认」一节。
"""

import os
import sys
import tempfile
import traceback

import pymysql
import pylibseekdb

DB = "probe"
# 注意：Windows 上 seekdb 打不开含中文的目录，这里固定用 ASCII 临时目录
PATH = os.path.join(tempfile.gettempdir(), "seekdb_probe")


def conn(db=None):
    handle = pylibseekdb.open(PATH)
    opts = handle.connection_options()
    return pymysql.connect(
        database=db, autocommit=True, cursorclass=pymysql.cursors.DictCursor, **opts
    )


def main() -> int:
    os.makedirs(PATH, exist_ok=True)
    print("pylibseekdb:", getattr(pylibseekdb, "__version__", "?"))
    c = conn()
    with c.cursor() as cur:
        cur.execute("SELECT VERSION() AS v")
        print("server version:", cur.fetchone()["v"])
        cur.execute(f"DROP DATABASE IF EXISTS {DB}")
        cur.execute(f"CREATE DATABASE {DB}")
    c.close()

    c = conn(DB)
    results = {}

    ddl = f"""
    CREATE TABLE chunks (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      doc_id VARCHAR(64) NOT NULL,
      course VARCHAR(64) NOT NULL,
      topic VARCHAR(128) NOT NULL,
      seq INT NOT NULL,
      content TEXT NOT NULL,
      embedding VECTOR(8) NOT NULL,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FULLTEXT INDEX ft_content (content) WITH PARSER ik,
      VECTOR INDEX vidx (embedding) WITH (DISTANCE=cosine, TYPE=hnsw, LIB=vsag),
      KEY idx_course (course)
    ) ORGANIZATION = HEAP
    """
    with c.cursor() as cur:
        try:
            cur.execute(ddl)
            results["ddl"] = "OK"
        except Exception as e:
            results["ddl"] = f"FAIL: {e}"
            print("DDL 失败，尝试简化版")
            cur.execute(
                """
                CREATE TABLE chunks (
                  id BIGINT PRIMARY KEY AUTO_INCREMENT,
                  doc_id VARCHAR(64) NOT NULL,
                  course VARCHAR(64) NOT NULL,
                  topic VARCHAR(128) NOT NULL,
                  seq INT NOT NULL,
                  content TEXT NOT NULL,
                  embedding VECTOR(8) NOT NULL
                )
                """
            )
            results["ddl"] = "OK(简化)"
    print("DDL:", results["ddl"])

    with c.cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO chunks (doc_id, course, topic, seq, content, embedding) VALUES "
                "('d1','os','页分裂',1,'B+ 树插入导致页分裂，索引维护成本上升','[0.9,0.1,0,0,0,0,0,0]'),"
                "('d1','os','缓存置换',2,'LRU 缓存置换算法淘汰最久未使用的页','[0.1,0.9,0,0,0,0,0,0]')"
            )
            results["insert_vector_literal"] = "OK"
        except Exception as e:
            results["insert_vector_literal"] = f"FAIL: {e}"
    print("INSERT 向量字面量:", results["insert_vector_literal"])

    with c.cursor() as cur:
        try:
            cur.execute(
                "SELECT id, cosine_distance(embedding, '[0.9,0.1,0,0,0,0,0,0]') AS d "
                "FROM chunks ORDER BY d APPROXIMATE LIMIT 2"
            )
            results["cosine_distance"] = f"OK {cur.fetchall()}"
        except Exception as e:
            results["cosine_distance"] = f"FAIL: {e}"
            try:
                cur.execute(
                    "SELECT id, l2_distance(embedding, '[0.9,0.1,0,0,0,0,0,0]') AS d "
                    "FROM chunks ORDER BY d APPROXIMATE LIMIT 2"
                )
                results["l2_distance"] = f"OK {cur.fetchall()}"
            except Exception as e2:
                results["l2_distance"] = f"FAIL: {e2}"
    print("向量距离:", results.get("cosine_distance"), "|", results.get("l2_distance"))

    with c.cursor() as cur:
        try:
            cur.execute("SELECT id, MATCH(content) AGAINST('页分裂') AS s FROM chunks")
            results["match_against"] = f"OK {cur.fetchall()}"
        except Exception as e:
            results["match_against"] = f"FAIL: {e}"
    print("全文检索:", results["match_against"])

    with c.cursor() as cur:
        try:
            cur.execute(
                "SELECT id, MATCH(content) AGAINST('页分裂') AS s, "
                "cosine_distance(embedding, '[0.9,0.1,0,0,0,0,0,0]') AS d "
                "FROM chunks WHERE course = 'os' AND MATCH(content) AGAINST('页分裂') "
                "ORDER BY d APPROXIMATE LIMIT 5"
            )
            results["hybrid_one_sql"] = f"OK {cur.fetchall()}"
        except Exception as e:
            results["hybrid_one_sql"] = f"FAIL: {e}"
    print("单条 SQL 混合检索:", results["hybrid_one_sql"])

    print("\n===== 汇总 =====")
    for k, v in results.items():
        print(f"{k}: {v}")
    c.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
