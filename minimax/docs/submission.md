# 提测步骤（MiniMax 赛道）

> 目标仓库与分支已实测确认，下面的路径、SHA、字段都按真实情况写，不含占位符。

## 0. 现状核对（2026-11-01 实测）

| 项 | 实际值 |
| --- | --- |
| 仓库 | `https://github.com/2742798579/Oceanbase-powercontext` |
| 可见性 | **public**（不需要 token 也能 clone / 组织方可直接读取） |
| 默认分支 | `2026-competition` |
| 生态赛道分支 | `2026-competition-ecosystem`，HEAD `d647275c308e`（`chore: initialize 2026 competition ecosystem directories`） |
| 该分支根目录 | `dify/`、`gaode/`、`minimax/`、`qoder/`、`stepfun/`，每个只有一个 `.gitkeep` |
| `minimax/` 现状 | **只有 `.gitkeep`，代码还没上传** |

所以本题要做的只有一件事：**把本地 `minimax/` 的全部文件传到 `2026-competition-ecosystem` 分支的 `minimax/` 目录**，然后提测。

## 1. 放什么 → 放到哪

```
本地 minimax/ 下的所有文件  →  分支 2026-competition-ecosystem 的 minimax/ 目录
```

完整清单见 [`upload-manifest.md`](upload-manifest.md)（当前 57 个文件，含每个文件的行数与 blob SHA）。
清单里的路径都**已经带 `minimax/` 前缀**，上传时不要再套一层；清单文件本身不必上传。

> ⚠️ 最常见的 0 分坑：在仓库根目录直接拖文件夹，GitHub 会把它放成 `minimax/minimax/`。
> 正确做法：先点进 `minimax/` 目录（URL 里出现 `/tree/2026-competition-ecosystem/minimax`），再拖。

## 2. 上传（三种方式，任选）

### 方式 A：网页拖拽（推荐，一次搞定）

1. 打开 `https://github.com/2742798579/Oceanbase-powercontext/tree/2026-competition-ecosystem/minimax`
2. 确认左上角分支选择器显示的是 **`2026-competition-ecosystem`**
3. 点右上 `Add file` → `Upload files`
4. 把本地 `minimax/` 里的**内容**（不是文件夹本身）整体拖进去：可以一次拖多个子目录（`business/`、`sddctx/`、`docs/`…）
5. 底部 Commit message 填：`feat(minimax): SDD context module (seekdb + PowerContext + MiniMax)`
6. `Commit changes` → 等进度条走完

### 方式 B：逐个补传

漏了哪个文件就进对应目录 → `Add file` → `Upload files` → 只拖那一个 → 文件名必须原样（不能变成 `xxx (1).py`）。

### 方式 C：一条命令（GitHub API，不用本机 git）

本机 git 走 HTTPS 有 TLS 吊销检查问题（`CRYPT_E_NO_REVOCATION_CHECK`），命令行 clone/push 容易失败，
所以提供脚本走 API：

```bash
# token 需要该仓库的 Contents: Read and write
export GITHUB_TOKEN=<你的 powercontext token>
python scripts/push_via_api.py --dry-run   # 先看会提交哪些文件
python scripts/push_via_api.py --yes       # 真正创建 1 个 commit 并更新分支
```

脚本会把 CRLF 归一化成 LF（与网页上传一致），并打印最终 40 位 SHA。

## 3. 拿 SHA

上传/推送完成后，打开
`https://github.com/2742798579/Oceanbase-powercontext/commits/2026-competition-ecosystem`
最上面那条提交右侧的短 SHA 点复制图标，得到 40 位完整 SHA。

（`tree/...` 目录页只显示分支名，**看不到 SHA**，这是最容易卡住的地方。）

## 4. 提测字段

| 字段 | 填什么 |
| --- | --- |
| 代码仓库地址 | 页面自动带出，不用填（来自报名登记） |
| Commit Id | 第 3 步拿到的 40 位 SHA |
| Branch | `2026-competition-ecosystem` |

> 报名登记里的仓库地址必须是**干净 URL**：`https://github.com/2742798579/Oceanbase-powercontext`。
> 仓库已经是 public，不需要也不应该带 `user:token@`；带认证信息的 URL 会撞上「URL 不含认证信息」的格式校验。

## 5. 上传后 30 秒自检（强烈建议）

```bash
python scripts/check_remote.py
```

它用 git blob SHA 逐文件比对本地与远端（网页上传的 CRLF→LF 差异已做归一化），输出：

```
本地 57 个文件，远端 57 个文件
一致 57，缺失 0，内容不一致 0，远端多余 0
结果：本地与远端一致 ✅ 可以提测
```

只要出现「缺失」或「不一致」，就先补齐再提测——判题是按 commit 加载文件的，缺一个文件相关用例就是 0 分。

## 6. 六个 0 分坑（按踩坑概率排序）

1. **拖错层级** → 变成 `minimax/minimax/`，判题在 `minimax/` 下找不到模块
2. **Branch 填成 `2026-competition`** → 那是另一条赛道（11 道代码题）的分支，`minimax/` 不在上面
3. **只传了 `sddctx/` 忘了 `business/`** → 上下文模块没有规格/代码/测试可检索，`run`、`context` 两项直接挂
4. **把 `.env` 传上去** → 泄露 API Key，且与「不含真实凭据」的要求冲突；`.gitignore` 已排除，网页上传时别手动拖
5. **用 `--backend memory` 的冒烟结果当验收** → `database` 项要的是 seekdb 检索证据，memory 后端必挂
6. **复用 `patches/after` 提测** → `records/minimax/` 没有真实调用记录，`minimax` 项必挂

## 7. 安全提醒

- 会话里出现过的 PAT 建议全部 revoke 后重发（`https://github.com/settings/tokens?type=beta`），
  已泄露的 token 即使只读也可能被用来读取仓库内容。
- 参赛信息栏建议使用新生成的一对 token；仓库已 public，组织方读取不依赖 token。
- `.env` 只放占位值，真实 Key 通过环境变量注入。
