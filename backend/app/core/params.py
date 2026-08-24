"""Query-parameter coercion.

A filter value that is not a valid enum member is bad input, not a server
fault. Coercing through here turns what would be an unhandled ValueError (and a
500) into a 422 that names the allowed values.
"""

from __future__ import annotations

from enum import Enum
from typing import TypeVar

from app.core.errors import ValidationError

E = TypeVar("E", bound=Enum)


def as_enum(value: str | None, enum_cls: type[E], *, field: str) -> E | None:
    """Parse a filter value, or raise a 422 listing what is accepted."""
    if value is None or value == "":
        return None
    try:
        return enum_cls(value.strip().upper())
    except ValueError:
        allowed = [member.value for member in enum_cls]
        raise ValidationError(
            f"Giá trị '{value}' không hợp lệ cho '{field}'.",
            code="INVALID_FILTER_VALUE",
            details={"field": field, "value": value, "allowed": allowed},
        ) from None


def as_lower_enum(value: str | None, enum_cls: type[E], *, field: str) -> E | None:
    """Same, for enums whose values are lowercase (sync modes)."""
    if value is None or value == "":
        return None
    try:
        return enum_cls(value.strip().lower())
    except ValueError:
        allowed = [member.value for member in enum_cls]
        raise ValidationError(
            f"Giá trị '{value}' không hợp lệ cho '{field}'.",
            code="INVALID_FILTER_VALUE",
            details={"field": field, "value": value, "allowed": allowed},
        ) from None
