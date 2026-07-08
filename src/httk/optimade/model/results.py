from typing import Any, Iterator, Protocol

from ..filter.parser import FilterAst


class QueryResults(Protocol):
    """The results of a query against a backend, as consumed by the entry endpoints.

    Iteration yields one dict per entry, mapping OPTIMADE response-field names
    to values; the ``id`` and ``type`` keys are always present.
    """

    more_data_available: bool

    def count(self) -> int: ...

    def __iter__(self) -> Iterator[dict[str, Any]]: ...


class QueryFunction(Protocol):
    """The callback seam through which the request engine runs queries on a backend."""

    def __call__(
        self,
        entries: list[str],
        response_fields: list[str],
        unknown_response_fields: list[str],
        page_limit: int,
        page_offset: int,
        filter_ast: FilterAst | None = None,
        *,
        debug: bool = False,
    ) -> QueryResults: ...
