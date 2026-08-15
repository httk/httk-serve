"""Wire-format identifier and timestamp primitives shared across serving protocols.

These helpers carry no protocol vocabulary: they normalise a UUID factory
result to the ``urn:uuid:`` URN form, render a timezone-aware datetime as an
XSD/RFC 3339 UTC timestamp, and report whether a string is free of lone UTF-16
surrogate code points. Protocol packages such as :mod:`httk.serve.dsp` consume
them for on-the-wire message construction.
"""

from datetime import UTC, datetime


def is_json_encodable_text(value: str) -> bool:
    """Report whether a string is free of lone UTF-16 surrogate code points.

    :param value: String to test for lone surrogate code points.
    :return: ``False`` when the string contains a lone surrogate, otherwise ``True``.
    """
    return not any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def urn_uuid(value: object) -> str:
    """Normalise a UUID factory result to the required ``urn:uuid:`` URN form.

    :param value: UUID factory output rendered to text before normalisation.
    :return: The identifier in ``urn:uuid:`` form.
    :raises RuntimeError: If the rendered value is blank or contains a lone surrogate.
    """
    text = str(value)
    if not text.strip() or not is_json_encodable_text(text):
        raise RuntimeError("identifier must be non-empty and free of lone surrogates")
    return text if text.startswith("urn:uuid:") else f"urn:uuid:{text}"


def xsd_utc_timestamp(value: datetime) -> str:
    """Render a timezone-aware datetime as an XSD/RFC 3339 UTC timestamp.

    :param value: Timezone-aware datetime to render as a UTC XML Schema date-time.
    :return: The value in UTC with a ``Z`` suffix.
    :raises TypeError: If ``value`` is not a :class:`~datetime.datetime`.
    :raises ValueError: If ``value`` is naive or lacks a UTC offset.
    """
    if not isinstance(value, datetime):
        raise TypeError("timestamp value must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp value must be a timezone-aware UTC datetime")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["is_json_encodable_text", "urn_uuid", "xsd_utc_timestamp"]
