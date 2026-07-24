"""Build a :class:`~httk.optimade.backend.adapter.BackendAdapter` from :class:`~httk.core.EntryProvider` sources.

This is the bridge from the neutral httk-core entry-provider contract to an
OPTIMADE-serving backend: it turns one or more providers into a fully wired
:class:`~httk.optimade.backend.adapter.BackendAdapter` over an in-memory store, deriving the served schema,
the filter handlers, and the response-field extractors from each provider's
descriptions, columns, and records. It is httk-optimade's only dependency on
``httk.core`` beyond the shared runtime.
"""

from typing import Any, Callable, Iterable, Mapping

from httk.core import EntryProvider, EntryTypeDefinition, RelatedEntry
from httk.data.optimade_query import (
    HandlerTable,
    relationship_id_handler,
    simple_property_handlers,
)

from ..schema.served import build_served_schema
from .adapter import BackendAdapter, EntrySource
from .memory_store import InMemoryStore


def _column_extractor(column: str) -> Callable[[Any], Any]:
    """A field extractor reading ``column`` from a record mapping."""
    return lambda row: row.get(column)


def _relationships_extractor(
    relationships_by_id: Mapping[str, tuple[RelatedEntry, ...]],
) -> Callable[[Any], dict[str, list[dict[str, Any]]]]:
    """Build a per-row relationships extractor from a provider's id -> related-entries mapping.

    The returned callable maps a record (looked up by its ``__id`` column) to the
    :class:`~httk.optimade.backend.adapter.EntrySource` relationships-block shape:
    the flat :class:`~httk.core.RelatedEntry` tuple is grouped by related entry
    type into ``{related_type: [{'id': ..., 'description'?: ..., 'role'?: ...},
    ...]}`` (empty when the record has no related entries), passing the
    per-identifier metadata through to the rendered ``meta`` object.
    """

    def extract(row: Any) -> dict[str, list[dict[str, Any]]]:
        related = relationships_by_id.get(row.get('__id'))
        if not related:
            return {}
        grouped: dict[str, list[dict[str, Any]]] = {}
        for entry in related:
            identifier: dict[str, Any] = {'id': entry.id}
            if entry.description:
                identifier['description'] = entry.description
            if entry.role:
                identifier['role'] = entry.role
            grouped.setdefault(entry.entry_type, []).append(identifier)
        return grouped

    return extract


def adapter_from_providers(providers: Iterable[EntryProvider], **options: Any) -> BackendAdapter:
    """Build a :class:`~httk.optimade.backend.adapter.BackendAdapter` serving the given entry providers.

    Every provider's :meth:`~httk.core.EntryProvider.entry_types` become served
    entry types (described by their :class:`~httk.core.EntryTypeDefinition`), its
    :meth:`~httk.core.EntryProvider.columns` name the served subset and drive
    both the filter handlers (via
    :func:`~httk.data.optimade_query.simple_property_handlers`) and the
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

    Declared relationships (:meth:`~httk.core.EntryProvider.relationships`) are
    fully auto-wired for serving *and* filtering: for each entry type with
    declared relationships, a synthetic ``__rel_<related_type>`` id-list column
    is materialized on EVERY row of that entry type (an empty list when the row
    has no related entries of that type, so inverse set semantics are
    well-defined), and a ``'<related_type>.id'`` entry built with
    :func:`~httk.data.optimade_query.relationship_id_handler` is merged into the
    entry type's derived filter-handler table (never overwriting an entry
    already present, mirroring how :class:`~httk.optimade.backend.adapter.BackendAdapter`
    respects explicitly supplied handler tables). ``<related_type>.id HAS ...``
    filters — and, through the related-property resolver of
    :func:`~httk.optimade.backend.translation.translate_filter`, depth-1
    relationship-property filters such as ``references.doi CONTAINS "10.1"`` —
    therefore work without any hand-wiring.
    """
    served_map: dict[str, list[str]] = {}
    definitions: dict[str, EntryTypeDefinition] = {}
    default_overrides: dict[str, list[str]] = {}
    columns_by_entry: dict[str, dict[str, str]] = {}
    records_by_entry: dict[str, list[dict[str, Any]]] = {}
    relationships_by_entry: dict[str, dict[str, tuple[RelatedEntry, ...]]] = {}

    for provider in providers:
        for entry_type, definition in provider.entry_types().items():
            provider_relationships = provider.relationships(entry_type)
            if provider_relationships:
                # Merge semantics across providers: per-id replace — when a later
                # provider declares relationships for an id an earlier provider
                # already covered, the later provider's tuple wins wholesale.
                relationships_by_entry.setdefault(entry_type, {}).update(
                    {entry_id: tuple(entries) for entry_id, entries in provider_relationships.items()}
                )
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
        property_fulltypes = {
            name: prop.get('fulltype', 'string') for name, prop in schema.entry_info[entry_type]['properties'].items()
        }
        handlers = simple_property_handlers(entry_type, filter_columns, property_fulltypes)
        fields: dict[str, Callable[[Any], Any]] = {name: _column_extractor(column) for name, column in columns.items()}
        entry_relationships = relationships_by_entry.get(entry_type)
        relationships = _relationships_extractor(entry_relationships) if entry_relationships else None
        if entry_relationships:
            # Auto-wire relationship filtering: materialize a synthetic
            # '__rel_<related_type>' id-list column on every row (empty when the
            # row has no related entries of that type — the '__' namespace,
            # like '__id', cannot collide with served columns) and register the
            # matching '<related_type>.id' filter handler. setdefault keeps any
            # same-named handler that the derivation already produced.
            related_types = sorted(
                {related.entry_type for entries in entry_relationships.values() for related in entries}
            )
            for row in records_by_entry[entry_type]:
                row_related = entry_relationships.get(row['__id'], ())
                for related_type in related_types:
                    row['__rel_' + related_type] = [r.id for r in row_related if r.entry_type == related_type]
            for related_type in related_types:
                handlers.setdefault(related_type + '.id', relationship_id_handler('__rel_' + related_type))
        field_handlers[entry_type] = handlers
        sources[entry_type] = (EntrySource(target=entry_type, fields=fields, relationships=relationships),)
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
