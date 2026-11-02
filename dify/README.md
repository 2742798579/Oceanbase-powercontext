# 不留当铺 · 谎言典当

> 赛题：Dify × OceanBase —— Just for Fun，用 Dify 做一个好玩的智能应用
> 技术选型：**Dify（编排）+ seekdb（账本存储 / 向量检索 / 混合检索）**

---

## 1. 这是什么

一间只收谎话的当铺。

你说一句谎，老板孟九给你估个价、开一张当票，把这句话记进账册。谎话越具体、越像亲历，越值钱；要是你编的这句正好撞上别人当过的旧账，按七折入库，还要被他当众说破。

**关键点在于他记性极好。** 你下次再来——哪怕开了个全新的对话——他仍然记得你上次当的是什么、被他识破过几次、账上还剩几文。

### 怎么玩

| 你说 | 会发生什么 |
| --- | --- |
| 任意一句谎（默认） | 估价 → 开当票 → 记进账册，老板说一段话点评 |
| `查账` | 列出你自己的全部当票与合计 |
| `查账 月亮` | 按关键词翻**整本**账册（全文 + 向量混合检索） |
| `赎回` | 把最近一张当票赎回来，加两成利 |

估值规则是确定性的（篇幅、数目、地点、亲历口吻加分，俗套开头扣分），同一句话反复当价格稳定；模型只负责"说话"，不负责定价——这样评审复现时结果可预期。

### 为什么 seekdb 是必需的，不是摆设

账册不是塞给模型让它"记"，而是真的落在 seekdb 里，每一步都要查库：

| 环节 | seekdb 能力 | 具体用法 |
| --- | --- | --- |
| 撞当判定 | **向量检索** | 新谎言 embedding 与历史当票做近邻检索，`1 - cosine_distance` 作为语义相似度，超阈值判定撞当 |
| 查账（带关键词） | **全文 + 向量 + RRF 融合** | `hybrid_search(query.where_document.$contains, knn, rank.rrf)`，字面命中与语义近邻一起排 |
| 查账（不带关键词） | **结构化条件过滤** | `get(where={"player_id": ...})` 按玩家取全部当票 |
| 熟客记忆 | **文档存储 + upsert** | 玩家档案正文写进 `players` 集合，下次进店原样读回，拼进给模型的上下文 |

三种检索能力都用上了，且都直接决定玩家看到的结果。

---

## 2. 架构与分工

```
┌──────────────┐   ① 玩家一句话
│   玩家        │ ─────────────────────────┐
└──────────────┘                          ▼
                              ┌────────────────────────┐
                              │  Dify Chatflow          │
                              │  start → if-else 分流   │
                              └───────┬────────────────┘
                     ┌────────────────┼──────────────────┐
                     ▼                ▼                  ▼
              查账分支           赎回分支            典当分支（主流程）
                     │                │                  │
                     └────────┬───────┴──────────────────┘
                              ▼  HTTP Request 节点
                    ┌──────────────────────────┐
                    │  配套后端 (FastAPI)       │
                    │  估值 / 撞当 / 画像 / 渲染 │
                    └───────────┬──────────────┘
                                ▼
                    ┌──────────────────────────┐
                    │  seekdb (pyseekdb)       │
                    │  lies / players          │
                    └──────────────────────────┘
                                ▲
                    典当分支返回后：Code 节点拆 JSON
                                ▼
                    ┌──────────────────────────┐
                    │  LLM 节点（孟九台词）      │
                    └───────────┬──────────────┘
                                ▼
                          Answer：台词 + 当票
```

- **Dify** 负责流程编排：输入处理、意图分流（两级 if-else）、调用后端（HTTP Request）、拆解返回值（Code）、模型调用（LLM）、结果输出（Answer）。
- **后端**负责规则与数据：估值、撞当判定、玩家画像、Markdown 渲染，以及全部 seekdb 读写。
- **seekdb** 负责存储与检索：当票账本、熟客档案、向量/全文/结构化检索。

之所以把规则放在后端而不是全塞进 Prompt：估值和撞当是**可复现的数值逻辑**，交给模型会每次不一样，评审没法核对。模型只做它擅长的事——把结构化事实说成人话。

---

## 3. 运行环境

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Dify | **1.9.x 及以上**（DSL 按 0.1.5 格式编写，Dify 对 ≤0.1.5 的 DSL 走兼容导入并自动推断依赖） | Docker Compose 部署，或 Dify Cloud |
| seekdb | **pyseekdb ≥ 1.4.0** + **pylibseekdb ≥ 1.4.0.post2** | 嵌入式模式，无需单独起服务 |
| Python | **3.11 ~ 3.13** | 3.14 会走 sentence-transformers 分支，未验证 |
| 操作系统 | Linux x86_64 / macOS arm64 / **Windows x86_64** | Windows 需要 pylibseekdb ≥ 1.4.0.post2（该版本起才有 win_amd64 wheel） |
| 模型服务 | 任意 OpenAI 兼容的 Chat 模型 | 用于 LLM 节点生成台词 |
| 嵌入模型 | 任意 OpenAI 兼容的 `/embeddings` | **强烈建议配置**，见 4.3 |

磁盘：seekdb 数据目录约 100–300 MB；依赖约 400 MB。

---

## 4. 从零跑起来

### 4.1 启动后端

```bash
cd dify/backend
python -m venv .venv

# Linux / macOS
source .venv/bin/activate
# Windows (PowerShell)
# .venv\Scripts\Activate.ps1

pip install -r requirements.txt
cp .env.example .env          # 按需修改，见 4.3
```

初始化数据（写入 10 条预置旧账，让新玩家第一次进店就有可撞的旧账）：

```bash
python seed.py                # 幂等；加 --reset 可清空重建
```

看到下面这行就说明 seekdb 已经真的写进去了：

```
[seed] 完成：lies=10 players=0
```

> **路径必须纯英文。** seekdb 嵌入式内核打不开含中文/非 ASCII 字符的目录
> （报 `seekdb_open failed: code=-1`）。如果本项目所在目录带中文，
> 程序会自动把数据目录改到系统临时目录并打印一行 `[warn]`；
> 想固定位置就在 `.env` 里设 `SEEKDB_PATH=D:\seekdb-data\pawnshop` 这样的纯英文绝对路径。

启动服务：

```bash
python -m uvicorn app:app --host 0.0.0.0 --port 8077
```

自检：

```bash
curl http://127.0.0.1:8077/health
```

```json
{"status":"ok","embedding":"...","seekdb":{"mode":"embedded","path":"D:\\seekdb-data\\pawnshop","dimension":512,"lies":10,"players":0}}
```

> 也可以直接跑 `scripts/start_backend.ps1`（Windows）或 `scripts/start_backend.sh`（Linux/macOS）。

### 4.2 导入 DSL

1. 打开 Dify → **工作室 / Studio** → **创建空白应用** → 选 **Chatflow**（先随便建一个，导入时会覆盖）。
2. 右上角 **⋯ → 导入 DSL 文件**，选择 `dsl/lie-pawnshop.chatflow.yml`。
3. 导入后打开应用，做两处配置：

   **① 绑定模型**：点开「孟九开口」这个 LLM 节点，把模型换成本地已配置的对话模型（DeepSeek / Qwen / GPT / 本地 Ollama 均可），温度建议 0.8–1.0。

   **② 确认后端地址**：DSL 里用环境变量 `BACKEND_BASE` 指向后端，默认值是
   ```
   http://host.docker.internal:8077
   ```
   - Dify 用 Docker 部署、后端跑在宿主机 → 保持默认即可（`host.docker.internal` 在 Docker Desktop 上可用；Linux 上若不通，改用宿主机内网 IP）。
   - Dify 与后端在同一个 compose 网络 → 改成 `http://<后端服务名>:8077`。
   - 若你的 Dify 版本不支持在 URL 里用环境变量，直接把三个 HTTP 节点的 URL 改成完整地址。

   位置：应用编排页左上角「环境变量」，或每个 HTTP 节点的 URL 字段。

4. 点「运行」，应用即可用。

### 4.3 嵌入模型（强烈建议配）

默认 `EMBEDDING_PROVIDER=hash` 是**离线降级模式**：不需要任何密钥就能把整条链路跑通，但它的向量没有真实语义能力，"撞当"主要靠字面重合兜底。

要让语义检索真正生效，在 `.env` 里换成任意 OpenAI 兼容的嵌入服务：

```dotenv
EMBEDDING_PROVIDER=openai
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_API_KEY=sk-xxxxxxxx          # 占位，请填自己的
EMBEDDING_MODEL=text-embedding-v3
EMBEDDING_DIMENSION=1024
```

其他常见选择：

| 服务 | EMBEDDING_BASE_URL | EMBEDDING_MODEL | 维度 |
| --- | --- | --- | --- |
| 阿里云百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `text-embedding-v3` | 1024 |
| 硅基流动 | `https://api.siliconflow.cn/v1` | `BAAI/bge-m3` | 1024 |
| 本地 Ollama | `http://localhost:11434/v1` | `bge-m3` | 1024 |
| OpenAI | `https://api.openai.com/v1` | `text-embedding-3-small` | 1536 |

> **换嵌入模型必须重建库**：`python seed.py --reset`。collection 的向量维度在建表时就定死了，改维度不重建会直接报维度不匹配。

### 4.4 外部依赖与网络

- 后端需要访问嵌入服务的 `/embeddings` 接口（仅在使用 `EMBEDDING_PROVIDER=openai` 时）。
- Dify 需要访问后端的 8077 端口。
- 不需要任何外网回调、不需要公网域名，纯内网即可。
- 若设了 `BACKEND_TOKEN`，需要在三个 HTTP 节点的 Header 里加 `Authorization: Bearer <token>`。

---

## 5. 目录结构

```
dify/
├── README.md                      # 本文件（运行说明）
├── SUBMIT.md                      # 提交步骤
├── dsl/
│   └── lie-pawnshop.chatflow.yml  # 唯一的 DSL 文件，直接导入
├── backend/
│   ├── app.py                     # FastAPI 入口：/v1/pawn /v1/ledger /v1/redeem
│   ├── game.py                    # 估值、撞当、玩家画像、上下文拼装
│   ├── seekdb_store.py            # seekdb 读写封装（向量/全文/结构化 + trace）
│   ├── embedding.py               # 嵌入函数（OpenAI 兼容 / 离线哈希）
│   ├── seed.py                    # 初始化预置旧账
│   ├── requirements.txt
│   ├── .env.example
│   └── data/seed_lies.json        # 10 条预置旧账（本作品自拟）
├── scripts/
│   ├── verify.py                  # 端到端验证脚本，产出运行日志
│   ├── validate_dsl.py            # DSL 静态校验（连通性/变量引用）
│   ├── check_repo.py              # 提测前自查（仓库/分支/dify 目录/报名 URL）
│   ├── start_backend.ps1
│   └── start_backend.sh
├── samples/
│   └── verification.md            # 验证样例（剧本 + 预期 + 实际结果）
└── docs/
    └── evidence/                  # 实际运行日志
```

---

## 6. 数据说明

- `backend/data/seed_lies.json` 里 10 条"旧账"为本作品自拟，用于让新玩家第一次进店就有可撞的旧账、可检索的账册。内容为虚构，不涉及真实人物。
- 玩家数据在首次交互时自动创建，无需预置。
- 恢复初始状态：停服务 → `python seed.py --reset` → 重启。所有玩家数据清空，只剩 10 条预置旧账。

## 7. 已知限制

1. **离线哈希嵌入没有语义能力**：默认模式下"撞当"依赖字面重合兜底，语义近邻质量差。配置真实嵌入模型后才是完整的语义检索体验。
2. **数据目录不能含中文**：seekdb 嵌入式内核在含非 ASCII 字符的路径下打不开库。程序会自动兜底到临时目录，但更稳妥的做法是显式指定一个纯英文 `SEEKDB_PATH`。
3. **单进程单实例**：seekdb 嵌入式模式下一个数据目录只能被一个进程打开，不支持多副本部署。需要并发/多实例时改用 `SEEKDB_MODE=server` 连服务端。
4. **估值规则是写死的**，不做模型微调；如果评审觉得价格曲线不合理，改 `game.py::appraise` 即可，不影响其他模块。
5. **DSL 未在真实 Dify 实例上做过导入验证**（开发环境没有 Docker），只做了静态结构校验（`scripts/validate_dsl.py`）。若导入报错，见 `SUBMIT.md` 末尾的排查清单。
6. 赎回允许余额为负（老板给记账），没有做负债上限。

## 8. 参考与致谢

- 玩法形态参考了赛题 2.4 节列出的 `dzh-gitcode/dify-workflows` 中《完蛋！我被 LLM 包围了！》《瞎说新语》等互动文字类 DSL 的思路（**未复用其任何 DSL 内容**，本作品 DSL 为从零编写）。
- seekdb 接口用法参考官方文档与 `pyseekdb` 1.4.0 的 API。
- Dify DSL 结构参考公开工作流导出格式。
