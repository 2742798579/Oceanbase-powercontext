# 提交步骤

> 本文基于对你仓库的**实测**（2026-11-01），不是照着文档抄的。文末「实测记录」有原始结论。

---

## 0. 现状：你其实已经完成大半

| 事项 | 状态 |
| --- | --- |
| 私有/公开仓库导入 | ✅ 已导入 `2742798579/Oceanbase-powercontext`（Import 方式，历史完整） |
| 仓库可见性 | ⚠️ **现在是 public**（当初为了绕开"报名地址带 token"的冲突改的） |
| 默认分支 | ⚠️ `2026-competition`，**不是** Dify 赛题要的 `2026-competition-ecosystem` |
| 目标分支 `2026-competition-ecosystem` | ✅ 存在，SHA `d647275c30` |
| `dify/` 目录 | ⚠️ 只有 `.gitkeep`，**代码还没推上去** ← 唯一的硬缺口 |
| 两个 PAT | ✅ 都有效，各绑一个仓库（见文末） |

所以真正要做的只有一件事：**把本作品推到 `2026-competition-ecosystem` 分支的 `dify/` 下。**

先跑自查，看清楚当前状态：

```bash
cd dify
export GITHUB_TOKEN=<你的 powercontext token>     # Windows: set GITHUB_TOKEN=...
python scripts/check_repo.py --repo 2742798579/Oceanbase-powercontext \
    --url https://github.com/2742798579/Oceanbase-powercontext
```

推代码之前它会报 `[FAIL] ⑤ 作品目录有内容`，推完再跑一次应该全绿。

---

## 1. 推代码（两种方式，按你的 token 权限选）

**你的两个 PAT 都是 fine-grained、Contents: Read-only**，也就是说 `git push` 会被 403 拒绝。二选一：

### 方式 A：新建一个带写权限的 token（推荐，19 个文件一次推完）

1. `https://github.com/settings/tokens?type=beta` → **Generate new token**
2. Repository access → Only select repositories → 只勾 `Oceanbase-powercontext`
3. Permissions → Repository permissions → **Contents: Read and write**（Metadata 会自动带上 Read）
4. 生成后复制，然后：

```bash
# 注意本机 TLS 有吊销检查报错，加这个参数
git -c http.schannelCheckRevoke=false clone https://<用户名>:<写权限token>@github.com/2742798579/Oceanbase-powercontext.git
cd Oceanbase-powercontext

# 默认分支不是目标分支，必须切
git checkout 2026-competition-ecosystem

# 把本作品拷进 dify/（该目录现在只有 .gitkeep）
cp -r <本作品路径>/dify/. ./dify/
# Windows PowerShell: Copy-Item -Recurse -Force <本作品路径>\dify\* .\dify\

git add dify
git status --short          # 确认没混进 .env / .venv / 数据目录
git commit -m "feat(dify): 不留当铺·谎言典当 —— Dify + seekdb 互动文字游戏"
git push origin 2026-competition-ecosystem
```

推完拿 hash：

```bash
git rev-parse HEAD
```

### 方式 B：网页上传（不想再建 token）

1. 浏览器打开 `https://github.com/2742798579/Oceanbase-powercontext/tree/2026-competition-ecosystem/dify`
2. **Add file → Upload files**
3. 把本地 `dify/` 目录**里面的内容**拖进去（不是拖 `dify` 文件夹本身，否则会变成 `dify/dify/`）
4. Commit message 随便写，提交到 `2026-competition-ecosystem` 分支

> 网页上传有两个坑：① 隐藏文件（`.gitignore`、`.env.example`）可能被跳过，传完检查一遍；② 它会把你本地的 CRLF 转成 LF，导致文件哈希和本地不一致——这是正常的，不影响评审。

---

## 2. 报名地址必须是干净 URL

提测弹窗里的「代码仓库地址」**是从大赛报名登记读出来的，页面内改不了**。你现在仓库是 public，所以那里必须填：

```
https://github.com/2742798579/Oceanbase-powercontext
```

**不能**是 `https://2742798579:github_pat_xxx@github.com/...`。

理由：赛题规则明确「提交 URL 不含认证信息」，且「格式错误的 URL 属于有效失败」（直接 0 分）。而公开仓库本来就不需要 token 才能读，带 token 纯属自找风险。

如果报名登记里现在还带着 token：去**报名信息**页改（不是提测页），改完提测弹窗会自动刷新。

> 这也是当初把仓库改成 public 的原因——公开 + 干净 URL，两个条件同时满足，不用赌平台会不会对 URL 做脱敏。
>
> **不要为了"遵守《使用说明》里那句私有仓库"再改回 private。** 改回去就必须在报名地址里带 token，
> 而带 token 的 URL 明确属于「格式错误 → 有效失败」。两份文档在这里是冲突的，
> 公开 + 干净 URL 是唯一两边都不踩的组合（赛题一原文也写了「公开或私有本身不影响得分」）。

---

## 3. 提测

1. 大赛官网 `https://open.oceanbase.com/competition/` → 登录 → **提测平台**
2. 选 **Dify** 赛题
3. 填：

| 字段 | 填什么 |
| --- | --- |
| 代码仓库地址 | 自动带出，确认是干净 URL |
| Branch | `2026-competition-ecosystem` ← **必须手填，别用默认分支** |
| Commit Id | 第 1 步 `git rev-parse HEAD` 的完整 40 位 |

Commit Id 宁可留空，也别填一个不存在的 hash——那会被判成版本解析失败。

4. 提交后看平台反馈。可以反复提。

---

## 4. 提交前自查清单

```bash
python scripts/check_repo.py --repo 2742798579/Oceanbase-powercontext \
    --url https://github.com/2742798579/Oceanbase-powercontext
```

六项全 PASS（③ 默认分支不一致会给 WARN，属正常，注意提测时手填 Branch 即可）：

| 项 | 含义 |
| --- | --- |
| ① 仓库可读 | token 有权限 |
| ② 可见性 | public / private，决定报名地址要不要带凭据 |
| ③ 默认分支 | 只提醒，不是评分条件 |
| ④ 目标分支存在 | `2026-competition-ecosystem` 在 |
| ⑤ 作品目录有内容 | `dify/` 不是空的 |
| ⑥ 报名地址格式 | 无 token、无 query、无端口、无分支路径 |

另外手动确认：

- [ ] `dify/dsl/lie-pawnshop.chatflow.yml` 在
- [ ] `dify/README.md` 在
- [ ] 没有真实密钥：`git grep -nE "github_pat_|sk-[a-zA-Z0-9]{16,}" -- dify/` 无输出
- [ ] `dify/.env` 没被提交（`.gitignore` 已排除）

---

## 5. 本机环境已知问题

**TLS 吊销检查报错**（`CRYPT_E_NO_REVOCATION_CHECK` / `expected flush after ref listing`）：

```bash
# git
git -c http.schannelCheckRevoke=false clone ...
# curl
curl --ssl-no-revoke ...
```

这是本机证书策略问题，不是权限问题。`scripts/check_repo.py` 里已经默认带 `--ssl-no-revoke`，并且 curl 走不通时会自动回退到 urllib。

---

## 实测记录（2026-11-01）

**两个 PAT**

| Token | `/user` | 绑定仓库 | 类型 | 写权限 |
| --- | --- | --- | --- | --- |
| `…0V…`（powercontext） | 200，login=`2742798579` | Oceanbase-powercontext | fine-grained | ❌ 只读 |
| `…0X…`（seekdb） | 200，login=`2742798579` | Oceanbase-seekdb | fine-grained | ❌ 只读 |

写权限怎么测的：对仓库 POST 一个**已存在**的分支引用（`refs/heads/2026-competition`）。
有写权限只会返回 422「引用已存在」（不会真的改动），无写权限返回 403。
两个 token 都是 403 `Resource not accessible by personal access token`。

> 注意：`GET /repos/{owner}/{repo}` 返回的 `permissions.push=true` **不能**用来判断 token 权限——
> 那是你这个账号在该仓库的角色（你是 owner），不是 token 的作用域。别被它骗了。

**两个仓库**

| 仓库 | 可见性 | 默认分支 | 大小 | 最近推送 |
| --- | --- | --- | --- | --- |
| Oceanbase-powercontext | public | `2026-competition` | 138 MB | 2026-10-28 |
| Oceanbase-seekdb | public | `2026-competition` | 321 MB | 2026-10-26 |

**分支**

- `2026-competition` @ `20c6045795`（默认分支，Dify 赛题**不用**这个）
- `2026-competition-ecosystem` @ `d647275c30`（Dify 赛题目标分支，`dify/` 下只有 `.gitkeep`）

> 提醒：这两个 token 在聊天里明文出现过。比赛结束后去 `https://github.com/settings/tokens` 撤销。参赛信息里建议用重新生成的、只读的那一对。
