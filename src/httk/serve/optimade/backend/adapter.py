"""Adapt store/searcher implementations to the OPTIMADE backend contract."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from httk.core.optimade import FilterAst
from httk.data.query import Store
from httk.data.query.optimade_filters import HandlerTable

from ..model.results import QueryFunction, QueryResults
from ..schema.served import ServedSchema

type FieldExtractor = Callable[[Any], Any]


@dataclass(frozen=True)
class EntrySource:
    """Describe one queryable source behind an OPTIMADE entry endpoint.

    ``target`` is what gets passed to ``searcher.variable()``; ``fields`` maps
    OPTIMADE response-field names to extractors applied to matched row objects.
    ``relationships``, when set, is an extractor mapping a matched row to a
    dictionary keyed by related entry type, each value a list of
    ``{'id': str, 'description': str?, 'role': str?}`` dictionaries.
    ``sort_keys`` maps response-field names to the backend field names to sort
    on. ``property_metadata`` maps response-field names to extractors returning
    the per-property metadata dictionary for a matched row (or ``None`` when
    there is no metadata for that row).

    :param target: Store-specific target passed to ``searcher.variable``.
    :param fields: Response-field extractors applied to matched rows.
    :param sort_keys: Response-field to backend-sort-field mappings.
    :param relationships: Optional extractor for related-resource data.
    :param property_metadata: Optional per-property metadata extractors.
    """

    target: Any
    fields: Mapping[str, FieldExtractor]
    sort_keys: Mapping[str, str] = field(default_factory=dict)
    relationships: FieldExtractor | None = None
    property_metadata: Mapping[str, FieldExtractor] = field(default_factory=dict)


@dataclass(frozen=True)
class BackendAdapter:
    """Bind a store to the OPTIMADE entry endpoints it serves.

    ``sources`` maps entry endpoint names (e.g. ``'structures'``) to the
    sources queried for that endpoint; an endpoint with several sources (e.g.
    several calculation result types) is queried across all of them.

    ``schema`` is required: it declares the served entry types and properties.
    ``field_handlers`` maps each entry type to its filter-handler table. When
    omitted (left empty) it is derived from ``schema`` via
    :func:`~httk.data.query.optimade_filters.simple_property_handlers`, using an
    identity property-key map (each property is filtered against a backend field
    of the same name); a backend whose field names differ, or that wants finer
    control, supplies its own tables instead.

    :param store: Store implementing the neutral query protocol.
    :param sources: Queryable sources keyed by entry endpoint.
    :param schema: Required schema describing served entries and properties.
    :param field_handlers: Optional filter handlers keyed by entry endpoint.
    """

    store: Store
    sources: Mapping[str, Sequence[EntrySource]]
    schema: ServedSchema
    field_handlers: Mapping[str, HandlerTable] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.field_handlers:
            from ._property_handlers import value_aware_property_handlers

            derived: dict[str, HandlerTable] = {}
            for entry in self.schema.all_entries:
                properties = self.schema.entry_info[entry]['properties']
                property_keys = {name: name for name in properties if name not in ('id', 'type')}
                property_fulltypes = {name: prop.get('fulltype', 'string') for name, prop in properties.items()}
                derived[entry] = value_aware_property_handlers(entry, property_keys, property_fulltypes)
            object.__setattr__(self, 'field_handlers', derived)

        # Every property declared sortable for an entry type must have a
        # backend field mapping in every source of that entry type.
        for entry, sortable in self.schema.sortable_response_fields.items():
            if not sortable:
                continue
            for source in self.sources.get(entry, ()):
                for name in sortable:
                    if name not in source.sort_keys:
                        raise ValueError(
                            "Property '"
                            + name
                            + "' is marked sortable for entry type '"
                            + entry
                            + "' but has no sort_keys mapping in one of its sources."
                        )

    def query_function(self) -> QueryFunction:
        """Return the callback that executes queries through this adapter.

        :return: Query callback consumed by the OPTIMADE request engine.
        """

        from .execution import execute_query

        def query(
            entries: list[str],
            response_fields: list[str],
            unknown_response_fields: list[str],
            page_limit: int,
            page_offset: int,
            filter_ast: FilterAst | None = None,
            *,
            sort: Sequence[tuple[str, bool]] | None = None,
            debug: bool = False,
        ) -> QueryResults:
            return execute_query(
                self,
                entries,
                response_fields,
                unknown_response_fields,
                page_limit,
                page_offset,
                filter_ast,
                sort=sort,
                debug=debug,
            )

        return query
