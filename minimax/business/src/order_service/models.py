"""订单领域模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

#: 合法订单状态（SPEC-ORD-002：唯一定义处，其他模块不得硬编码）
ORDER_STATUSES: tuple[str, ...] = ("created", "paid", "shipped", "cancelled")


@dataclass(frozen=True)
class Order:
    """订单实体。

    frozen dataclass 保证字段不可变（SPEC-ORD-008）。
    amount 必须是 Decimal（SPEC-ORD-001）。
    """

    order_id: str
    customer_id: str
    status: str
    amount: Decimal
    created_at: datetime
