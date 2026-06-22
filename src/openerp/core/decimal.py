from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def to_decimal(value: str | int | float | Decimal | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def decimal_to_db(value: str | int | float | Decimal) -> str:
    return format(to_decimal(value), "f")


def round_decimal(value: Decimal, scale: int) -> Decimal:
    quant = Decimal(1).scaleb(-scale)
    return value.quantize(quant, rounding=ROUND_HALF_UP)
