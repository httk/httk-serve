"""Build a :class:`~httk.optimade.backend.adapter.BackendAdapter` from :class:`~httk.core.EntryProvider` sources.

This is the bridge from the neutral httk-core entry-provider contract to an
OPTIMADE-serving backend: it turns one or more providers into a fully wired
:class:`~httk.optimade.backend.adapter.BackendAdapter` over an in-memory store, deriving the served schema,
the filter handlers, and the response-field extractors from each provider's
descriptions, columns, and records. It is httk-optimade's only dependency on
``httk.core`` beyond the shared runtime.
"""

from typing import Any, Callable, Iterable

from httk.core import EntryProvider, EntryTypeDefinition

from ..schema.served import build_served_schema
from .adapter import BackendAdapter, EntrySource
from .handlers import HandlerTable, simple_property_handlers
from .memory_store import InMemoryStore


def _column_extractor(column: str) -> Callable[[Any], Any]:
    """A field extractor reading ``column`` from a record mapping."""
    return lambda row: row.get(column)


def adapter_from_providers(providers: Iterable[EntryProvider], **options: Any) -> BackendAdapter:
    """Build a :class:`~httk.optimade.backend.adapter.BackendAdapter` serving the given entry providers.

    Every provider's :meth:`~httk.core.EntryProvider.entry_types` become served
    entry types (described by their :class:`~httk.core.EntryTypeDefinition`), its
    :meth:`~httk.core.EntryProvider.columns` name the served subset and drive
    both the filter handlers (via
    :func:`~httk.optimade.backend.handlers.simple_property_handlers`) and the
    response-field extractors, and its
    :meth:`~httk.core.EntryProvider.records` are loaded into an
    :class:`~httk.optimade.backend.memory_store.InMemoryStore`. Every served
    property MUST be described by the entry type's definition (a custom property
    must therefore live in an
    :meth:`~httk.core.EntryTypeDefinition.extended` definition); a
    :class:`ValueError` names any offender. All served properties beyond
    ``id``/``type`` are marked default-response. Extra keyword ``options`` (e.g.
    ``sortable``, ``recognized_prefixes``) are forwarded to
    :func:`~httk.optimade.schema.served.build_served_schema`.
    """
    served_map: dict[str, list[str]] = {}
    definitions: dict[str, EntryTypeDefinition] = {}
    default_overrides: dict[str, list[str]] = {}
    columns_by_entry: dict[str, dict[str, str]] = {}
    records_by_entry: dict[str, list[dict[str, Any]]] = {}

    for provider in providers:
        for entry_type, definition in provider.entry_types().items():
            columns = dict(provider.columns(entry_type))
            if 'id' not in columns or 'type' not in columns:
                raise ValueError(
                    "Provider columns for entry type '" + entry_type + "' must cover at least 'id' and 'type'."
                )
            described = definition.properties
            for name in columns:
                if name not in described:
                    raise ValueError(
                        "Provider serves property '"
                        + name
                        + "' for entry type '"
                        + entry_type
                        + "' that is not described by its definition; custom properties must be added via "
                        + "EntryTypeDefinition.extended()."
                    )
            id_column = columns['id']
            rows: list[dict[str, Any]] = []
            for record in provider.records(entry_type):
                row = dict(record)
                # Normalize the id under the '__id' column simple_property_handlers
                # matches against, regardless of which column the provider uses.
                row['__id'] = row[id_column]
                rows.append(row)
            if entry_type in columns_by_entry:
                columns_by_entry[entry_type].update(columns)
                records_by_entry[entry_type].extend(rows)
                for name in columns:
                    if name not in served_map[entry_type]:
                        served_map[entry_type].append(name)
                        if name not in ('id', 'type'):
                            default_overrides[entry_type].append(name)
                continue
            columns_by_entry[entry_type] = columns
            records_by_entry[entry_type] = rows
            definitions[entry_type] = definition
            served = list(columns.keys())
            served_map[entry_type] = served
            default_overrides[entry_type] = [name for name in served if name not in ('id', 'type')]

    schema = build_served_schema(
        definitions,
        served_map,
        default_response_overrides=default_overrides,
        **options,
    )

    field_handlers: dict[str, HandlerTable] = {}
    sources: dict[str, tuple[EntrySource, ...]] = {}
    tables: dict[str, list[dict[str, Any]]] = {}
    for entry_type, columns in columns_by_entry.items():
        filter_columns = {name: column for name, column in columns.items() if name not in ('id', 'type')}
        field_handlers[entry_type] = simple_property_handlers(entry_type, filter_columns, schema.entry_info[entry_type])
        fields: dict[str, Callable[[Any], Any]] = {name: _column_extractor(column) for name, column in columns.items()}
        sources[entry_type] = (EntrySource(target=entry_type, fields=fields),)
        tables[entry_type] = records_by_entry[entry_type]

    return BackendAdapter(
        store=InMemoryStore(tables),
        sources=sources,
        schema=schema,
        field_handlers=field_handlers,
    )


def providers_from_registry() -> dict[str, Callable[..., EntryProvider]]:
    """Return the registered entry-provider factories keyed by their registered name.

    Resolves each factory registered via
    :func:`httk.core.register_entry_provider` (through ``httk.handlers.*``
    self-registration) into a callable. Providers need data, so applications
    instantiate them: ``providers_from_registry()["atomistic-structures"](data)``.
    """
    from httk.core._plugins import resolve_callable
    from httk.core.register import entry_providers, known_entry_providers

    return {name: resolve_callable(entry_providers.require(name).handler) for name in known_entry_providers()}
