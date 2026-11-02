"""回归测试：既有行为必须保持不变（SPEC-ORD-004/005/006/007/008、SPEC-ORD-012）。

这组测试在修改前后都必须全部通过。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from order_service.errors import UnknownStatusError
from order_service.models import ORDER_STATUSES
from order_service.repository import InMemoryOrderRepository
from order_service.service import OrderService


def test_list_all_orders_in_default_order(service: OrderService) -> None:
    """不传任何条件时返回全部订单，按 created_at 升序（SPEC-ORD-004）。"""
    result = service.list_orders()
    assert [order.order_id for order in result] == [
        "ORD-1001",
        "ORD-1002",
        "ORD-1003",
        "ORD-1004",
        "ORD-1005",
        "ORD-1006",
    ]


def test_filter_by_status(service: OrderService) -> None:
    """按状态过滤（SPEC-ORD-005）。"""
    result = service.list_orders(status="paid")
    assert [order.order_id for order in result] == ["ORD-1002", "ORD-1003", "ORD-1006"]


def test_unknown_status_raises_value_error(service: OrderService) -> None:
    """未知状态抛 UnknownStatusError，且仍是 ValueError（SPEC-ORD-006 / SPEC-ERR-001）。"""
    with pytest.raises(UnknownStatusError) as excinfo:
        service.list_orders(status="refunded")
    assert isinstance(excinfo.value, ValueError)
    assert "refunded" in str(excinfo.value)


def test_empty_result_is_empty_list() -> None:
    """无匹配时返回空列表而不是 None（SPEC-ORD-007）。"""
    empty_service = OrderService(InMemoryOrderRepository([]))
    result = empty_service.list_orders(status="paid")
    assert result == []
    assert isinstance(result, list)


def test_returned_list_is_independent_copy(service: OrderService) -> None:
    """调用方修改返回列表不影响仓储（SPEC-ORD-008）。"""
    first = service.list_orders()
    first.clear()
    second = service.list_orders()
    assert len(second) == 6


def test_order_entity_is_immutable(service: OrderService) -> None:
    """Order 为 frozen dataclass（SPEC-ORD-008）。"""
    order = service.list_orders()[0]
    with pytest.raises(Exception):
        order.status = "paid"  # type: ignore[misc]


def test_amount_is_decimal(service: OrderService) -> None:
    """金额必须是 Decimal（SPEC-ORD-001）。"""
    assert all(isinstance(order.amount, Decimal) for order in service.list_orders())


def test_status_constants_are_single_source() -> None:
    """状态取值来自 models.ORDER_STATUSES（SPEC-ORD-002）。"""
    assert ORDER_STATUSES == ("created", "paid", "shipped", "cancelled")
