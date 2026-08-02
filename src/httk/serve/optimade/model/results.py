from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from httk.core import FilterAst

if TYPE_CHECKING:
    from ..schema.served import ServedSchema


@dataclass(slots=True)
class ResultRow:
    """One entry result: its attribute values plus per-entry envelope data."""

    values: dict[str, Any]
    relationships: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    property_metadata: dict[str, Any] = field(default_factory=dict)


class QueryResults(Protocol):
    """The results of a query against a backend, as consumed by the entry endpoints.

    Iteration yields one :class:`~httk.serve.optimade.model.results.ResultRow` per entry; its ``values`` map
    OPTIMADE response-field names to values, and the ``id`` and ``type`` keys
    are always present.
    """

    @property
    def more_data_available(self) -> bool: ...

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


class OptimadeAdapter(Protocol):
    """Structural adapter contract consumed by the public serving helpers.

    Query execution may be backed by the ordinary Store/Searcher adapter or a
    storage federation with its own bounded paging policy.  The HTTP layer only
    needs the served schema and a callback implementing :class:`QueryFunction`.
    """

    @property
    def schema(self) -> "ServedSchema": ...

    def query_function(self) -> QueryFunction: ...
