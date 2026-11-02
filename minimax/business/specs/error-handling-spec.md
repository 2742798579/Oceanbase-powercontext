# 异常与日志规格（Error Handling Spec）

> 版本：v1.0。所有对外异常必须满足本文件约定，新增过滤参数时同样适用。

### SPEC-ERR-001 异常继承
业务异常必须继承 `order_service.errors.OrderServiceError`，且 `OrderServiceError` 本身继承
`ValueError`，以保证既有 `except ValueError` 调用方不受影响。

### SPEC-ERR-002 异常类型清单
`order_service.errors` 中对外暴露的异常仅允许：

| 异常 | 触发条件 | 对应规格 |
| --- | --- | --- |
| `UnknownStatusError` | 状态取值非法 | SPEC-ORD-006 |
| `InvalidAmountRangeError` | 金额区间非法 | SPEC-ORD-011 |

新增异常必须同步更新本表格，禁止使用内置 `Exception` 直接抛出。

### SPEC-ERR-003 异常信息格式
异常信息必须使用中文短语 + 关键入参，格式为 `<原因>：<参数名>=<取值>`，例如
`金额区间非法：min_amount=200.00, max_amount=100.00`。禁止在异常信息中泄漏内部对象地址。

### SPEC-ERR-004 参数校验顺序
参数校验必须在数据读取**之前**完成：先校验 `status`，再校验金额区间，最后才访问仓储。
校验失败时不得产生任何仓储调用（便于上层做幂等重试）。
