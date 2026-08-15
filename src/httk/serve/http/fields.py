"""Validate HTTP field names and values against the RFC 9110 grammar."""

import re
from collections.abc import Iterable

_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


def is_field_name(value: str) -> bool:
    """Report whether a value is a valid RFC 9110 field name.

    :param value: The candidate field name.
    :return: ``True`` when the value is a string of RFC 9110 ``tchar`` characters.
    """
    return isinstance(value, str) and _HEADER_NAME.fullmatch(value) is not None


def is_field_value(value: str) -> bool:
    """Report whether a value is an acceptable HTTP field value.

    :param value: The candidate field value.
    :return: ``True`` when the value is a non-empty string free of CR and LF.
    """
    return isinstance(value, str) and bool(value.strip()) and "\r" not in value and "\n" not in value


def validated_headers(pairs: Iterable[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """Validate response header name-value pairs, rejecting duplicate names.

    Names must be RFC 9110 field names and values must be non-empty and free of
    CR and LF; header names are compared case-insensitively for uniqueness.

    :param pairs: The header name-value pairs to validate.
    :return: The validated pairs as an immutable tuple.
    """
    validated: list[tuple[str, str]] = []
    names: set[str] = set()
    for name, value in pairs:
        if not is_field_name(name) or not is_field_value(value):
            raise ValueError("headers must have valid names and non-empty single-line values")
        normalized = name.lower()
        if normalized in names:
            raise ValueError("header names must be unique")
        names.add(normalized)
        validated.append((name, value))
    return tuple(validated)
