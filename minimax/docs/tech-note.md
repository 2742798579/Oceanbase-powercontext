# 一页技术说明

## 1. 模块结构

```
任务定义 (task.json)
      │
      ├─► [seekdb] 56 条 chunk：16 规格 / 20 代码 / 20 测试
      │       向量检索（HNSW，384 维） + 全文检索（$contains）
      │                 │
      │                 ▼
      ├─► RRF 融合 → 分桶（规格 6 / 代码 4 / 测试 6）→ 加来源（文件:行号、规格编号）
      │                 │
      │                 ├─► 上下文 JSON / Markdown（可核对来源）
      │                 ▼
      │        MiniMax 提示词（系统提示 + 规格 + 代码 + 测试 + 历史状态）
      │                 │
      │                 ▼
      │        MiniMax → JSON{edits[path, content], spec_coverage} → 落地 → unified diff
      │                 │
      │                 ▼
      └─► pytest：目标测试（先失败后通过）+ 回归测试（始终通过）
                        │
                        ▼
              PowerContext：source 快照 + 3 条 Memory（completed/todo/verification）
                        │
                        ▼
              新进程 state-resume → /v1/context/prepare + /v1/memory/search → 注入上下文
```

## 2. 上下文选择方式

1. **切分**：规格按 `### SPEC-xxx-nnn` 标题切成独立条目（每条都是可核对的规格单元）；代码用
   `ast` 按模块 / 类 / 函数切分，chunk 头部写入 `symbol` 与 `lines`；测试按 `test_*` 函数切分，
   并从 docstring 中解析它声明的规格依据。
2. **双路召回**：任务标题 + 描述 + 关键词做向量检索（近邻 2×limit），同时用规格编号、符号名、
   关键词做 fulltext `$contains` 检索。
3. **融合排序**：RRF（k=60）合并多路排名；任务里显式声明的"必须包含"来源额外加权。
4. **分桶输出**：规格 / 代码 / 测试分别取 Top-N，保证三类信息都在，且每条都带真实来源。

为什么不全用向量：代码符号（`min_amount`）与规格编号（`SPEC-ORD-009`）是强精确匹配信号，
纯向量检索会漏；为什么不全用全文：自然语言任务描述无法直接命中"参数校验"这类语义条目。
RRF 让两路互补。

## 3. 技术分工与"实际参与"证据

| 技术 | 参与点 | 可核对证据 |
| --- | --- | --- |
| seekdb | 存 56 条工程资料；每次上下文构造执行约 67 次检索，命中结果进入上下文 | `records/db/ingest.json`、`records/context/*.json` 的 `store.query_log` |
| MiniMax | 生成整份 `service.py` 修改；输出里给出 `spec_coverage` | `records/minimax/*.json`（请求上下文、响应正文、usage、latency） |
| PowerContext | 写入 1 条 source 快照 + 3 条 Memory；新进程读回 | `records/state/*-saved.json`、`records/context/T1-*-resume*.json` |

## 4. 关键设计取舍

- **整文件替换而非 diff 应用**：模型输出的可执行性比"补丁美观"更重要，整文件写入失败率为 0；
  行为正确性由回归测试兜底。
- **本地哈希嵌入**：评审环境无需 embedding API 也能复现检索；换模型只改 `embedding.py`。
- **校验先于数据读取**（SPEC-ERR-004）：上下文里显式给出该规格，并用一个 spy 仓储测试验证，
  体现"规格 → 代码 → 测试"三者对齐。
- **状态与上下文分离**：seekdb 管"工程资料"，PowerContext 管"任务进展"，各司其职，避免把
  可变状态混进检索语料。
