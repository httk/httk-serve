"""Serve an existing database over OPTIMADE from a store plus a property-to-field map."""

import datetime
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from httk.core import EntryTypeDefinition, standard_entry_type
from httk.store.query import Store

from ..schema.served import build_served_schema
from ._property_handlers import value_aware_property_handlers
from .adapter import BackendAdapter, EntrySource


@dataclass(frozen=True)
class MappedSource:
    """One queryable source of an entry type, described by a property-to-field map.

    :param target: Store-specific target passed to ``searcher.variable``.
    :param keys: Served property name to backend field name; must contain ``id``.
    :param fields: Computed or overriding response extractors applied to matched rows.
    :param relationships: Optional relationships extractor, as for :class:`~httk.serve.optimade.backend.adapter.EntrySource`.
    """

    target: Any
    keys: Mapping[str, str]
    fields: Mapping[str, Callable[[Any], Any]] = field(default_factory=dict)
    relationships: Callable[[Any], Any] | None = None


def _present(value: Any, name: str) -> Any:
    """Present a raw database value as JSON-serialisable.

    :class:`decimal.Decimal` values become ``int`` when integral, else ``float``: this is
    presentation of an exact backend value at the protocol boundary.

    :param value: The raw value.
    :param name: The served property name, for error messages.
    :return: The JSON-serialisable value.
    :raises TypeError: If the value type cannot be presented.
    """
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime.datetime):
        utc = value.replace(tzinfo=datetime.UTC) if value.tzinfo is None else value.astimezone(datetime.UTC)
        text = utc.isoformat(timespec="seconds" if utc.microsecond == 0 else "auto")
        return text.replace("+00:00", "Z")
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise TypeError(
                f"property {name!r}: cannot serve the non-finite Decimal {value}; map it with a fields extractor"
            )
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, list | tuple):
        return [_present(item, name) for item in value]
    if isinstance(value, Mapping):
        return {key: _present(item, name) for key, item in value.items()}
    raise TypeError(f"property {name!r}: cannot serve a {type(value).__name__} value; map it with a fields extractor")


def _constant(value: str) -> Callable[[Any], str]:
    """Build an extractor returning ``value`` for every row."""
    return lambda row: value


def _extractor(entry: str, name: str, key: str) -> Callable[[Any], Any]:
    """Build the presenting extractor reading ``row[key]``."""

    def extract(row: Any) -> Any:
        if not isinstance(row, Mapping):
            raise TypeError(
                f"entry type {entry!r}: rows are {type(row).__name__}, not mappings; use a fields extractor for {name!r}"
            )
        value = _present(row.get(key), name)
        return str(value) if name == "id" and value is not None else value

    return extract


def adapter_from_sources(
    store: Store,
    sources: Mapping[str, MappedSource | Sequence[MappedSource]],
    definitions: Mapping[str, EntryTypeDefinition] | None = None,
    **schema_options: Any,
) -> BackendAdapter:
    """Build a :class:`~httk.serve.optimade.backend.adapter.BackendAdapter` serving an existing store through per-entry-type column maps.

    Keyed properties are filterable and sortable (unless list-valued); ``fields``-only
    properties are served but not filterable (HTTP 501). Raw values are presented as JSON:
    datetimes as RFC 3339 UTC with ``Z`` (naive treated as UTC), dates as ISO, ``Decimal`` as ``int``
    when integral else ``float``, lists and mappings recursively, ids as strings; other types and non-finite Decimals raise ``TypeError``. Revisions, alternatives and ``as_of``
    are not supported. Every served property must be described by the entry type's
    definition (``definitions`` or the standard one).

    :param store: Store implementing the neutral query protocol.
    :param sources: Sources per entry type; several sources of one type must share ``keys``.
    :param definitions: Entry type definitions overriding the standard ones.
    :param \\*\\*schema_options: Forwarded to :func:`~httk.serve.optimade.schema.served.build_served_schema`.
    :return: Fully wired backend adapter.
    :raises ValueError: If a source is invalid, a mapped backend field is missing, or a served property is undefined.
    """
    definitions = definitions or {}
    grouped: dict[str, tuple[MappedSource, ...]] = {}
    served: dict[str, list[str]] = {}
    for entry, given in sources.items():
        group = (given,) if isinstance(given, MappedSource) else tuple(given)
        if not group:
            raise ValueError(f"entry type {entry!r}: no sources given")
        names = ["id", "type"]
        for source in group:
            if "id" not in source.keys:
                raise ValueError(f"entry type {entry!r}: keys must contain 'id'")
            if "type" in source.keys or "type" in source.fields:
                raise ValueError(f"entry type {entry!r}: 'type' is always served as the endpoint name")
            if dict(source.keys) != dict(group[0].keys):
                raise ValueError(f"entry type {entry!r}: all sources must have identical keys")
            variable = store.searcher().variable(source.target)
            for name, backend_field in source.keys.items():
                try:
                    getattr(variable, backend_field)
                except AttributeError as error:
                    raise ValueError(
                        f"entry type {entry!r}: property {name!r} maps to {backend_field!r}, "
                        f"which target {source.target!r} does not have: {error}"
                    ) from error
            names += [name for name in (*source.keys, *source.fields) if name not in names]
        grouped[entry] = group
        served[entry] = names
    defs = {entry: definitions.get(entry) or standard_entry_type(entry) for entry in grouped}
    overrides = {entry: [n for n in names if n not in ("id", "type")] for entry, names in served.items()}
    options: dict[str, Any] = {"default_response_overrides": overrides, **schema_options}
    schema = build_served_schema(defs, served, **options)
    if "sortable" not in schema_options:
        sortable = {
            entry: [
                name
                for name in grouped[entry][0].keys
                if not schema.entry_info[entry]["properties"][name].get("fulltype", "string").startswith("list of ")
            ]
            for entry in grouped
        }
        schema = build_served_schema(defs, served, **{**options, "sortable": sortable})
    handlers: dict[str, Any] = {}
    resolved: dict[str, tuple[EntrySource, ...]] = {}
    for entry, group in grouped.items():
        fulltypes = {n: p.get("fulltype", "string") for n, p in schema.entry_info[entry]["properties"].items()}
        handlers[entry] = value_aware_property_handlers(entry, dict(group[0].keys), fulltypes)
        entry_sources = []
        for source in group:
            extractors: dict[str, Callable[[Any], Any]] = {
                name: _extractor(entry, name, key) for name, key in source.keys.items()
            }
            extractors["type"] = _constant(entry)
            extractors.update(source.fields)
            entry_sources.append(
                EntrySource(
                    source.target, fields=extractors, sort_keys=dict(source.keys), relationships=source.relationships
                )
            )
        resolved[entry] = tuple(entry_sources)
    return BackendAdapter(store=store, sources=resolved, schema=schema, field_handlers=handlers)
