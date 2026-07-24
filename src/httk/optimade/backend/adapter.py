from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from httk.data.query import Store

from ..filter.parser import FilterAst
from ..model.results import QueryFunction, QueryResults
from ..schema.served import ServedSchema
from .handlers import HandlerTable

type FieldExtractor = Callable[[Any], Any]


@dataclass(frozen=True)
class EntrySource:
    """One queryable source (table/type) behind an OPTIMADE entry endpoint.

    ``target`` is what gets passed to ``searcher.variable()``; ``fields`` maps
    OPTIMADE response-field names to extractors applied to matched row objects.
    ``relationships``, when set, is an extractor mapping a matched row to a
    dictionary keyed by related entry type, each value a list of
    ``{'id': str, 'description': str?, 'role': str?}`` dictionaries.
    ``property_metadata`` maps response-field names to extractors returning the
    per-property metadata dictionary for a matched row (or ``None`` when there
    is no metadata for that row).
    """

    target: Any
    fields: Mapping[str, FieldExtractor]
    sort_columns: Mapping[str, str] = field(default_factory=dict)
    relationships: FieldExtractor | None = None
    property_metadata: Mapping[str, FieldExtractor] = field(default_factory=dict)


@dataclass(frozen=True)
class BackendAdapter:
    """Binds a store to the OPTIMADE entry endpoints it serves.

    ``sources`` maps entry endpoint names (e.g. ``'structures'``) to the
    sources queried for that endpoint; an endpoint with several sources (e.g.
    several calculation result types) is queried across all of them.

    ``schema`` is required: it declares the served entry types and properties.
    ``field_handlers`` maps each entry type to its filter-handler table. When
    omitted (left empty) it is derived from ``schema`` via
    :func:`~httk.optimade.backend.handlers.simple_property_handlers`, using an
    identity column map (each property is filtered against a backend column of
    the same name); a backend whose columns differ, or that wants finer control,
    supplies its own tables instead.
    """

    store: Store
    sources: Mapping[str, Sequence[EntrySource]]
    schema: ServedSchema
    field_handlers: Mapping[str, HandlerTable] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.field_handlers:
            from .handlers import simple_property_handlers

            derived: dict[str, HandlerTable] = {}
            for entry in self.schema.all_entries:
                entry_info = self.schema.entry_info[entry]
                columns = {name: name for name in entry_info['properties'] if name not in ('id', 'type')}
                derived[entry] = simple_property_handlers(entry, columns, entry_info)
            object.__setattr__(self, 'field_handlers', derived)

        # Every property declared sortable for an entry type must have a
        # backend column mapping in every source of that entry type.
        for entry, sortable in self.schema.sortable_response_fields.items():
            if not sortable:
                continue
            for source in self.sources.get(entry, ()):
                for name in sortable:
                    if name not in source.sort_columns:
                        raise ValueError(
                            "Property '"
                            + name
                            + "' is marked sortable for entry type '"
                            + entry
                            + "' but has no sort_columns mapping in one of its sources."
                        )

    def query_function(self) -> QueryFunction:
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
