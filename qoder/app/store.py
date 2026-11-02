"""seekdb 数据层。

这里承担两件事：
  1. 知识库：chunk 表把「正文 + 向量 + 全文索引 + 结构化字段（课程/主题/标签）」
     放在同一张表里，检索时一条 SQL 就能同时用上向量相似度、全文匹配和标量过滤。
  2. 复习事实：review_log 表记录每一次「记住了 / 还不熟」，薄弱主题是对它做聚合得到的。

seekdb 以嵌入式方式运行（pylibseekdb），数据落在 data/seekdb 目录，不需要单独起服务。
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

import pymysql
import pylibseekdb
from pymysql.cursors import DictCursor

from .config import SEEKDB_DATABASE, SEEKDB_PATH
from .embedding import embed, embedding_dim, get_embedder, to_vector_literal

log = logging.getLogger("recall.store")


class SeekDB:
    """嵌入式 seekdb 的单例封装。

    pylibseekdb 的运行时是进程级的，一个进程只 open 一次，退出时 close。
    """

    _instance: "SeekDB | None" = None
    _lock = threading.Lock()

    def __init__(self, path: str, database: str) -> None:
        self.path = path
        self.database = database
        self._handle = None
        self._options: dict[str, Any] = {}
        self.distance_fn = "cosine_distance"

    # ---------- 生命周期 ----------

    @classmethod
    def instance(cls) -> "SeekDB":
        with cls._lock:
            if cls._instance is None:
                inst = cls(SEEKDB_PATH, SEEKDB_DATABASE)
                inst.open()
                cls._instance = inst
            return cls._instance

    def open(self) -> None:
        import os

        os.makedirs(self.path, exist_ok=True)
        self._handle = pylibseekdb.open(self.path)
        self._options = dict(self._handle.connection_options())
        log.info("seekdb 已打开: %s", self.path)
        self._ensure_database()

    def close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            except Exception:  # pragma: no cover
                pass
            self._handle = None

    def _connect(self, database: str | None = None):
        return pymysql.connect(
            database=database,
            autocommit=True,
            cursorclass=DictCursor,
            **self._options,
        )

    # ---------- 建库建表 ----------

    def _ensure_database(self) -> None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS {self.database} "
                    "DEFAULT CHARACTER SET utf8mb4"
                )
        finally:
            conn.close()
        self.init_schema()

    def init_schema(self) -> None:
        conn = self._connect(self.database)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app_meta (
                      k VARCHAR(64) PRIMARY KEY,
                      v VARCHAR(255) NOT NULL
                    ) ORGANIZATION = HEAP
                    """
                )
                # 换了向量模型（维度不同）时，旧表里的向量无法复用，直接重建并提示重新导入
                want_dim = str(embedding_dim())
                actual_dim = self._existing_vector_dim(cur)
                if actual_dim and actual_dim != want_dim:
                    log.warning(
                        "向量维度从 %s 变为 %s（换了 embedding 模型），重建 chunk 表，请重新导入资料",
                        actual_dim,
                        want_dim,
                    )
                    cur.execute("DROP TABLE IF EXISTS chunk")
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS chunk (
                      id BIGINT PRIMARY KEY AUTO_INCREMENT,
                      doc_id VARCHAR(64) NOT NULL,
                      doc_title VARCHAR(255) NOT NULL,
                      source_path VARCHAR(512) NOT NULL,
                      course VARCHAR(64) NOT NULL,
                      topic VARCHAR(128) NOT NULL,
                      tags VARCHAR(255) NOT NULL,
                      seq INT NOT NULL,
                      content TEXT NOT NULL,
                      embedding VECTOR({embedding_dim()}) NOT NULL,
                      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      FULLTEXT INDEX ft_content (content) WITH PARSER ik,
                      VECTOR INDEX vidx (embedding)
                        WITH (DISTANCE=cosine, TYPE=hnsw, LIB=vsag),
                      KEY idx_course (course),
                      KEY idx_doc (doc_id)
                    ) ORGANIZATION = HEAP
                    """
                )
                cur.execute(
                    "INSERT INTO app_meta (k, v) VALUES ('embedding_dim', %s) "
                    "ON DUPLICATE KEY UPDATE v = VALUES(v)",
                    (want_dim,),
                )
                cur.execute(
                    "INSERT INTO app_meta (k, v) VALUES ('embedder', %s) "
                    "ON DUPLICATE KEY UPDATE v = VALUES(v)",
                    (get_embedder().name,),
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS review_log (
                      id BIGINT PRIMARY KEY AUTO_INCREMENT,
                      chunk_id BIGINT NOT NULL,
                      topic VARCHAR(128) NOT NULL,
                      course VARCHAR(64) NOT NULL,
                      known TINYINT NOT NULL,
                      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      KEY idx_topic (topic)
                    ) ORGANIZATION = HEAP
                    """
                )
        finally:
            conn.close()
        self._detect_distance_fn()

    @staticmethod
    def _existing_vector_dim(cur) -> str | None:
        """从 SHOW CREATE TABLE 里读出 chunk.embedding 的实际维度。"""
        try:
            cur.execute("SHOW CREATE TABLE chunk")
            row = cur.fetchone()
        except Exception:
            return None
        ddl = row.get("Create Table") or ""
        m = re.search(r"`embedding`\s+VECTOR\((\d+)\)", ddl, re.IGNORECASE)
        return m.group(1) if m else None

    def _detect_distance_fn(self) -> None:
        """不同版本的 seekdb 暴露的距离函数名略有差异，启动时探一次。"""
        conn = self._connect(self.database)
        try:
            with conn.cursor() as cur:
                for fn in ("cosine_distance", "l2_distance", "cosine_similarity"):
                    try:
                        cur.execute(
                            f"SELECT {fn}(embedding, embedding) AS d FROM chunk LIMIT 1"
                        )
                        cur.fetchall()
                        self.distance_fn = fn
                        break
                    except Exception:
                        continue
        finally:
            conn.close()
        log.info("向量距离函数: %s", self.distance_fn)

    # ---------- 写入 ----------

    def insert_chunks(self, doc: dict[str, Any], chunks: list[dict[str, Any]]) -> int:
        """doc: doc_id/doc_title/source_path/course/tags；chunks: topic/seq/content"""
        if not chunks:
            return 0
        conn = self._connect(self.database)
        try:
            with conn.cursor() as cur:
                rows = []
                for ch in chunks:
                    rows.append(
                        (
                            doc["doc_id"],
                            doc["doc_title"],
                            doc["source_path"],
                            doc["course"],
                            ch["topic"],
                            doc.get("tags", ""),
                            ch["seq"],
                            ch["content"],
                            to_vector_literal(embed(ch["content"])),
                        )
                    )
                cur.executemany(
                    "INSERT INTO chunk (doc_id, doc_title, source_path, course, topic, "
                    "tags, seq, content, embedding) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    rows,
                )
                return len(rows)
        finally:
            conn.close()

    def delete_doc(self, doc_id: str) -> int:
        conn = self._connect(self.database)
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chunk WHERE doc_id = %s", (doc_id,))
                return cur.rowcount
        finally:
            conn.close()

    def log_review(self, chunk_id: int, topic: str, course: str, known: bool) -> None:
        conn = self._connect(self.database)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO review_log (chunk_id, topic, course, known) "
                    "VALUES (%s,%s,%s,%s)",
                    (chunk_id, topic, course, 1 if known else 0),
                )
        finally:
            conn.close()

    # ---------- 检索 ----------

    def _filter_sql(self, course: str | None, doc_id: str | None) -> tuple[str, list[Any]]:
        clauses, params = [], []
        if course:
            # 模糊匹配：记忆里可能只写了「数据库」，库里存的是「数据库系统」
            clauses.append("course LIKE %s")
            params.append(f"%{course}%")
        if doc_id:
            clauses.append("doc_id = %s")
            params.append(doc_id)
        return (" AND ".join(clauses), params)

    def search_vector(
        self, query: str, k: int, course: str | None = None, doc_id: str | None = None
    ) -> tuple[str, list[dict[str, Any]], int]:
        """向量检索：优先走 HNSW 近似最近邻（`ORDER BY ... APPROXIMATE`）。

        seekdb 的向量索引默认 SYNC_MODE=ASYNC，刚写入的数据可能还没进索引；
        这种情况下近似查询会少返回行，再用精确扫描补足，保证「导入后立刻能搜到」。
        返回 (展示用 SQL, 行, 精确补足的行数)。
        """
        where, params = self._filter_sql(course, doc_id)
        vec_literal = to_vector_literal(embed(query, is_query=True))
        approx_sql = (
            f"SELECT id, doc_id, doc_title, source_path, course, topic, tags, seq, content, "
            f"       {self.distance_fn}(embedding, %s) AS dist "
            f"FROM chunk "
            f"{'WHERE ' + where + ' ' if where else ''}"
            f"ORDER BY dist APPROXIMATE LIMIT %s"
        )
        rows = self._query(approx_sql, [vec_literal, *params, k])

        fill_sql = ""
        filled = 0
        if len(rows) < k:
            seen = [r["id"] for r in rows]
            extra_where = list(where.split(" AND ")) if where else []
            extra_params = list(params)
            if seen:
                extra_where.append("id NOT IN (" + ",".join(["%s"] * len(seen)) + ")")
                extra_params.extend(seen)
            fill_sql = (
                f"SELECT id, doc_id, doc_title, source_path, course, topic, tags, seq, content, "
                f"       {self.distance_fn}(embedding, %s) AS dist "
                f"FROM chunk "
                f"{'WHERE ' + ' AND '.join(extra_where) + ' ' if extra_where else ''}"
                f"ORDER BY dist LIMIT %s"
            )
            extra = self._query(fill_sql, [vec_literal, *extra_params, k - len(rows)])
            rows.extend(extra)
            filled = len(extra)

        shown = approx_sql
        if filled:
            shown = (
                approx_sql
                + "\n-- 向量索引为异步构建，索引尚未追平时用精确扫描补足 "
                + str(filled)
                + " 行\n"
                + fill_sql
            )
        return self._render(shown, [vec_literal, *params, k]), rows, filled

    def search_fulltext(
        self, query: str, k: int, course: str | None = None, doc_id: str | None = None
    ) -> tuple[str, list[dict[str, Any]]]:
        """纯全文检索：ik 分词 + MATCH ... AGAINST。"""
        where, params = self._filter_sql(course, doc_id)
        sql = (
            f"SELECT id, doc_id, doc_title, source_path, course, topic, tags, seq, content, "
            f"       MATCH(content) AGAINST(%s) AS ft_score "
            f"FROM chunk "
            f"WHERE MATCH(content) AGAINST(%s)"
            f"{' AND ' + where if where else ''} "
            f"ORDER BY ft_score DESC LIMIT %s"
        )
        args = [query, query, *params, k]
        return self._render(sql, args), self._query(sql, args)

    def search_hybrid_one_sql(
        self, query: str, k: int, course: str | None = None
    ) -> tuple[str, list[dict[str, Any]]]:
        """演示 seekdb 的核心卖点：向量 + 全文 + 标量条件压在同一条 SQL 里。

        注意：把 MATCH 放进 WHERE 会先做全文过滤再排序，召回面偏窄；
        线上主路径用的是下面 `search_hybrid` 的 RRF 融合，这条 SQL 只用于对照展示。
        """
        where, params = self._filter_sql(course, None)
        sql = (
            f"SELECT id, doc_title, topic, content, "
            f"       MATCH(content) AGAINST(%s) AS ft_score, "
            f"       {self.distance_fn}(embedding, %s) AS dist "
            f"FROM chunk "
            f"WHERE MATCH(content) AGAINST(%s)"
            f"{' AND ' + where if where else ''} "
            f"ORDER BY dist APPROXIMATE LIMIT %s"
        )
        args = [query, to_vector_literal(embed(query, is_query=True)), query, *params, k]
        return self._render(sql, args), self._query(sql, args)

    def search_hybrid(
        self,
        query: str,
        k: int,
        course: str | None = None,
        weak_topics: set[str] | None = None,
        boost_course: str | None = None,
    ) -> dict[str, Any]:
        """主检索路径：向量召回 + 全文召回 -> RRF 融合 -> 记忆加权。"""
        from .config import (
            COURSE_BOOST,
            FULLTEXT_CANDIDATES,
            RRF_K,
            VECTOR_CANDIDATES,
            WEAK_TOPIC_BOOST,
        )

        vec_sql, vec_rows, exact_fill = self.search_vector(query, VECTOR_CANDIDATES, course)
        ft_sql, ft_rows = self.search_fulltext(query, FULLTEXT_CANDIDATES, course)

        fused: dict[int, dict[str, Any]] = {}
        for rank, row in enumerate(vec_rows, start=1):
            item = fused.setdefault(
                row["id"], {**row, "vec_rank": None, "ft_rank": None, "rrf": 0.0}
            )
            item["vec_rank"] = rank
            item["dist"] = float(row["dist"])
            item["rrf"] += 1.0 / (RRF_K + rank)
        for rank, row in enumerate(ft_rows, start=1):
            item = fused.setdefault(
                row["id"], {**row, "vec_rank": None, "ft_rank": None, "rrf": 0.0}
            )
            item["ft_rank"] = rank
            item["ft_score"] = float(row["ft_score"])
            item["rrf"] += 1.0 / (RRF_K + rank)

        weak_topics = weak_topics or set()
        applied: list[str] = []
        for item in fused.values():
            boost, reasons = 0.0, []
            if boost_course and boost_course in item["course"]:
                boost += COURSE_BOOST
                reasons.append(f"记忆偏好科目 {item['course']}")
            if item["topic"] in weak_topics:
                boost += WEAK_TOPIC_BOOST
                reasons.append(f"记忆薄弱点 {item['topic']}")
            item["memory_boost"] = round(boost, 4)
            item["memory_reasons"] = reasons
            item["score"] = round(item["rrf"] + boost, 6)
            if reasons:
                applied.extend(reasons)

        # 分数相同时优先「全文命中更靠前」的结果：说明查询词在原文里确实出现过
        ranked = sorted(
            fused.values(),
            key=lambda x: (-x["score"], x["ft_rank"] or 999, x["vec_rank"] or 999),
        )[:k]
        for item in ranked:
            item.pop("embedding", None)

        return {
            "results": ranked,
            "debug": {
                "vector_sql": vec_sql,
                "fulltext_sql": ft_sql,
                "vector_hits": len(vec_rows),
                "vector_exact_fill": exact_fill,
                "fulltext_hits": len(ft_rows),
                "fused_hits": len(fused),
                "distance_fn": self.distance_fn,
                "rrf_k": RRF_K,
                "memory_applied": sorted(set(applied)),
            },
        }

    # ---------- 复习事实 ----------

    def weak_topics(self, limit: int = 8) -> list[dict[str, Any]]:
        """薄弱主题 = 在 review_log 里「还不熟」次数多于「记住了」的主题。"""
        sql = (
            "SELECT topic, course, "
            "       SUM(CASE WHEN known = 0 THEN 1 ELSE 0 END) AS miss, "
            "       SUM(CASE WHEN known = 1 THEN 1 ELSE 0 END) AS hit "
            "FROM review_log GROUP BY topic, course "
            "HAVING miss > hit ORDER BY (miss - hit) DESC, miss DESC LIMIT %s"
        )
        rows = self._query(sql, (limit,))
        for r in rows:
            r["miss"] = int(r["miss"] or 0)
            r["hit"] = int(r["hit"] or 0)
        return rows

    def card_pool(self, limit: int = 20, course: str | None = None) -> list[dict[str, Any]]:
        """复习卡片池：优先取被标记过「还不熟」的 chunk，其余按最近写入补足。"""
        where, params = self._filter_sql(course, None)
        sql = (
            "SELECT c.id, c.doc_title, c.source_path, c.course, c.topic, c.content, "
            "       COALESCE(r.miss, 0) AS miss, COALESCE(r.hit, 0) AS hit "
            "FROM chunk c LEFT JOIN ("
            "  SELECT chunk_id, "
            "         SUM(CASE WHEN known = 0 THEN 1 ELSE 0 END) AS miss, "
            "         SUM(CASE WHEN known = 1 THEN 1 ELSE 0 END) AS hit "
            "  FROM review_log GROUP BY chunk_id"
            ") r ON r.chunk_id = c.id "
            f"{'WHERE ' + where + ' ' if where else ''}"
            "ORDER BY (COALESCE(r.miss,0) - COALESCE(r.hit,0)) DESC, c.id ASC LIMIT %s"
        )
        rows = self._query(sql, (*params, limit))
        for r in rows:
            r["miss"] = int(r["miss"] or 0)
            r["hit"] = int(r["hit"] or 0)
        return rows

    # ---------- 统计 ----------

    def stats(self) -> dict[str, Any]:
        docs = self._query(
            "SELECT COUNT(DISTINCT doc_id) AS docs, COUNT(*) AS chunks FROM chunk"
        )[0]
        reviews = self._query(
            "SELECT COUNT(*) AS reviews, "
            "SUM(CASE WHEN known = 0 THEN 1 ELSE 0 END) AS miss, "
            "SUM(CASE WHEN known = 1 THEN 1 ELSE 0 END) AS hit FROM review_log"
        )[0]
        courses = self._query(
            "SELECT course, COUNT(*) AS chunks FROM chunk GROUP BY course ORDER BY chunks DESC"
        )
        return {
            "docs": int(docs["docs"] or 0),
            "chunks": int(docs["chunks"] or 0),
            "reviews": int(reviews["reviews"] or 0),
            "miss": int(reviews["miss"] or 0),
            "hit": int(reviews["hit"] or 0),
            "courses": courses,
        }

    def documents(self) -> list[dict[str, Any]]:
        return self._query(
            "SELECT doc_id, doc_title, source_path, course, tags, COUNT(*) AS chunks "
            "FROM chunk GROUP BY doc_id, doc_title, source_path, course, tags "
            "ORDER BY MIN(id)"
        )

    def get_chunk(self, chunk_id: int) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT id, doc_id, doc_title, source_path, course, topic, content "
            "FROM chunk WHERE id = %s",
            (chunk_id,),
        )
        return rows[0] if rows else None

    # ---------- 工具 ----------

    def _query(self, sql: str, args: tuple | list = ()) -> list[dict[str, Any]]:
        conn = self._connect(self.database)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                return list(cur.fetchall())
        finally:
            conn.close()

    @staticmethod
    def _render(sql: str, args: list[Any]) -> str:
        """把 SQL 里的 %s 换成实际值，只用于在前端/日志里展示真实执行语句。"""
        rendered, idx = "", 0
        parts = sql.split("%s")
        for j, part in enumerate(parts):
            rendered += part
            if j < len(parts) - 1 and idx < len(args):
                value = args[idx]
                idx += 1
                if isinstance(value, str) and len(value) > 160:
                    rendered += f"'{value[:157]}...'"
                elif isinstance(value, str):
                    rendered += f"'{value}'"
                else:
                    rendered += str(value)
        return " ".join(rendered.split())
