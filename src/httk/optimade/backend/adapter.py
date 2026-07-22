from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..filter.parser import FilterAst
from ..model.results import QueryFunction, QueryResults
from ..schema.served import ServedSchema, default_served_schema
from .handlers import HandlerTable, default_field_handlers
from .protocols import Store

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
    """

    store: Store
    sources: Mapping[str, Sequence[EntrySource]]
    field_handlers: Mapping[str, HandlerTable] = field(default_factory=default_field_handlers)
    schema: ServedSchema = field(default_factory=default_served_schema)

    def __post_init__(self) -> None:
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
