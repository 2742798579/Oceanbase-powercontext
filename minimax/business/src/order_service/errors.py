"""业务异常定义。

SPEC-ERR-001：全部继承 OrderServiceError，且 OrderServiceError 继承 ValueError。
SPEC-ERR-002：对外异常仅允许 UnknownStatusError / InvalidAmountRangeError。
SPEC-ERR-003：异常信息格式为 `<原因>：<参数名>=<取值>`。
"""

from __future__ import annotations

from decimal import Decimal


class OrderServiceError(ValueError):
    """所有业务异常的基类（SPEC-ERR-001）。"""


class UnknownStatusError(OrderServiceError):
    """状态取值不在 ORDER_STATUSES 中（SPEC-ORD-006）。"""

    def __init__(self, status: object) -> None:
        self.status = status
        super().__init__(f"未知订单状态：status={status}")


class InvalidAmountRangeError(OrderServiceError):
    """金额区间非法：负数、下界大于上界、无法转换（SPEC-ORD-011）。"""

    def __init__(self, min_amount: object, max_amount: object, reason: str = "金额区间非法") -> None:
        self.min_amount = min_amount
        self.max_amount = max_amount
        super().__init__(f"{reason}：min_amount={min_amount}, max_amount={max_amount}")


def to_decimal(value: object, name: str) -> Decimal:
    """把入参安全转换为 Decimal（SPEC-ORD-001 / SPEC-ORD-011）。"""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is None:
        raise InvalidAmountRangeError(value, None, f"金额无法转换（{name}）")
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001 - 统一转换为业务异常
        raise InvalidAmountRangeError(value, None, f"金额无法转换（{name}）") from exc
