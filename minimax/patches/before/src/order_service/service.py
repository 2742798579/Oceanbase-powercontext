"""订单查询服务。

规格依据：
- SPEC-ORD-003 唯一查询入口
- SPEC-ORD-004 默认排序
- SPEC-ORD-005 状态过滤
- SPEC-ORD-006 未知状态报错
- SPEC-ORD-009 / 010 / 011 金额区间过滤（本次任务目标，尚未实现）
- SPEC-ERR-004 校验先于数据读取
"""

from __future__ import annotations

from .errors import UnknownStatusError
from .models import ORDER_STATUSES, Order
from .repository import InMemoryOrderRepository


class OrderService:
    def __init__(self, repository: InMemoryOrderRepository) -> None:
        self._repository = repository

    def list_orders(self, status: str | None = None) -> list[Order]:
        """按状态过滤并按 created_at / order_id 升序返回订单。

        TODO(SPEC-ORD-009): 支持 min_amount / max_amount 闭区间过滤。
        TODO(SPEC-ORD-010): 金额过滤与状态过滤按 AND 叠加。
        TODO(SPEC-ORD-011): 非法区间抛 InvalidAmountRangeError。
        """
        # 参数校验先于数据读取（SPEC-ERR-004）
        if status is not None and status not in ORDER_STATUSES:
            raise UnknownStatusError(status)

        orders = self._repository.all()
        if status is not None:
            orders = [order for order in orders if order.status == status]

        return sorted(orders, key=lambda order: (order.created_at, order.order_id))
