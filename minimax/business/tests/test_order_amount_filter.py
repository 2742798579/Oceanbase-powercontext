"""目标功能测试：金额区间过滤（SPEC-ORD-009 / 010 / 011）。

这组测试在修改前必须失败（目标行为未实现），修改后必须全部通过。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from order_service.errors import InvalidAmountRangeError
from order_service.models import Order
from order_service.service import OrderService


def test_filter_by_min_amount(service: OrderService) -> None:
    """min_amount 为闭区间下界（SPEC-ORD-009）。"""
    result = service.list_orders(min_amount=Decimal("300.00"))
    assert [order.order_id for order in result] == ["ORD-1003", "ORD-1004"]


def test_filter_by_max_amount(service: OrderService) -> None:
    """max_amount 为闭区间上界（SPEC-ORD-009）。"""
    result = service.list_orders(max_amount=Decimal("100.00"))
    assert [order.order_id for order in result] == ["ORD-1001", "ORD-1002", "ORD-1005"]


def test_amount_range_is_inclusive(service: OrderService) -> None:
    """两端点都命中，结果仍按 created_at 升序（SPEC-ORD-009 + SPEC-ORD-004）。"""
    result = service.list_orders(min_amount=Decimal("100.00"), max_amount=Decimal("300.00"))
    assert [order.order_id for order in result] == ["ORD-1002", "ORD-1003", "ORD-1006"]


def test_amount_range_combines_with_status(service: OrderService) -> None:
    """金额区间与状态过滤是 AND 关系（SPEC-ORD-010）。"""
    result = service.list_orders(status="paid", min_amount=Decimal("120.00"), max_amount=Decimal("300.00"))
    assert [order.order_id for order in result] == ["ORD-1003", "ORD-1006"]


def test_amount_accepts_numeric_string_and_int(service: OrderService) -> None:
    """非 Decimal 但可转换的入参也要支持（SPEC-ORD-009）。"""
    assert [order.order_id for order in service.list_orders(min_amount="150.50")] == [
        "ORD-1003",
        "ORD-1004",
        "ORD-1006",
    ]
    assert [order.order_id for order in service.list_orders(max_amount=100)] == [
        "ORD-1001",
        "ORD-1002",
        "ORD-1005",
    ]


def test_empty_range_returns_empty_list(service: OrderService) -> None:
    """区间无命中返回空列表（SPEC-ORD-007）。"""
    assert service.list_orders(min_amount=Decimal("1000")) == []


def test_invalid_range_raises(service: OrderService) -> None:
    """下界大于上界抛 InvalidAmountRangeError，且信息包含两个边界值（SPEC-ORD-011）。"""
    with pytest.raises(InvalidAmountRangeError) as excinfo:
        service.list_orders(min_amount=Decimal("200.00"), max_amount=Decimal("100.00"))
    message = str(excinfo.value)
    assert "200.00" in message and "100.00" in message
    assert isinstance(excinfo.value, ValueError)


def test_negative_bound_raises(service: OrderService) -> None:
    """负数边界抛 InvalidAmountRangeError（SPEC-ORD-011）。"""
    with pytest.raises(InvalidAmountRangeError):
        service.list_orders(min_amount=Decimal("-0.01"))


def test_non_numeric_bound_raises(service: OrderService) -> None:
    """无法转换的边界抛 InvalidAmountRangeError（SPEC-ORD-011）。"""
    with pytest.raises(InvalidAmountRangeError):
        service.list_orders(max_amount="abc")


def test_validation_happens_before_repository_access(service: OrderService) -> None:
    """参数校验先于数据读取（SPEC-ERR-004）。"""

    class _SpyRepository:
        def __init__(self) -> None:
            self.calls = 0

        def all(self) -> list[Order]:
            self.calls += 1
            return []

    spy = _SpyRepository()
    guarded = OrderService(spy)  # type: ignore[arg-type]
    with pytest.raises(InvalidAmountRangeError):
        guarded.list_orders(min_amount=Decimal("50"), max_amount=Decimal("10"))
    assert spy.calls == 0
