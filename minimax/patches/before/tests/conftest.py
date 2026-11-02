"""pytest 公共夹具：固定数据集，保证测试可重复。"""

from __future__ import annotations

import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from order_service.models import Order  # noqa: E402
from order_service.repository import InMemoryOrderRepository  # noqa: E402
from order_service.service import OrderService  # noqa: E402


def _order(order_id: str, status: str, amount: str, day: int) -> Order:
    return Order(
        order_id=order_id,
        customer_id=f"C-{order_id[-2:]}",
        status=status,
        amount=Decimal(amount),
        created_at=datetime(2026, 3, day, 9, 0, 0),
    )


@pytest.fixture()
def orders() -> list[Order]:
    """6 条固定订单，覆盖 4 种状态与金额边界（100.00 / 300.00）。"""
    return [
        _order("ORD-1001", "created", "99.99", 1),
        _order("ORD-1002", "paid", "100.00", 2),
        _order("ORD-1003", "paid", "300.00", 3),
        _order("ORD-1004", "shipped", "300.01", 4),
        _order("ORD-1005", "cancelled", "0.00", 5),
        _order("ORD-1006", "paid", "150.50", 6),
    ]


@pytest.fixture()
def repository(orders: list[Order]) -> InMemoryOrderRepository:
    return InMemoryOrderRepository(orders)


@pytest.fixture()
def service(repository: InMemoryOrderRepository) -> OrderService:
    return OrderService(repository)
