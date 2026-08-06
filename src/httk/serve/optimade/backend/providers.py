"""Build a :class:`~httk.serve.optimade.backend.adapter.BackendAdapter` from :class:`~httk.core.EntryProvider` sources.

This is the bridge from the neutral httk-core entry-provider contract to an
OPTIMADE-serving backend: it turns one or more providers into a fully wired
:class:`~httk.serve.optimade.backend.adapter.BackendAdapter` over an in-memory store, deriving the served schema,
the filter handlers, and the response-field extractors from each provider's
descriptions, property keys, and records. It is httk-serve's only dependency on
``httk.core`` beyond the shared runtime.
"""

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from httk.core import EntryProvider, EntryTypeDefinition, RelatedEntry
from httk.data.query.optimade_filters import (
    HandlerTable,
    relationship_id_handler,
)

from ..schema.served import build_served_schema
from ._property_handlers import value_aware_property_handlers
from .adapter import BackendAdapter, EntrySource
from .memory_store import InMemoryStore


def _key_extractor(key: str) -> Callable[[Any], Any]:
    """A field extractor reading ``key`` from a record mapping."""
    return lambda row: row.get(key)


def _relationships_extractor(
    relationships_by_id: Mapping[str, tuple[RelatedEntry, ...]],
) -> Callable[[Any], dict[str, list[dict[str, Any]]]]:
    """Build a per-row relationships extractor from a provider's id -> related-entries mapping.

    The returned callable maps a record (looked up by its ``__id`` key) to the
    :class:`~httk.serve.optimade.backend.adapter.EntrySource` relationships-block shape:
    the flat :class:`~httk.core.RelatedEntry` tuple is grouped by related entry
    type into ``{related_type: [{'id': ..., 'description'?: ..., 'role'?: ..., 'label'?: ...},
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
            if entry.label is not None:
                identifier['label'] = entry.label
            grouped.setdefault(entry.entry_type, []).append(identifier)
        return grouped

    return extract


def adapter_from_providers(providers: Iterable[EntryProvider], **options: Any) -> BackendAdapter:
    """Build a :class:`~httk.serve.optimade.backend.adapter.BackendAdapter` serving the given entry providers.

    Every provider's :meth:`~httk.core.EntryProvider.entry_types` become served
    entry types (described by their :class:`~httk.core.EntryTypeDefinition`), its
    :meth:`~httk.core.EntryProvider.property_keys` name the served subset and drive
    both the filter handlers (via
    :func:`~httk.data.query.optimade_filters.simple_property_handlers`) and the
    response-field extractors, and its
    :meth:`~httk.core.EntryProvider.records` are loaded into an
    :class:`~httk.serve.optimade.backend.memory_store.InMemoryStore`. Every served
    property MUST be described by the entry type's definition (a custom property
    must therefore live in an
    :meth:`~httk.core.EntryTypeDefinition.extended` definition); a
    :class:`ValueError` names any offender. All served properties beyond
    ``id``/``type`` are marked default-response. Extra keyword ``options`` (e.g.
    ``sortable``, ``recognized_prefixes``) are forwarded to
    :func:`~httk.serve.optimade.schema.served.build_served_schema`; every served
    property is sortable-capable, since the provider's property-key map is
    passed through as the source's
    :attr:`~httk.serve.optimade.backend.adapter.EntrySource.sort_keys`.

    Declared relationships (:meth:`~httk.core.EntryProvider.relationships`) are
    fully auto-wired for serving *and* filtering: for each entry type with
    declared relationships, a synthetic ``__rel_<related_type>`` id-list field
    is materialized on EVERY row of that entry type (an empty list when the row
    has no related entries of that type, so inverse set semantics are
    well-defined), and a ``'<related_type>.id'`` entry built with
    :func:`~httk.data.query.optimade_filters.relationship_id_handler` is merged into the
    entry type's derived filter-handler table (never overwriting an entry
    already present, mirroring how :class:`~httk.serve.optimade.backend.adapter.BackendAdapter`
    respects explicitly supplied handler tables). ``<related_type>.id HAS ...``
    filters — and, through the related-property resolver of
    :func:`~httk.serve.optimade.backend.translation.translate_filter`, depth-1
    relationship-property filters such as ``references.doi CONTAINS "10.1"`` —
    therefore work without any hand-wiring.
    """
    served_map: dict[str, list[str]] = {}
    definitions: dict[str, EntryTypeDefinition] = {}
    default_overrides: dict[str, list[str]] = {}
    keys_by_entry: dict[str, dict[str, str]] = {}
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
            property_keys = dict(provider.property_keys(entry_type))
            if 'id' not in property_keys or 'type' not in property_keys:
                raise ValueError(
                    "Provider property keys for entry type '" + entry_type + "' must cover at least 'id' and 'type'."
                )
            described = definition.properties
            for name in property_keys:
                if name not in described:
                    raise ValueError(
                        "Provider serves property '"
                        + name
                        + "' for entry type '"
                        + entry_type
                        + "' that is not described by its definition; custom properties must be added via "
                        + "EntryTypeDefinition.extended()."
                    )
            id_key = property_keys['id']
            rows: list[dict[str, Any]] = []
            for record in provider.records(entry_type):
                row = dict(record)
                # Normalize the id under the '__id' key simple_property_handlers
                # matches against, regardless of which key the provider uses.
                row['__id'] = row[id_key]
                rows.append(row)
            if entry_type in keys_by_entry:
                keys_by_entry[entry_type].update(property_keys)
                records_by_entry[entry_type].extend(rows)
                for name in property_keys:
                    if name not in served_map[entry_type]:
                        served_map[entry_type].append(name)
                        if name not in ('id', 'type'):
                            default_overrides[entry_type].append(name)
                continue
            keys_by_entry[entry_type] = property_keys
            records_by_entry[entry_type] = rows
            definitions[entry_type] = definition
            served = list(property_keys.keys())
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
    for entry_type, property_keys in keys_by_entry.items():
        filter_keys = {name: key for name, key in property_keys.items() if name not in ('id', 'type')}
        property_fulltypes = {
            name: prop.get('fulltype', 'string') for name, prop in schema.entry_info[entry_type]['properties'].items()
        }
        handlers = value_aware_property_handlers(entry_type, filter_keys, property_fulltypes)
        fields: dict[str, Callable[[Any], Any]] = {name: _key_extractor(key) for name, key in property_keys.items()}
        entry_relationships = relationships_by_entry.get(entry_type)
        relationships = _relationships_extractor(entry_relationships) if entry_relationships else None
        if entry_relationships:
            # Auto-wire relationship filtering: materialize a synthetic
            # '__rel_<related_type>' id-list field on every row (empty when the
            # row has no related entries of that type — the '__' namespace,
            # like '__id', cannot collide with served record keys) and register the
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
        # The in-memory store sorts on record keys, so the provider's
        # property-key map IS the sort mapping; without it any property the
        # served schema declares sortable would trip BackendAdapter.__post_init__.
        sources[entry_type] = (
            EntrySource(
                target=entry_type,
                fields=fields,
                sort_keys=dict(property_keys),
                relationships=relationships,
            ),
        )
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
    :func:`httk.core.register_entry_provider` (through ``httk.registry.*``
    self-registration) into a callable. Providers need data, so applications
    instantiate them: ``providers_from_registry()["atomistic-structures"](data)``.
    """
    from httk.core._plugins import resolve_callable
    from httk.core.register import entry_providers, known_entry_providers

    return {name: resolve_callable(entry_providers.require(name).handler) for name in known_entry_providers()}
