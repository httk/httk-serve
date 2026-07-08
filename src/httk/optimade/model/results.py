from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol, Sequence

from ..filter.parser import FilterAst


@dataclass(slots=True)
class ResultRow:
    """One entry result: its attribute values plus per-entry envelope data."""

    values: dict[str, Any]
    relationships: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    property_metadata: dict[str, Any] = field(default_factory=dict)


class QueryResults(Protocol):
    """The results of a query against a backend, as consumed by the entry endpoints.

    Iteration yields one :class:`ResultRow` per entry; its ``values`` map
    OPTIMADE response-field names to values, and the ``id`` and ``type`` keys
    are always present.
    """

    more_data_available: bool

    def count(self) -> int: ...

    def __iter__(self) -> Iterator[ResultRow]: ...


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
        sort: Sequence[tuple[str, bool]] | None = None,
        debug: bool = False,
    ) -> QueryResults: ...
