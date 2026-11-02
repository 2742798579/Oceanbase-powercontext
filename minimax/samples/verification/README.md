# 验证样例

## 1. 任务描述

`order-service` 的 `OrderService.list_orders` 目前只支持按状态过滤。业务方要求：

- 支持 `min_amount` / `max_amount` 金额区间过滤，**闭区间**；
- 可与 `status` 叠加（AND）；
- 非法区间（负数、下界 > 上界、无法转 Decimal）抛 `InvalidAmountRangeError`；
- 既有调用方式行为完全不变。

任务定义文件：[`task.json`](task.json)。

## 2. 有效规格（必须出现在上下文里）

| 规格编号 | 文件 | 作用 |
| --- | --- | --- |
| `SPEC-ORD-009` | `business/specs/order-query-spec.md` | 金额区间过滤语义（闭区间、None 不参与） |
| `SPEC-ORD-010` | 同上 | 金额区间与状态过滤按 AND 叠加 |
| `SPEC-ORD-011` | 同上 | 非法区间的异常与错误信息要求 |
| `SPEC-ORD-004` | 同上 | 默认排序（过滤后按 created_at、order_id 升序） |
| `SPEC-ERR-004` | `business/specs/error-handling-spec.md` | 参数校验先于数据读取 |

## 3. 必须定位到的代码与测试

| 类型 | 期望来源 |
| --- | --- |
| 代码 | `src/order_service/service.py` → `OrderService.list_orders` |
| 代码 | `src/order_service/errors.py` → `InvalidAmountRangeError`、`to_decimal` |
| 测试 | `tests/test_order_amount_filter.py` → `test_filter_by_min_amount`、`test_filter_by_max_amount`、`test_amount_range_is_inclusive`、`test_invalid_range_raises`、`test_validation_happens_before_repository_access` |

## 4. 初始数据

- 规格 2 个文件（16 条 `SPEC-*`）
- 源码 5 个文件（20 个符号 chunk）
- 测试 3 个文件（20 个 chunk，含 18 个测试函数）
- 订单数据集：`business/tests/conftest.py` 中固定 6 条订单，金额覆盖 0.00 / 99.99 / 100.00 / 150.50 / 300.00 / 300.01

## 5. 操作步骤

```bash
python -m sddctx ingest                 # 1. 导入规格/代码/测试到 seekdb
python -m sddctx context                # 2. 生成上下文（核对来源）
python -m pytest business/tests -q      # 3. 修改前：10 failed, 8 passed
python -m sddctx patch                  # 4. MiniMax 生成并落地修改
python -m pytest business/tests -q      # 5. 修改后：18 passed
python -m sddctx state-save --state records/state/task-state.json   # 6. 保存状态
python -m sddctx state-resume           # 7. 新进程读回状态并注入上下文
```

或一条命令：`python -m sddctx verify`。

## 6. 预期结果

| 验收项 | 预期 |
| --- | --- |
| 能够运行 | `ingest` 入库 56 条；`context` 正常输出 |
| 上下文正确 | 5 条必需规格 + 3 个代码符号 + 5 个测试全部命中，且带来源 |
| 数据库实际参与 | 后端 `seekdb`，检索日志非空，结果进入上下文 |
| MiniMax 实际参与 | `records/minimax/*.json` 与 `patches/*.diff` 对应 |
| 代码修改有效 | 目标测试 `10 failed → 10 passed`；回归测试 `8 passed → 8 passed` |
| 状态能够复用 | 新进程读回 4 条已完成 / 2 条待办，`prepared_status=ready` |

## 7. 重置方法

```bash
python -m sddctx restore          # 恢复 patches/before 下的基线代码与测试
rm -rf data/ records/context records/tests   # 清空本地库与运行记录（保留 records/minimax 作证据时可不删）
python -m sddctx ingest           # 重新入库
```

PowerContext 侧如需从空状态重来：换一个 `POWERCONTEXT_SCOPE_ID`，或删除
`records/state/scope.json` 后用新的 `idempotency_key` 创建新 scope。
