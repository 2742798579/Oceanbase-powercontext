# 规有所依 · SDD 工程上下文管理系统（MiniMax 赛道）

面向软件持续演进的**规格驱动开发（SDD）上下文模块**：给定一个开发任务，模块从
**seekdb** 里检索出"本次任务适用的规格条目、需要修改的代码符号、用于验证的测试"，
把这份带来源的上下文交给 **MiniMax** 完成一次真实代码修改，跑通功能与回归测试，
再用 **PowerContext** 保存任务状态，并在模块重启后读回、继续使用。

业务工程是一个可运行的订单服务（`business/`），本次任务是为 `OrderService.list_orders`
增加**金额区间筛选**（闭区间、可与状态过滤叠加、非法区间抛 `InvalidAmountRangeError`）。

---

## 1. 技术分工（评审重点）

| 技术 | 在本作品中的实际作用 | 代码位置 |
| --- | --- | --- |
| **MiniMax** | 接收上下文，输出完整文件级补丁，直接落地为本次代码修改；每次调用落盘开发记录 | `sddctx/minimax.py`、`records/minimax/` |
| **seekdb（pyseekdb）** | 存储规格 / 代码 / 测试共 56 条 chunk（含 metadata），提供 HNSW 向量检索 + fulltext `$contains` 检索，检索结果直接构成上下文 | `sddctx/store.py`、`sddctx/corpus.py` |
| **PowerContext** | 保存"已完成 / 待办 / 验证结果"任务状态与状态快照 Source，重启后通过 `/v1/context/prepare` + `/v1/memory/search` 读回并注入上下文 | `sddctx/powercontext.py` |
| 本地哈希嵌入 | 让检索离线可复现（不依赖外部 embedding API）；替换为真实 embedding 只需改一个文件 | `sddctx/embedding.py` |

接入方式：seekdb 用 **Python SDK（pyseekdb 1.4.0.post1）**，PowerContext 用 **HTTP API（Server 1.2.0）**，
MiniMax 用 **OpenAI 兼容 HTTP 接口**。三者都没有只"装一下"，而是参与任务主流程。

---

## 2. 目录结构

```
minimax/
├── business/                     # 业务工程（可运行、带测试）
│   ├── specs/                    # 有效规格：SPEC-ORD-*、SPEC-ERR-*
│   ├── src/order_service/        # 被测代码
│   └── tests/                    # 回归测试 + 目标功能测试
├── sddctx/                       # 上下文模块（CLI 入口）
│   ├── corpus.py                 # 规格按编号切分 / 代码按 AST 符号切分 / 测试按函数切分
│   ├── embedding.py              # 本地确定性嵌入
│   ├── store.py                  # seekdb 存储与两类检索
│   ├── builder.py                # RRF 融合 + 分桶 + 来源标注 + 提示词构造
│   ├── minimax.py                # MiniMax 调用、记录落盘、补丁落地、diff
│   ├── powercontext.py           # 任务状态保存 / 读回
│   ├── runner.py                 # pytest 执行与日志（命令、退出码、代码版本）
│   ├── pipeline.py               # 各步骤编排
│   ├── verify.py                 # 六项必做验收自动执行 + 报告
│   └── cli.py                    # 调用入口
├── samples/verification/         # 验证样例（任务、期望来源、重置方法）
├── patches/                      # 修改前 / 后快照与 unified diff
├── records/                      # 运行证据：minimax / db / context / tests / state / verification
├── docs/                         # 技术说明、技术使用记录、提测步骤、上传清单
├── scripts/
│   ├── run_verification.py       # 一键复现六项验收
│   ├── build_usage_record.py     # 由 records/ 生成 docs/usage-record.md
│   ├── build_manifest.py         # 生成 docs/upload-manifest.md（上传清单）
│   ├── check_remote.py           # 上传后用 blob SHA 逐文件比对远端
│   └── push_via_api.py           # 无 git 环境下用 GitHub API 推送
├── .env.example                  # 配置模板（不含真实凭据）
└── requirements.txt / pyproject.toml
```

---

## 3. 环境要求

| 项 | 版本 / 说明 |
| --- | --- |
| Python | **3.11+**（开发验证用 3.13） |
| 操作系统 | **Linux x86_64 / macOS**（seekdb 嵌入式模式要求）；Windows 需用 seekdb server 模式 |
| seekdb | `pip install pyseekdb==1.4.0.post1`（Linux/macOS 会自动带 `pylibseekdb`） |
| PowerContext | `pip install "powercontext[cli,server]==1.2.0"` |
| MiniMax | 需要 API Key（比赛发放的 Token Plan 即可） |

---

## 4. 配置

```bash
cp .env.example .env
# 必填：MINIMAX_API_KEY
# 其余项默认值即可（Linux/macOS 用 seekdb 嵌入式模式）
```

`.env` 只放占位值，**不要提交真实凭据**；评审时用环境变量覆盖同名配置即可。

---

## 5. 快速开始（从干净环境复现）

```bash
# 0) 安装依赖
pip install -r requirements.txt          # 或：pip install -e . && pip install pytest
pip install "powercontext[cli,server]==1.2.0"

# 1) 启动 PowerContext Server（另开一个终端，保持运行）
powercontext server run                  # 默认 http://127.0.0.1:8000，SQLite 存储

# 2) 初始化数据：把规格 / 代码 / 测试导入 seekdb
python -m sddctx ingest

# 3) 看一次上下文（这一步就能核对"规格 / 代码 / 测试来源"）
python -m sddctx context

# 4) 一键跑完六项验收（会调用 MiniMax、改代码、跑测试、存状态、重启读状态）
python -m sddctx verify
# 或者
python scripts/run_verification.py
```

`verify` 会打印每一项 `[PASS]/[FAIL]` 与证据，并生成
`records/verification/verification-report.md`。退出码 0 表示六项全通过。

---

## 6. 调用入口（输入 / 输出）

入口是 CLI `python -m sddctx <command>`（也可 `pip install -e .` 后用 `sddctx <command>`）。

| 命令 | 输入 | 输出 |
| --- | --- | --- |
| `ingest` | `business/` 下的规格、源码、测试 | `records/db/ingest.json`（入库条数、分类、样例 ID） |
| `context [--with-state]` | 任务定义 `samples/verification/task.json` | 上下文 JSON + Markdown：适用规格、待改代码、验证测试、来源（文件:行号）、任务状态 |
| `patch` | 上一步的上下文 | `records/minimax/<时间戳>-*.json`（请求/响应/用量）、落地后的源码、`patches/*.diff` |
| `test --label --targets` | pytest 目标 | `records/tests/<label>.log`（命令、退出码、代码版本、通过/失败数） |
| `state-save --state` | 状态 JSON | PowerContext scope + Memory 写入回执（`records/state/*-saved.json`） |
| `state-resume` | 任务定义 | 新进程从 PowerContext 读回的状态 + 重建的上下文（`records/context/*`） |
| `verify` | 任务定义 | `records/verification/verification-report.{json,md}` |
| `restore` / `snapshot` | — | 恢复到修改前基线 / 生成前后快照 |

上下文输出示例（节选）：

```
## 适用规格（6 条）
### SPEC-ORD-009 金额区间过滤
来源：`specs/order-query-spec.md:45`（相关度 0.2711）
## 需要修改的代码（4 个符号）
- `OrderService.list_orders` — `src/order_service/service.py:24`
## 用于验证的测试（6 个）
- `test_filter_by_min_amount` — `tests/test_order_amount_filter.py:16` → SPEC-ORD-009
```

---

## 7. 测试

```bash
# 回归测试（修改前后都必须通过）
python -m pytest business/tests/test_order_query_regression.py -q
# 目标功能测试（修改前失败，修改后通过）
python -m pytest business/tests/test_order_amount_filter.py -q
```

修改前基线：`10 failed, 8 passed`；修改后：`18 passed`。
测试集在修改前后完全一致，未删除断言、未跳过用例、未修改预期值。

---

## 8. 验证样例

见 [`samples/verification/`](samples/verification/)：任务描述、必须命中的规格 / 代码 / 测试来源、
初始数据、操作步骤、预期结果与**重置方法**（`python -m sddctx restore` + 删除 `data/`）。

---

## 9. 提测

作品落在仓库 `2742798579/Oceanbase-powercontext` 的 **`2026-competition-ecosystem`** 分支的 `minimax/` 目录。
上传清单、拿 SHA、提测字段、0 分坑都在 [`docs/submission.md`](docs/submission.md)，
文件级清单在 [`docs/upload-manifest.md`](docs/upload-manifest.md)。

上传后自检（比对本地与远端，按 git blob SHA，不受换行差异影响）：

```bash
python scripts/check_remote.py
```

---

## 10. 已知限制

1. **seekdb 嵌入式模式不支持 Windows**（`pylibseekdb` 仅 Linux/macOS）。Windows 下需设置
   `SEEKDB_HOST/PORT/USER/PASSWORD` 使用 server 模式，或直接在 Linux 环境复现。
   本仓库因此**不附带 seekdb 运行日志**：在 Linux 上执行 `python -m sddctx verify` 会生成
   `records/db/ingest.json`、`records/context/*.json` 与 `database` 项的检索证据。
   代码路径按 pyseekdb 1.4.0.post1 的公开接口实现（`create_collection` / `add` / `query` / `get`）。
2. 本地嵌入是**哈希特征**（384 维、确定性），语义泛化能力弱于真实 embedding 模型；
   因为语料只有 56 条且配合 fulltext 检索 + RRF，本任务召回稳定。替换方式见 `sddctx/embedding.py` 头部注释。
3. `patch` 采用**整文件替换**落地（避免 search/replace 精确匹配失败），diff 由 `difflib` 生成；
   回归测试负责兜住"改坏别的行为"的风险。
4. PowerContext 状态复用只覆盖"任务状态"，不恢复完整 Agent 会话（题面 2.4 明确不要求）。
5. 规格刷新验证会在 `specs/order-query-spec.md` 临时注入标记，结束后自动还原并重新入库。
