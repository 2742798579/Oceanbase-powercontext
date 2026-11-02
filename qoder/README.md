# 知返 KnowReturn

> 把散落的笔记变成「找得到」也「记得住」的知识。
> Qoder × OceanBase 2026 大赛 · Qoder 赛题参赛作品

## 一、这个项目解决什么问题

备考和做科研的人都有两个重复劳动：

1. **找不回**。笔记散在十几个 Markdown 文件、PDF 摘录和聊天记录里。想找「B+ 树插入为什么会变慢」，
   用编辑器全文搜 `变慢` 搜不到（原文写的是「拖慢」），用关键词搜 `B+` 又只能搜到讲结构的章节。
   真正需要的是「按意思找」+「按字面找」同时生效。
2. **记不住**。找到了、看懂了，过两周又忘了。市面上的复习工具（Anki 等）要手工制卡，
   而且不记得「你上次在哪一章卡住了」，每次都从零开始。

知返把这两件事接成一条闭环：**资料进 seekdb → 混合检索找到出处 → 复习卡片回写薄弱点 → 下次检索和排队自动用上这些记忆。**

## 二、技术分工

| 组件 | 在本项目里具体做什么 | 可核对的位置 |
| --- | --- | --- |
| **seekdb**（嵌入式，pylibseekdb） | 一张 `chunk` 表同时承载正文、512 维向量、ik 全文索引和结构化字段（科目/主题/标签/出处）。检索时向量召回与全文召回各跑一条 SQL，再用 RRF 融合；`review_log` 表存每一次「记住了/还不熟」，薄弱主题是对它做 SQL 聚合得到的。 | 检索页可展开「查看实际执行的两条 seekdb SQL」；`scripts/verify.py` 会打印 SQL 与 Top3 |
| **PowerContext**（HTTP API） | 存三类长期记忆：学习偏好（`优先复习科目=…；每日卡片上限=…`）、薄弱主题、复习进度。每次检索前用 `POST /v1/context/prepare` 召回，解析后**直接改变检索排序权重和复习队列长度**。 | 「学习档案」页展示 `prepare` 返回的原始上下文；检索结果卡片上有「记忆薄弱点 xxx」的加权标签 |
| **Qoder** | 主要开发工作、调试、以及通过 Qoder Sites 发布项目网站 | `QODER_LOG.md` |

两者的边界：**seekdb 管「事实与检索」，PowerContext 管「这个学习者是谁」**。
seekdb 里的薄弱主题是统计出来的客观事实，PowerContext 里的薄弱主题是跨会话、可被人读懂和编辑的记忆条目。
检索排序同时用两者：统计口径决定「先复习什么」，记忆口径决定「给谁加权」。

## 三、运行环境

| 项 | 要求 | 说明 |
| --- | --- | --- |
| Python | 3.11+ | 已在 3.13.14 / Windows 11 实测 |
| 操作系统 | Windows / macOS / Linux | Windows 用 `pylibseekdb` 官方 win_amd64 wheel |
| seekdb | 无需单独安装 | `pylibseekdb` 嵌入式加载，数据落在本地目录 |
| PowerContext | 1.4.1 | 独立进程，默认 `http://127.0.0.1:8000` |
| 磁盘 | 约 400MB | 依赖 + 95MB 向量模型 |

## 四、从零启动

### 1. 装依赖

```bash
cd qoder
python -m venv .venv

# Windows
.venv\Scripts\python.exe -m pip install -r requirements.txt
# macOS / Linux
.venv/bin/python -m pip install -r requirements.txt
```

> 国内网络慢可以加镜像：`-i https://mirrors.aliyun.com/pypi/simple/`

### 2. 下载中文向量模型（约 95MB，只需一次）

```bash
.venv\Scripts\python.exe scripts\fetch_model.py
```

默认从 `hf-mirror.com` 下载 `BAAI/bge-small-zh-v1.5` 的 ONNX 版本到
`data/models/bge-small-zh-v1.5/`。海外网络可加 `--mirror https://huggingface.co`。

**跳过这一步也能跑**：应用会自动退回 384 维哈希向量（词面匹配），
功能完整但语义召回变弱。强制指定：`EMBEDDING_PROVIDER=hash|onnx`。

### 3. 启动 PowerContext（记忆层）

```bash
# 方式 A：官方推荐（macOS / Linux）
uv tool install --force "powercontext[cli,server]==1.4.1"

# 方式 B：从源码安装（Windows 推荐，见「已知问题」）
git clone https://github.com/oceanbase/powercontext.git
cd powercontext
python -m venv .venv
.venv/Scripts/pip.exe install -e ".[server]"
```

启动（两个终端，保持不关）：

```bash
# 终端 1
powercontext server run
# 默认监听 http://127.0.0.1:8000，SQLite 存储，不需要任何模型 API Key
```

确认就绪：

```bash
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
# {"status":"ready","checks":{"runtime":"ready","database":"ready",...}}
```

> Scope 不需要手工建。应用第一次调记忆接口时会自动
> `POST /v1/scopes` 创建，并把 `scope_id` 缓存到 `data/powercontext_scope.txt`。

### 4. 启动应用

```bash
# Windows 一键（含建虚拟环境、装依赖、下模型）
start.bat

# 或手动
.venv\Scripts\python.exe run.py            # http://127.0.0.1:8080
```

首次启动会自动导入 `data/samples/` 下的 3 篇示例资料（数据库/操作系统/机器学习各 5 个知识块）。

打开 <http://127.0.0.1:8080>，右上角两个状态灯都应该是绿色的：

```
seekdb 就绪 · 15 块 / 3 篇        PowerContext 已连接
```

如果第二个是红色「未连接」，说明第 3 步没起来 —— 检索功能仍可用，但记忆功能降级。

## 五、验证样例（3 分钟跑完）

完整步骤见 [`VERIFY.md`](VERIFY.md)，也可以直接跑脚本：

```bash
.venv\Scripts\python.exe scripts\verify.py
```

预期输出（节选，实际内容见运行结果）：

```
[PASS] 1. seekdb 嵌入式实例 + 建表
[PASS] 2. 导入示例资料
[PASS] 3. 混合检索 Top3
       查询：为什么 B+ 树插入会变慢
         #1 B+ 树索引的结构        (向量#1 全文#1)  score=0.03279
         #2 页分裂为什么会拖慢写入  (向量#2 全文#-)  score=0.01613
[PASS] 4. 写入复习事实 + 聚合薄弱主题
[PASS] 5. PowerContext 写入 -> 召回 -> 影响行为
结果：6 步，失败 0，跳过 0
```

> 库里有数据时第 2 步会显示 `[SKIP]`，末尾变成「跳过 1」。失败数必须是 0。

## 六、目录结构

```
qoder/                     ← 放在 powercontext 仓库根目录，与 src/、competition/ 平级
├── app/
│   ├── config.py       配置（全部可用环境变量覆盖）
│   ├── embedding.py    向量化：bge-small-zh ONNX / 哈希兜底
│   ├── store.py        seekdb 数据层：建表、写入、混合检索、聚合
│   ├── memory.py       PowerContext 客户端（纯标准库 HTTP）
│   ├── ingest.py       Markdown 切块导入
│   ├── coach.py        复习卡片生成与评分
│   └── main.py         FastAPI 路由
├── web/                前端（原生 JS，无需构建）
├── scripts/
│   ├── fetch_model.py  下载向量模型
│   ├── seed.py         导入示例资料
│   ├── verify.py       端到端验证（提交材料里的验证样例）
│   ├── check_submission.py  提测前自检（URL 格式 / 分支 / 文件齐全）
│   ├── shoot.js        批量截图（借本机 Edge，无需下载浏览器内核）
│   └── probe_seekdb.py seekdb 能力探测（确认索引/函数可用）
├── data/samples/       示例资料
├── run.py  start.bat  start.sh
├── requirements.txt  .env.example  .gitignore
├── README.md  VERIFY.md  QODER_LOG.md  SUBMIT.md
└── site/               Qoder Sites 展示站源码 + shots 截图
```

## 七、关键实现说明

### 7.1 一条 SQL 里的三种检索

`chunk` 表的建表语句同时声明了 `FULLTEXT INDEX ... WITH PARSER ik`、
`VECTOR INDEX ... WITH (DISTANCE=cosine, TYPE=hnsw, LIB=vsag)` 和普通二级索引：

```sql
SELECT id, MATCH(content) AGAINST('页分裂') AS ft_score,
       cosine_distance(embedding, '[...]') AS dist
FROM chunk
WHERE MATCH(content) AGAINST('页分裂') AND course = '数据库系统'
ORDER BY dist APPROXIMATE LIMIT 10;
```

向量相似度、全文匹配、标量过滤压在同一条语句、同一个执行计划里，不需要在应用层做 N+1 合并。
检索页底部可以展开看到每次查询真正执行的三条 SQL（两条候选召回 + 一条对照）。

### 7.2 为什么主路径用 RRF 而不是单条 SQL

把 `MATCH` 放进 `WHERE` 会先做全文过滤再排序，召回面被全文条件卡死：
问「为什么 B+ 树插入会变慢」时，最该出现的「页分裂」章节因为不含「变慢」二字会被直接过滤掉。
所以主路径是**向量召回 Top20 + 全文召回 Top20 → RRF 融合**，单条 SQL 版本保留下来做对照演示。

### 7.3 记忆如何真正影响结果

```
评分卡片「还不熟」
  → POST /v1/memory/remember  {"kind":"fact","text":"薄弱主题：页分裂"}
  → 下次检索 POST /v1/context/prepare {"query":"我的学习偏好、薄弱主题和复习进度"}
  → 解析出 weak_topics=["页分裂"]
  → search_hybrid(..., weak_topics=...) 给命中该主题的结果 +0.008 分
  → 结果卡片上出现「记忆薄弱点 页分裂」标签
```

偏好同理：`学习偏好：优先复习科目=数据库系统；每日卡片上限=6`
→ 解析成 `preferred_course` / `card_limit` → 检索给该科目加权、复习队列按 6 张截断。

写之前会先读一次，同一个薄弱点不重复写；只有「原本薄弱、这次答对」才记一条进展。

### 7.4 两个必须知道的 seekdb 行为

- **向量索引是异步构建的**（`SYNC_MODE=ASYNC`，且不支持改成 SYNC）。
  刚导入的资料可能还没进 HNSW，`ORDER BY ... APPROXIMATE` 会少返回行。
  代码里做了自动兜底：近似查询不足 k 条时用精确扫描补足，并在 SQL 展示里标注。
- **嵌入式模式打不开含非 ASCII 字符的目录**（`seekdb_open failed: code=-1`）。
  项目放在中文路径下时，数据目录会自动退到 `%LOCALAPPDATA%/KnowReturn/seekdb`，
  也可以显式指定 `SEEKDB_PATH=C:/seekdb-data/knowreturn`。

## 八、已知问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `uv tool install "powercontext[cli,server]"` 在 Windows 上失败，报 `Failed to build jieba==0.42.1` | jieba 0.42.1 只发布 sdist，在本机 setuptools 构建时报 `0xc0000005` | 用「方式 B 从源码装」；或先 `pip install jieba` 再装；或直接在 WSL/Linux 里跑 PowerContext |
| `seekdb_open failed: code=-1` | 数据目录路径含中文 | 见 7.4，指定 `SEEKDB_PATH` 为纯英文路径 |
| 首次检索结果里没有刚导入的资料 | HNSW 异步索引还没追平 | 已自动用精确扫描兜底；等几秒也会追平 |
| 换向量模型后启动报 `inconsistent dimension` | 表里的向量维度与当前模型不一致 | 已自动检测并重建 `chunk` 表，重建后重新导入资料即可 |
| 用 `scripts/verify.py` 时复习记录对不上 | 嵌入式 seekdb 同一份数据目录被服务进程和脚本进程同时**写** | 跑 CLI 脚本前先停掉 Web 服务，或只用网页操作；只读查询可以共存 |

## 九、复用与出处

- seekdb：<https://github.com/oceanbase/seekdb>（Apache-2.0）
- PowerContext：<https://github.com/oceanbase/powercontext>（Apache-2.0）
- 向量模型：`BAAI/bge-small-zh-v1.5`（MIT），ONNX 版本来自 `Xenova/bge-small-zh-v1.5`
- `data/samples/` 下的三篇笔记为本项目自行整理撰写，内容来自公开教材的通用知识点
