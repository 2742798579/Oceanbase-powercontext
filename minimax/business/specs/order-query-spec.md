# 订单查询规格（Order Query Spec）

> 本文件是 order-service 的**有效规格**（source of truth）。每条规格以 `SPEC-ORD-xxx` 编号，
> 上下文模块按编号切分并入库，评审可据此核对"上下文是否包含必要规格条目"。
>
> 版本：v1.0（初始版本）

## 1. 数据模型

### SPEC-ORD-001 订单金额精度
订单金额 `amount` 必须使用 `decimal.Decimal` 表示，禁止使用 `float` 参与金额比较与运算。
理由：浮点误差会让区间边界（如 100.00）出现漏单或多单。

### SPEC-ORD-002 订单状态取值
订单状态 `status` 仅允许 `created`、`paid`、`shipped`、`cancelled` 四种取值，定义在
`order_service.models.ORDER_STATUSES` 中，其他模块不得重复硬编码。

## 2. 列表查询

### SPEC-ORD-003 查询入口
订单列表查询入口为 `order_service.service.OrderService.list_orders`。
该方法是**唯一**对外查询入口，仓储层 `OrderRepository.all()` 不得直接暴露给调用方。

### SPEC-ORD-004 默认排序
`list_orders` 返回的订单必须按 `created_at` 升序排列；`created_at` 相同时按 `order_id`
字典序升序排列。排序在过滤之后执行，且不得改变入参仓储中的原始顺序。

### SPEC-ORD-005 按状态过滤
`list_orders(status=...)` 只返回 `status` 与入参完全相等的订单；`status=None` 表示不按状态过滤，
返回全部订单。比较区分大小写。

### SPEC-ORD-006 未知状态报错
当 `status` 取值不在 `ORDER_STATUSES` 中时，必须抛出
`order_service.errors.UnknownStatusError`（`ValueError` 的子类），错误信息需包含非法取值。

### SPEC-ORD-007 空结果
任何过滤条件组合下若没有匹配订单，必须返回**空列表** `[]`，不得返回 `None`，不得抛出异常。

### SPEC-ORD-008 返回值不可变契约
`list_orders` 必须返回新的 `list` 对象，调用方对返回列表的增删改不得影响仓储内部状态。
返回的 `Order` 为 frozen dataclass，字段不可被修改。

## 3. 金额区间过滤（本次任务新增）

### SPEC-ORD-009 金额区间过滤
`list_orders` 必须支持可选的 `min_amount` 与 `max_amount` 参数（`Decimal` 或可被
`Decimal(str(x))` 转换的数值），语义为**闭区间**过滤：

- `min_amount` 不为 `None` 时，保留 `amount >= min_amount` 的订单；
- `max_amount` 不为 `None` 时，保留 `amount <= max_amount` 的订单；
- 两者同时给出时取交集；
- 两者均为 `None` 时，金额不参与过滤。

### SPEC-ORD-010 金额区间与状态过滤可叠加
`min_amount` / `max_amount` 必须能与 `status` 同时使用，过滤条件之间是**与（AND）**关系；
先做过滤，再按 SPEC-ORD-004 排序。

### SPEC-ORD-011 金额区间参数校验
以下情况必须抛出 `order_service.errors.InvalidAmountRangeError`（`ValueError` 的子类）：

- `min_amount` 或 `max_amount` 为负数；
- `min_amount` 大于 `max_amount`；
- 入参无法转换为 `Decimal`（如 `"abc"`、`None` 以外的空字符串）。

错误信息需同时包含两个边界值，便于定位。

## 4. 兼容性

### SPEC-ORD-012 向后兼容
新增金额区间过滤不得改变既有调用方式的行为：仅传 `status` 或什么都不传时，返回结果必须与
v1.0 完全一致（包括排序与异常类型）。
