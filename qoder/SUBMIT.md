# 提交指南

> 赛题原文：<https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/uygu3273xzlrcmrm>
> 提交前以赛题页面为准，本文档把要求拆成可执行清单。
>
> **本文档已按你仓库的真实状态和之前踩过的坑校正过**，和第一版相比改了 4 处（见文末「本版修正」）。

---

## ⚠️ 先处理这件事：两个 PAT 已经泄露

你在对话里贴了两个 `github_pat_...`，而那个对话被分享成了**公网可访问的页面**
（我未登录就能拉到完整记录）。这两个 token 现在等于公开了。

立刻去 <https://github.com/settings/tokens> 把它们 **Revoke**，然后重新生成：

- 类型选 **Fine-grained**
- 只勾选目标仓库（一个 token 对一个仓库）
- 权限只要 **Contents: Read-only** + **Metadata: Read-only**

> 判题只需要「读得到代码」，只读权限足够。给写权限没有收益，只有风险。

---

## 一、你的仓库现状（已核对）

| 项 | 实际值 |
| --- | --- |
| 仓库 | `2742798579/Oceanbase-powercontext` |
| 可见性 | **public**（已从私有改过来） |
| 默认分支 | `2026-competition` |
| 已提交内容 | 11 道仓库赛题的实现，最近一次判分 150 / 240 |
| 本作品要放的位置 | 仓库根目录下的 `qoder/`，与 `src/`、`competition/` 平级 |

因为仓库已经是 public，组织方**不需要 token 就能读到代码**，这消掉了「私有仓库授权」这一整类风险。
剩下的风险只有两个：**URL 带凭据** 和 **commit/分支填错**。

---

## 二、六件提交物对照表

| # | 赛题要求 | 对应产物 | 状态 |
| --- | --- | --- | --- |
| 1 | 项目网站网址（任何人都可访问） | Qoder Sites 发布 `qoder/site/` | 待发布 |
| 2 | 代码仓库 | `2742798579/Oceanbase-powercontext` 的 `qoder/` 目录 | 待推送 |
| 3 | 运行说明 | `qoder/README.md` | ✅ |
| 4 | 验证样例 | `qoder/VERIFY.md` + `scripts/verify.py` + `verify_report.json` | ✅ |
| 5 | Qoder 开发记录 | `qoder/QODER_LOG.md` + 截图 | 待你在 Qoder 里补录 |
| 6 | 使用效果材料（鼓励） | 见第七节 | 待补 |

---

## 三、把代码推上去

### 3.1 确认分支名（这一步别跳）

赛题写的是基于 `2026-competition-ecosystem` 分支开发，但你的仓库默认分支是 `2026-competition`。
先确认那个分支到底在不在：

```bash
git clone https://github.com/2742798579/Oceanbase-powercontext.git
cd Oceanbase-powercontext
git ls-remote --heads origin | grep 2026
```

三种结果三种走法：

| 看到什么 | 怎么做 |
| --- | --- |
| 有 `2026-competition-ecosystem` | `git checkout 2026-competition-ecosystem`，直接往下走 |
| 只有 `2026-competition` 等分支 | 优先**问组委会**确认分支名；实在要自己来：`git checkout -b 2026-competition-ecosystem 2026-competition`，推上去，并在提交说明里注明 |
| 一个 2026 开头的都没有 | 说明导入没成功，先按《GitHub 在大赛中的使用说明》重新 Import |

### 3.2 放文件

```bash
# 把本仓库的 qoder/ 整个目录拷进去（不要带 .venv、data/seekdb、data/models）
cp -r /path/to/本项目/qoder ./qoder

# 确认没有脏东西被加进来
git status --short | head -20
```

`.gitignore` 已经写好了，会挡掉 `.venv/`、`data/seekdb/`、`data/models/`、
`data/powercontext_scope.txt`、`data/server.log`。**提交前一定 `git status` 看一眼。**

### 3.3 提交并推送

```bash
git add qoder
git commit -m "feat(qoder): 知返 —— 学习资料混合检索与复习教练"
git push origin 2026-competition-ecosystem

git rev-parse HEAD     # ← 这 40 位就是提测要填的 Commit Id
```

### 3.4 三个会把分弄没的操作，别做

1. **`push --force` / `rebase` / `squash`** —— 会把冻结基线提交挤掉，祖先关系断了直接判失败
2. **Download ZIP 重建仓库** —— 历史没了，`compare` 拿不到祖先关系
3. **提交成上游 `oceanbase/powercontext` 的地址** —— 那是官方仓库，不是你自己的

### 3.5 双分支保险（建议做，成本 1 分钟）

你的仓库**默认分支是 `2026-competition`**，而本作品在 `2026-competition-ecosystem`。
如果平台在「Branch 留空」时按默认分支解析，就会找不到 `qoder/` 目录。

`qoder/` 是纯增量目录，推到两个分支上互不影响，所以最稳的做法是两边都放：

```bash
# 已经在 2026-competition-ecosystem 上提交并推送后
git checkout 2026-competition
git checkout 2026-competition-ecosystem -- qoder
git add qoder
git commit -m "chore(qoder): mirror 知返 project assets for submission resolution"
git push origin 2026-competition
git checkout 2026-competition-ecosystem
```

这样无论平台按哪个分支取代码，都能在仓库根下看到 `qoder/`。

> 不要为此改默认分支——`2026-competition` 现在是仓库赛题（二~十一）的判分基准，
> 动它风险比收益大。

---

## 四、提测页怎么填（最容易搞混的地方）

平台上有**两个地方**都叫「仓库地址」，规则**相反**：

| 位置 | 填什么 | 规则 |
| --- | --- | --- |
| **参赛信息 / 报名登记** | `https://<用户名>:<新 token>@github.com/2742798579/Oceanbase-powercontext` | 官方使用说明要求带凭据（私有仓库场景） |
| **本题提交 URL** | `https://github.com/2742798579/Oceanbase-powercontext` | 赛题一明确**禁止带认证信息**，格式错算「有效失败」＝0 分 |

你之前就卡在这里：提测弹窗里的地址是从**报名登记**读出来的，页面内改不了。
好消息是仓库已经 public 了，所以：

**去报名登记把地址改成不带 token 的干净 URL**，然后回提测页刷新，地址栏会自动跟着变。

如果报名信息已经锁死改不了：联系组委会说明「仓库已转 public，请使用无凭据地址」，
**不要带着 token 提交**——那是确定性的 0 分风险，而公开仓库本来就不需要凭据。

### 本题（Qoder 赛题）的三个字段

| 字段 | 填什么 |
| --- | --- |
| 项目网站网址 | Qoder Sites 发布后的完整 URL（无痕窗口能打开） |
| 代码仓库地址 | `https://github.com/2742798579/Oceanbase-powercontext`（**不带 token、不带 /tree/、不带 .git**） |
| Qoder 账号 UID | `QODER_LOG.md` 里记录的那个，必须与发布站点的账号一致 |

> 仓库赛题（二~十一）要求填**真实 commit**；本题以人工评审为主，但平台若也要求
> Commit Id / Branch，就填 `2026-competition-ecosystem` 分支上的真实 HEAD，
> **千万别填不存在的 hash**——那会被判成版本解析失败，比留空更糟。

---

## 五、项目网站怎么发布（Qoder Sites）

1. Qoder → **Sites** → 新建站点，源目录用 `qoder/site/`
2. 内容应包含：项目简介、解决的问题、核心功能、技术架构（seekdb / PowerContext 分工）、
   截图、代码仓库链接、运行说明入口 —— 现在的 `site/index.html` 已经全都有了
3. **可见范围设「任何人都可访问」**（企业版账号设不了，必须个人账号）
4. 点「发布」，拿到公网 URL
5. 打开站点后台「访问统计」截图，后面「使用效果」要用

改过内容必须**再点一次「发布」**才生效；评审结束前别暂停或删除站点。

---

## 六、提交前自检（跑脚本，别靠眼睛）

```bash
# 1. 完整检查（需要能访问 github.com）
set GITHUB_TOKEN=github_pat_你的新token
.venv\Scripts\python.exe scripts\check_submission.py ^
    --repo-url "https://github.com/2742798579/Oceanbase-powercontext" ^
    --branch 2026-competition-ecosystem ^
    --commit <40 位 SHA> ^
    --site https://<你的站点地址>

# 2. 只想快速校验 URL 格式和本地文件
.venv\Scripts\python.exe scripts\check_submission.py ^
    --repo-url "https://github.com/2742798579/Oceanbase-powercontext" --offline
```

它会逐条打印 `[PASS] / [FAIL] / [SKIP]`，退出码 0 才建议提交。检查项：

```
1  提交 URL 格式（带 token / 端口 / query / 分支路径都会报错）
2  仓库存在且可读
3  不是官方上游本身
4  目标分支存在
5  commit 存在且在该分支上
6  qoder/ 目录下 12 个关键文件齐全
7  项目网站未登录可访问
8  本地文件齐全 + 没有把 .venv / 数据库 / 模型 / 凭据带进去
```

实测效果（拿你之前那个带 token 的地址跑）：

```
[FAIL] 1. 提交 URL 格式    包含 @（疑似把 token 拼进了 URL）；包含 token 字样
```

换成干净地址：

```
[PASS] 1. 提交 URL 格式    2742798579/Oceanbase-powercontext
```

### 还要手动做的两件事

- [ ] 浏览器**无痕窗口**打开项目网站，确认未登录能访问
- [ ] 无痕窗口打开 `https://github.com/2742798579/Oceanbase-powercontext/tree/2026-competition-ecosystem/qoder`
      确认 `qoder/` 目录和文件都在（这是评委看代码的入口）

---

## 七、使用效果材料（鼓励提交，有就加分）

| 材料 | 怎么拿 | 注意 |
| --- | --- | --- |
| 站点访问量 | Qoder Sites 后台「访问统计」截图 | 带时间范围 |
| 使用次数 | 发给 3~5 个同学各用一次检索 + 复习 | 「学习档案」页截图记忆条目增长 |
| 提效对比 | 自己计时：改前「翻 3 篇笔记找一段话」vs 改后「一次检索命中」 | 写清任务、几次取平均 |
| 用户反馈 | 3 个问题的问卷或聊天截图 | **隐去姓名、头像、联系方式** |

---

## 八、打包上传

```
知返-提交材料.zip
├── README.md            运行说明
├── VERIFY.md            验证样例
├── verify_report.json   verify.py 的实际输出
├── QODER_LOG.md         Qoder 开发记录
├── qoder-shots/         各阶段 Qoder 对话截图
├── demo-shots/          应用界面截图（site/shots 里的）
└── usage/               访问统计、反馈、提效对比（可选）
```

---

## 九、本版修正（相对第一版）

| # | 第一版写的 | 实际 | 已改成 |
| --- | --- | --- | --- |
| 1 | 「私有仓库，导入后获得同名分支」 | 你的仓库已经是 **public**，默认分支 `2026-competition` | 先 `ls-remote` 确认 `2026-competition-ecosystem` 是否存在，给了三种走法 |
| 2 | 只写「填仓库地址」 | 平台有**两个**仓库地址栏，规则相反；报名登记那份可能带 token | 单列第四节，并给出改 public 后的解法 |
| 3 | 「填 commit hash」 | 填不存在的 hash 会被判版本解析失败，比留空更糟 | 明确「必须真实存在」，并加了脚本第 5 项校验 |
| 4 | 靠人工自检 | 眼睛看不住 URL 格式和目录完整性 | 新增 `scripts/check_submission.py`，逐条 PASS/FAIL |

另外补了一条你没提但会致命的问题：**两个 PAT 已在公网页面泄露，必须先 Revoke。**
