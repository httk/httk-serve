"""Protocol-neutral JSON value model shared across httk serving protocols.

This module owns the recursive JSON data model used by every protocol
implementation in :mod:`httk.serve`, together with the deep freeze/thaw pair
that converts between ordinary caller-owned JSON containers and an immutable
snapshot representation.

:data:`httk.serve.http.JsonDocument` is a deliberately laxer alias
(``Mapping[str, object]``) for a single JSON object accepted by the GET-app
factories, whereas :data:`JsonValue` here models an arbitrary JSON value
recursively. The two are intentionally distinct and neither is defined in
terms of the other.
"""

from collections.abc import Mapping
from math import isfinite
from types import MappingProxyType

type JsonScalar = str | int | float | bool | None
"""A JSON scalar -- string, number, boolean, or null."""

type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
"""An arbitrary JSON value modelled with ordinary mutable containers."""

type FrozenJsonValue = JsonScalar | tuple["FrozenJsonValue", ...] | Mapping[str, "FrozenJsonValue"]
"""An immutable JSON value snapshot.

The mapping arm is typed as :class:`~collections.abc.Mapping` so a plain
mutable :class:`dict` satisfies it statically, while :func:`freeze_json`
guarantees a :class:`~types.MappingProxyType` at runtime. This static/runtime
gap is intentional and is not closed here.
"""


def freeze_json(value: object) -> FrozenJsonValue:
    """Freeze a JSON-compatible value without retaining caller-owned containers.

    :param value: JSON-compatible value to copy into an immutable representation.
    :return: An immutable JSON-compatible value.
    :raises TypeError: If ``value`` is not JSON-compatible.
    :raises ValueError: If a floating-point value is non-finite.
    """
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("JSON floats must be finite")
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, FrozenJsonValue] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            frozen[key] = freeze_json(child)
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(freeze_json(child) for child in value)
    raise TypeError(f"Expected a JSON-compatible value, got {type(value).__name__}")


def thaw_json(value: FrozenJsonValue) -> JsonValue:
    """Return an independent ordinary JSON value from an immutable snapshot.

    :param value: Immutable JSON-compatible value to copy.
    :return: Plain JSON-compatible lists and dictionaries.
    """
    if isinstance(value, Mapping):
        return {key: thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(child) for child in value]
    return value


__all__ = [
    "FrozenJsonValue",
    "JsonScalar",
    "JsonValue",
    "freeze_json",
    "thaw_json",
]
