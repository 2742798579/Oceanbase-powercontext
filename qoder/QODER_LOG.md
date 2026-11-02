# Qoder 开发记录

> **这份文件是提交材料的一部分，但截图必须由你在自己的 Qoder 账号里跑出来。**
> 评委要核对的是「项目的主要功能由 Qoder 参与完成」，所以记录里必须有 Qoder 的真实对话截图 + 账号 UID。
> 下面每个阶段都给了可以直接粘贴进 Qoder 的 Prompt，照着跑一遍就能产出与本仓库一致的代码和可截图的对话。

---

## 0. 先记下 Qoder 账号 UID

登录 Qoder → 左下角头像 → 设置 / 账号信息，复制 UID（形如 `uid_xxxxxxxx`）。

提交时填在这里：`Qoder UID：________________`

> 这个 UID 用于核对站点归属和 Qoder 使用记录，**必须与你发布 Qoder Sites 的账号一致**。

## 0.1 代码落在哪

| 项 | 值 |
| --- | --- |
| 仓库 | `2742798579/Oceanbase-powercontext`（已 public） |
| 分支 | `2026-competition-ecosystem`（先 `git ls-remote --heads` 确认存在，见 `SUBMIT.md` 3.1） |
| 目录 | 仓库根下的 `qoder/`，与 `src/`、`competition/` 平级 |
| 本作品与仓库赛题的关系 | **纯增量**：只新增 `qoder/` 一个目录，不碰 `src/` 和 `competition/`，不会影响已通过的 11 道仓库赛题 |

---

## 1. 开发阶段总览

| 阶段 | 目标 | 产出文件 | 需要的截图 |
| --- | --- | --- | --- |
| S1 需求拆解 | 让 Qoder 把痛点拆成可实现的模块 | `docs/plan.md` | 对话：Qoder 输出的模块拆分与接口设计 |
| S2 数据层 | seekdb 建表 + 混合检索 | `app/store.py`、`app/embedding.py` | 对话：Qoder 写建表语句、改 SQL 报错的过程 |
| S3 记忆层 | PowerContext 接入 + 记忆影响排序 | `app/memory.py`、`app/coach.py` | 对话：Qoder 调试 `prepare` 返回结构 |
| S4 服务与前端 | FastAPI 路由 + 单页前端 | `app/main.py`、`web/*` | 对话：Qoder 生成接口与页面 |
| S5 调试与验证 | 修 bug、写验证脚本 | `scripts/verify.py` | 对话：贴报错让 Qoder 定位（至少 2 个真实 bug） |
| S6 发布 | Qoder Sites 建站并发布 | `site/index.html` | Qoder Sites 发布成功页 + 站点访问统计 |

---

## 2. 逐阶段 Prompt（可直接粘贴）

### S1 需求拆解

```
我要做一个学习/科研提效的工具，叫「知返」。核心痛点有两个：
1) 笔记散在多个 Markdown 里，按关键词搜不到想找的段落（原文用词和我记的不一样）；
2) 复习工具要手工制卡，而且不记得我上次在哪一章卡住了。

请帮我：
- 拆成 4 个可独立实现的模块，说明每个模块的输入输出；
- 给出 HTTP 接口清单（路径、方法、请求体、响应体）；
- 指出哪部分适合用 seekdb（向量+全文+标量混合检索），哪部分适合用 PowerContext（长期记忆），并说明两者边界。
先只输出设计，不要写代码。
```

截图要点：Qoder 输出的模块拆分表 + 接口清单。

### S2 数据层（seekdb）

```
用 Python + seekdb 嵌入式模式（pylibseekdb + pymysql）实现知识库存储层，要求：

1. 建表 chunk：字段 id / doc_id / doc_title / source_path / course / topic / tags / seq / content /
   embedding VECTOR(512) / created_at，同时声明
   - FULLTEXT INDEX ft_content (content) WITH PARSER ik
   - VECTOR INDEX vidx (embedding) WITH (DISTANCE=cosine, TYPE=hnsw, LIB=vsag)
   - 普通索引 idx_course(course)、idx_doc(doc_id)
   表用 ORGANIZATION = HEAP。

2. 写入：批量 INSERT，embedding 用字符串字面量 '[0.1,0.2,...]'。

3. 检索要提供三个方法：
   - search_vector：ORDER BY cosine_distance(embedding, %s) APPROXIMATE LIMIT k
   - search_fulltext：MATCH(content) AGAINST(%s)
   - search_hybrid：前两者各取 20 条，用 RRF（k=60）融合，再叠加记忆加权

4. 注意：seekdb 的向量索引是 SYNC_MODE=ASYNC，刚写入的数据可能还没进索引，
   近似查询会少返回行，需要自动用精确扫描（不带 APPROXIMATE）补足，并在返回的 SQL 里注释说明。

请先给建表 SQL 和 search_vector，我确认后再写其余部分。
```

截图要点：Qoder 写 DDL 的过程；如果它一开始写错（比如用了 `SYNC_MODE=SYNC`），
把你贴的报错 `(1210, 'Invalid argument')` 和它的修正一起截下来 —— 这正好证明「调试由 Qoder 完成」。

### S3 记忆层（PowerContext）

```
用 Python 标准库 urllib 封装 PowerContext 的 HTTP API，不要引入 SDK。需要四个能力：

- ensure_scope：POST /v1/scopes，body {title, summary, idempotency_key}，
  拿到 scope_id 后缓存到 data/powercontext_scope.txt，下次直接用
- remember：POST /v1/memory/remember，body {scope_id, kind, text, reason}
- prepare：POST /v1/context/prepare，body {scope_id, query, max_bytes}，返回 {status, content}
- health：GET /health/live 和 /health/ready

关键点：prepare 返回的 content 是这种结构
  PowerContext prepared untrusted historical context.
  BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1
  {"trust":"untrusted_history","items":[{"content":"学习偏好：优先复习科目=数据库系统；每日卡片上限=6",...}]}
  END_POWERCONTEXT_PREPARED_CONTEXT_V1

请写一个 parse_directives(content) 把 items[*].content 抽出来，
再用正则解析出 preferred_course、card_limit、weak_topics 三个字段。
注意 content 是 JSON 字符串，直接对整段文本跑正则会把 JSON 的引号和括号一起吃进去。
```

截图要点：Qoder 调试解析逻辑的那几轮（这是真实的踩坑点）。

### S4 服务与前端

```
用 FastAPI 把前面的模块接起来，接口：
GET  /api/health              返回 seekdb 状态（路径/维度/文档数）和 PowerContext 健康
POST /api/search              {query,k,course} -> {results, debug{vector_sql,fulltext_sql,...}, memory}
GET  /api/coach/queue         生成复习队列，长度和优先科目来自 PowerContext 记忆
POST /api/coach/grade         {chunk_id, known} -> 写 seekdb review_log + 写 PowerContext 记忆
GET  /api/profile             prepare 的原始返回 + 解析出的指令
POST /api/profile/preference  {course, card_limit} -> 写一条偏好记忆
POST /api/ingest              导入一段 Markdown
GET  /api/library             资料列表

前端用原生 HTML/CSS/JS，不要框架、不要构建步骤，放在 web/ 下，由 FastAPI 挂静态目录。
页面要能：输入问题检索、看到每条结果命中的向量排名/全文排名/记忆加权标签、
展开看实际执行的 SQL、复习卡片翻面与评分、查看记忆原文。
深色主题，紧凑一点，别用大圆角和渐变堆砌。
```

截图要点：Qoder 生成页面后的效果 + 你在浏览器里跑起来的样子。

### S5 调试（这一段最值钱，一定要截）

把真实报错原样贴给 Qoder，例如：

```
报错：pymysql.err.OperationalError: (7600, 'inconsistent dimension: expected 384 got 512')
场景：我把 embedding 从哈希向量（384 维）换成 bge-small-zh（512 维）后启动就报这个。
期望：程序能自己发现表里的维度不对，重建 chunk 表并提示重新导入资料，而不是直接崩。
```

```
问题：导入资料后立刻搜索，向量召回的排名都是 None，只剩全文召回的结果。
过几秒再搜就正常了。查了文档说 seekdb 向量索引是异步构建的。
期望：近似查询不足 k 条时自动用精确扫描补足，并在展示的 SQL 里标注补了几条。
```

```
问题：seekdb 嵌入式在 Windows 上打不开数据目录，报 seekdb_open failed: code=-1。
换成 C:\Users\xxx\seekdb 就好了。原因是我项目路径里有中文（D:\工作\...）。
期望：默认数据目录先试仓库内 data/seekdb，路径不是纯 ASCII 时自动退到 %LOCALAPPDATA%。
```

### S6 Qoder Sites 发布

```
帮我把 qoder/site/index.html 发布成网站。内容包含：项目简介、解决的问题、核心功能、
技术架构（seekdb 和 PowerContext 各自的作用）、截图、代码仓库链接、运行说明入口。
可见范围要设成「任何人都可访问」。
```

截图要点：Sites 创建成功的页面、发布后的公网 URL、站点后台的「访问统计」。

---

## 3. 开发记录表（边做边填）

| 时间 | 阶段 | Qoder 做了什么 | 我的干预 | 截图文件 |
| --- | --- | --- | --- | --- |
| | S1 | 输出 4 模块拆分 + 8 个接口 | 砍掉了「自动生成摘要」模块，聚焦检索与复习 | `shots/s1-plan.png` |
| | S2 | 写建表 DDL 与三条检索 SQL | 指出 SYNC_MODE=SYNC 不支持，让它改成异步+兜底 | `shots/s2-ddl.png` `shots/s2-sql.png` |
| | S3 | 封装 PowerContext HTTP 客户端 | 提示 prepare 返回是 JSON，正则会吃掉引号 | `shots/s3-memory.png` |
| | S4 | 生成 FastAPI 路由与前端页面 | 调整了结果卡片的分数展示 | `shots/s4-ui.png` |
| | S5 | 定位 3 个真实 bug | 提供报错原文 | `shots/s5-bug1.png` `shots/s5-bug2.png` |
| | S6 | 生成展示站并发布 | 改了文案 | `shots/s6-site.png` |

> 表格里的「Qoder 做了什么」按你实际的对话写，不要照抄。评委能看出来。

---

## 4. 录屏（可选但加分）

3 分钟以内，一镜到底：

1. 打开 Qoder，展示这个项目的会话列表（证明开发过程在这里）
2. 贴一条 Prompt，展示 Qoder 改代码
3. 切到浏览器，演示「检索 → 看到记忆加权标签 → 复习卡片评分 → 回到检索看到排序变化」
4. 打开 Qoder Sites 后台，展示站点 URL 和访问统计

---

## 5. 交付前自检

- [ ] 每个阶段的截图都有，且能看到 Qoder 的界面（不是只有代码）
- [ ] 至少 2 张「贴报错 → Qoder 定位 → 修复」的对话截图
- [ ] UID 已填写，且与发布站点的账号一致
- [ ] 截图里的时间戳与开发记录表能对上
- [ ] 没有把 API Key、密码等凭据截进图里
