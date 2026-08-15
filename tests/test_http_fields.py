"""Unit tests for the HTTP field-name and field-value validation helpers."""

import pytest

from httk.serve.http import is_field_name, is_field_value, validated_headers


def test_is_field_name_accepts_tchar_grammar() -> None:
    assert is_field_name("Content-Type")
    assert is_field_name("X-Custom_Header")
    assert is_field_name("!#$%&'*+.^_`|~0-9AZ-")


def test_is_field_name_rejects_invalid_names() -> None:
    assert not is_field_name("")
    assert not is_field_name("bad name")
    assert not is_field_name("bad:colon")
    assert not is_field_name("bad\r")
    assert not is_field_name(123)  # type: ignore[arg-type]


def test_is_field_value_accepts_non_empty_single_line_values() -> None:
    assert is_field_value("Accept")
    assert is_field_value("public, max-age=60")


def test_is_field_value_rejects_empty_and_multiline_values() -> None:
    assert not is_field_value("")
    assert not is_field_value("   ")
    assert not is_field_value("has\rcr")
    assert not is_field_value("has\nlf")
    assert not is_field_value(123)  # type: ignore[arg-type]


def test_validated_headers_returns_validated_pairs() -> None:
    pairs = (("Vary", "Accept"), ("Link", "<p>; rel=profile"))
    assert validated_headers(pairs) == pairs
    assert validated_headers(()) == ()


def test_validated_headers_rejects_malformed_pairs_with_expected_message() -> None:
    for pairs in (
        (("bad name", "value"),),
        (("Vary", ""),),
        (("Vary", "   "),),
        (("Vary", "has\nlf"),),
    ):
        with pytest.raises(ValueError, match="headers must have valid names and non-empty single-line values"):
            validated_headers(pairs)


def test_validated_headers_rejects_case_insensitive_duplicate_names() -> None:
    with pytest.raises(ValueError, match="header names must be unique"):
        validated_headers((("Vary", "Accept"), ("vary", "Origin")))
