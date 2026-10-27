"""Normalization and date helpers for serial-number/warranty tracking."""

from datetime import date
import re


def serial_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def add_months(value: date | None, months: int | None) -> date | None:
    if value is None or months is None:
        return None
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    import calendar
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))
