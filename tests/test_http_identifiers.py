"""Tests for the shared wire-format identifier and timestamp primitives."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from httk.serve.http.identifiers import is_json_encodable_text, urn_uuid, xsd_utc_timestamp


def test_is_json_encodable_text_flags_only_lone_surrogates() -> None:
    """Ordinary text is encodable while a lone surrogate code point is rejected."""
    assert is_json_encodable_text("urn:uuid:abc") is True
    assert is_json_encodable_text("") is True
    assert is_json_encodable_text("café") is True
    assert is_json_encodable_text("a\ud800b") is False


def test_urn_uuid_preserves_an_already_prefixed_value() -> None:
    """An input already in ``urn:uuid:`` form is returned unchanged."""
    assert urn_uuid("urn:uuid:11111111-2222-3333-4444-555555555555") == (
        "urn:uuid:11111111-2222-3333-4444-555555555555"
    )


def test_urn_uuid_prefixes_a_bare_value() -> None:
    """A bare identifier gains the required ``urn:uuid:`` prefix."""
    assert urn_uuid("negotiation") == "urn:uuid:negotiation"


def test_urn_uuid_renders_arbitrary_factory_output() -> None:
    """A non-string factory result is rendered to text before normalisation."""
    assert urn_uuid(1234) == "urn:uuid:1234"


def test_urn_uuid_rejects_blank_and_lone_surrogate_values() -> None:
    """A blank or surrogate-bearing identifier is rejected with a stable message."""
    with pytest.raises(RuntimeError, match="identifier must be non-empty and free of lone surrogates"):
        urn_uuid("   ")
    with pytest.raises(RuntimeError, match="identifier must be non-empty and free of lone surrogates"):
        urn_uuid("bad\ud800id")


def test_xsd_utc_timestamp_renders_an_aware_utc_datetime_with_a_z_suffix() -> None:
    """An aware UTC datetime renders with a trailing ``Z``."""
    assert xsd_utc_timestamp(datetime(2026, 8, 14, tzinfo=UTC)) == "2026-08-14T00:00:00Z"


def test_xsd_utc_timestamp_converts_a_non_utc_aware_datetime() -> None:
    """A non-UTC aware datetime is converted to UTC before rendering."""
    value = datetime(2026, 8, 14, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    assert xsd_utc_timestamp(value) == "2026-08-14T10:00:00Z"


def test_xsd_utc_timestamp_rejects_a_naive_datetime() -> None:
    """A naive datetime is rejected with a stable message."""
    with pytest.raises(ValueError, match="timestamp value must be a timezone-aware UTC datetime"):
        xsd_utc_timestamp(datetime(2026, 8, 14))  # noqa: DTZ001
