"""Server-specific property handlers over neutral query expressions."""

from collections.abc import Callable, Mapping
from typing import Any

from httk.store import FilterTranslationError
from httk.store.query import SearchExpression, SearchVariable
from httk.store.query.optimade_filters import simple_property_handlers


def _known_value_handler(
    field_name: str,
    search_variable: SearchVariable,
    unknown_type: str,
) -> SearchExpression:
    """Translate known/unknown against the row value, not schema presence.

    A property being served means its name is recognized; it does not mean
    every resource has a non-null value.  Comparing through the neutral field
    protocol gives ``IS [NOT] NULL`` in SQL and the same value semantics in the
    in-memory store.
    """

    field = getattr(search_variable, field_name)
    if unknown_type == "IS_KNOWN":
        return field.__ne__(None)
    if unknown_type == "IS_UNKNOWN":
        return field.__eq__(None)
    raise FilterTranslationError("Unexpected unknown operator type", "internal")


def value_aware_property_handlers(
    entry_type: str,
    property_keys: Mapping[str, str],
    property_fulltypes: Mapping[str, str],
) -> dict[str, Mapping[str, Callable[..., Any]]]:
    """Build generic handlers whose known/unknown tests inspect row values."""

    generated = simple_property_handlers(entry_type, property_keys, property_fulltypes)
    handlers: dict[str, Mapping[str, Callable[..., Any]]] = dict(generated)
    for name, key in property_keys.items():
        table = dict(generated[name])
        table["unknown"] = lambda entry, sv, unknown_type, k=key: _known_value_handler(k, sv, unknown_type)
        handlers[name] = table
    return handlers
