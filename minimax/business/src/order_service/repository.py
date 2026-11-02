"""仓储层：内存实现（SPEC-ORD-003 规定仓储不直接暴露给调用方）。"""

from __future__ import annotations

from .models import Order


class InMemoryOrderRepository:
    """最小可运行的订单仓储。"""

    def __init__(self, orders: list[Order] | None = None) -> None:
        self._orders: list[Order] = list(orders or [])

    def add(self, order: Order) -> None:
        self._orders.append(order)

    def all(self) -> list[Order]:
        """返回内部数据的浅拷贝，调用方修改不影响仓储（SPEC-ORD-008）。"""
        return list(self._orders)

    def __len__(self) -> int:
        return len(self._orders)
