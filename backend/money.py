from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional, Union


MoneyInput = Union[str, int, float, Decimal]

# ISO-4217 currencies whose minor-unit exponent is not the usual two digits.
ZERO_DECIMAL_CURRENCIES = {
    "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG",
    "RWF", "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF",
}
THREE_DECIMAL_CURRENCIES = {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"}
FOUR_DECIMAL_CURRENCIES = {"CLF", "UYW"}


def normalize_currency(currency: str | None) -> str:
    code = (currency or "MYR").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ValueError("Currency must be a three-letter ISO-4217 code.")
    return code


def minor_unit_exponent(currency: str) -> int:
    code = normalize_currency(currency)
    if code in ZERO_DECIMAL_CURRENCIES:
        return 0
    if code in THREE_DECIMAL_CURRENCIES:
        return 3
    if code in FOUR_DECIMAL_CURRENCIES:
        return 4
    return 2


def _as_decimal(value: MoneyInput) -> Decimal:
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid monetary value: {value!r}") from exc


def quantized_major(value: MoneyInput, currency: str = "MYR") -> Decimal:
    exponent = minor_unit_exponent(currency)
    quantum = Decimal(1).scaleb(-exponent)
    return _as_decimal(value).quantize(quantum, rounding=ROUND_HALF_UP)


def major_to_minor(value: MoneyInput, currency: str = "MYR") -> int:
    exponent = minor_unit_exponent(currency)
    scale = Decimal(10) ** exponent
    return int((quantized_major(value, currency) * scale).to_integral_exact())


def minor_to_major(minor: int, currency: str = "MYR") -> Decimal:
    exponent = minor_unit_exponent(currency)
    return Decimal(minor).scaleb(-exponent)


def has_excess_precision(value: MoneyInput, currency: str = "MYR") -> bool:
    amount = _as_decimal(value)
    return amount != quantized_major(amount, currency)


def display_amount_decimal(value: str | None, currency: str = "MYR") -> Optional[Decimal]:
    if value is None or not value.strip():
        return None
    cleaned = value.strip().upper().replace(normalize_currency(currency), "")
    cleaned = re.sub(r"[^0-9.\-+]", "", cleaned)
    if not cleaned or cleaned in {"-", "+", "."}:
        raise ValueError(f"Invalid monetary value: {value!r}")
    return _as_decimal(cleaned)


def parse_display_amount(value: str | None, currency: str = "MYR") -> Optional[int]:
    amount = display_amount_decimal(value, currency)
    return None if amount is None else major_to_minor(amount, currency)


def multiply_minor(unit_amount_minor: int, quantity: float | Decimal) -> int:
    result = Decimal(unit_amount_minor) * _as_decimal(quantity)
    return int(result.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
