"""Parse and select against RFC 9110 ``Accept`` media ranges.

This module consolidates the RFC 9110 ``Accept``-header parsing shared by the
lightweight JSON applications and the DSP catalogue policy. Parsing is common;
selection deliberately is not, so two separately named selectors are exposed:
one keeps parameterised ranges and matches their parameters, the other discards
any range that carries a parameter.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

_QVALUE = re.compile(r"(?:0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?)\Z")


@dataclass(frozen=True, slots=True)
class MediaRange:
    """One valid media range parsed from an HTTP ``Accept`` header.

    :param major: Lowercase major type, or ``"*"`` for a wildcard.
    :param minor: Lowercase minor type, or ``"*"`` for a wildcard.
    :param parameters: Non-``q`` parameters as immutable lowercase name-value pairs.
    :param quality: The ``q`` weight, defaulting to ``1.0`` when absent.
    """

    major: str
    minor: str
    parameters: tuple[tuple[str, str], ...]
    quality: float


def split_http_list(value: str, delimiter: str) -> tuple[str, ...] | None:
    """Split an HTTP field outside quoted strings, rejecting broken quoting.

    :param value: The raw HTTP field value to split.
    :param delimiter: The single-character separator to split on outside quotes.
    :return: The delimited parts, or ``None`` when a quoted string is unterminated.
    """
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    escaped = False
    for character in value:
        if escaped:
            current.append(character)
            escaped = False
        elif quoted and character == "\\":
            current.append(character)
            escaped = True
        elif character == '"':
            current.append(character)
            quoted = not quoted
        elif character == delimiter and not quoted:
            parts.append("".join(current))
            current = []
        else:
            current.append(character)
    if quoted or escaped:
        return None
    parts.append("".join(current))
    return tuple(parts)


def http_parameter_value(value: str) -> str | None:
    """Decode one token or quoted-string parameter value.

    :param value: The raw parameter value, possibly a quoted string.
    :return: The decoded value, or ``None`` when the value is malformed.
    """
    value = value.strip()
    if not value:
        return None
    if not value.startswith('"'):
        return None if '"' in value else value
    if len(value) < 2 or not value.endswith('"'):
        return None
    decoded: list[str] = []
    escaped = False
    for character in value[1:-1]:
        if escaped:
            decoded.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == '"':
            return None
        else:
            decoded.append(character)
    if escaped:
        return None
    return "".join(decoded)


def parse_media_type(value: object) -> tuple[str, str, dict[str, str]]:
    """Parse a JSON media type into its major, minor, and parameters.

    :param value: The declared media type; only ``application/json`` and
        ``application/*+json`` types with unique parameters are accepted.
    :return: The lowercase major type, lowercase minor type, and parameter mapping.
    """
    if not isinstance(value, str):
        raise ValueError("media_type must be a JSON media type")
    split_value = split_http_list(value, ";")
    if split_value is None:
        raise ValueError("media_type must be a JSON media type")
    parts = [part.strip() for part in split_value]
    if parts[0].count("/") != 1:
        raise ValueError("media_type must be a JSON media type")
    major, minor = parts[0].lower().split("/", 1)
    if major != "application" or (minor != "json" and not minor.endswith("+json")):
        raise ValueError("media_type must be application/json or an application/*+json type")
    parameters: dict[str, str] = {}
    for parameter in parts[1:]:
        if "=" not in parameter:
            raise ValueError("media_type parameters must be name=value pairs")
        name, raw_value = parameter.split("=", 1)
        name = name.strip().lower()
        decoded = http_parameter_value(raw_value)
        if not name or decoded is None or name in parameters:
            raise ValueError("media_type parameters must have unique non-empty names and values")
        parameters[name] = decoded
    return major, minor, parameters


def parse_accept(header: str) -> tuple[MediaRange, ...]:
    """Parse an ``Accept`` header into its valid media ranges.

    :param header: The raw ``Accept`` header value.
    :return: The valid parsed media ranges, or ``()`` when the header cannot be split.
    """
    ranges: list[MediaRange] = []
    items = split_http_list(header, ",")
    if items is None:
        return ()
    for item in items:
        split_parts = split_http_list(item, ";")
        if split_parts is None:
            continue
        parts = [part.strip() for part in split_parts]
        media_type = parts[0].lower()
        if media_type.count("/") != 1:
            continue
        major, minor = media_type.split("/", 1)
        if not major or not minor or (major == "*" and minor != "*") or ("*" in minor and minor != "*"):
            continue
        parameters: list[tuple[str, str]] = []
        quality = 1.0
        seen_names: set[str] = set()
        valid = True
        for parameter in parts[1:]:
            if "=" not in parameter:
                valid = False
                break
            name, raw_value = parameter.split("=", 1)
            name = name.strip().lower()
            value = http_parameter_value(raw_value)
            if not name or value is None or name in seen_names:
                valid = False
                break
            seen_names.add(name)
            if name == "q":
                if _QVALUE.fullmatch(value) is None:
                    valid = False
                    break
                quality = float(value)
            else:
                parameters.append((name, value))
        if valid:
            ranges.append(MediaRange(major, minor, tuple(parameters), quality))
    return tuple(ranges)


def best_quality_matching_parameters(
    ranges: tuple[MediaRange, ...], major: str, minor: str, parameters: Mapping[str, str]
) -> float | None:
    """Select the best range that keeps and matches its parameters.

    A parameterised range is kept only when every one of its parameters is
    present and equal in ``parameters``. The specificity tiebreak favours a more
    specific major/minor match and then a range carrying more parameters.

    :param ranges: The parsed media ranges to select among.
    :param major: The response major type to match.
    :param minor: The response minor type to match.
    :param parameters: The response media-type parameters to match against.
    :return: The winning range's quality, or ``None`` when nothing matches.
    """
    best: tuple[float, tuple[int, int]] | None = None
    for accept_range in ranges:
        if accept_range.major not in {"*", major} or accept_range.minor not in {"*", minor}:
            continue
        if any(parameters.get(name) != value for name, value in accept_range.parameters):
            continue
        specificity = (
            2
            if accept_range.major == major and accept_range.minor == minor
            else 1
            if accept_range.major == major
            else 0,
            len(accept_range.parameters),
        )
        if best is None or specificity > best[1]:
            best = (accept_range.quality, specificity)
    return None if best is None else best[0]


def best_quality_ignoring_parameterised(ranges: tuple[MediaRange, ...], major: str, minor: str) -> float | None:
    """Select the best range, discarding any range carrying a parameter.

    Any range that carries a parameter is discarded outright. The specificity
    tiebreak favours a more specific major/minor match, with a constant second
    component.

    :param ranges: The parsed media ranges to select among.
    :param major: The response major type to match.
    :param minor: The response minor type to match.
    :return: The winning range's quality, or ``None`` when nothing matches.
    """
    best: tuple[float, tuple[int, int]] | None = None
    for item in ranges:
        if item.major not in {"*", major} or item.minor not in {"*", minor} or item.parameters:
            continue
        specificity = (2 if item.major == major and item.minor == minor else 1 if item.major == major else 0, 0)
        if best is None or specificity > best[1]:
            best = (item.quality, specificity)
    return None if best is None else best[0]
