from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..filter.parser import FilterAst
from ..model.results import QueryFunction, QueryResults
from .handlers import HandlerTable, default_field_handlers
from .protocols import Store

FieldExtractor = Callable[[Any], Any]


@dataclass(frozen=True)
class EntrySource:
    """One queryable source (table/type) behind an OPTIMADE entry endpoint.

    ``target`` is what gets passed to ``searcher.variable()``; ``fields`` maps
    OPTIMADE response-field names to extractors applied to matched row objects.
    """

    target: Any
    fields: Mapping[str, FieldExtractor]


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
                debug=debug,
            )

        return query
