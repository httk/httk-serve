from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from httk.core.optimade import FilterAst

if TYPE_CHECKING:
    from ..schema.served import ServedSchema


@dataclass(slots=True)
class ResultRow:
    """Represent one entry result and its envelope data.

    :param values: Response-field values keyed by OPTIMADE property name.
    :param relationships: Related resources keyed by entry type.
    :param property_metadata: Per-property metadata keyed by response field.
    """

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
    def more_data_available(self) -> bool:
        """Report whether another page is available."""

        ...

    def count(self) -> int:
        """Return the total number of matches before pagination."""

        ...

    def __iter__(self) -> Iterator[ResultRow]:
        """Yield the current page as result rows."""

        ...


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
        as_of: int | None = None,
        sort: Sequence[tuple[str, bool]] | None = None,
        revisions: bool = False,
        immutable_id: str | None = None,
        debug: bool = False,
    ) -> QueryResults:
        """Execute one validated entry query.

        :param entries: Entry endpoints to query.
        :param response_fields: Recognized fields to return.
        :param unknown_response_fields: Unknown fields to return as null.
        :param page_limit: Maximum page size.
        :param page_offset: Number of matches to skip.
        :param filter_ast: Parsed filter, when one was requested.
        :param as_of: Nanosecond timestamp cutoff for a query snapshot; honored by
            timestamp-capable stored backends, served current-state by
            timestamp-disabled federation sources, and ignored by generic
            provider backends.
        :param sort: Field names paired with descending flags.
        :param revisions: Select all stored revisions rather than latest lineages.
        :param immutable_id: Select one immutable stored revision when supplied.
        :param debug: Enable backend diagnostics.
        :return: Query results for the requested page.
        """

        ...


class OptimadeAdapter(Protocol):
    """Structural adapter contract consumed by the public serving helpers.

    Query execution may be backed by the ordinary Store/Searcher adapter or a
    storage federation with its own bounded paging policy.  The HTTP layer only
    needs the served schema and a callback implementing
    :class:`~httk.serve.optimade.model.results.QueryFunction`.
    """

    @property
    def schema(self) -> "ServedSchema":
        """Return the schema supplied by the adapter."""

        ...

    def query_function(self) -> QueryFunction:
        """Return the callback used to execute entry queries."""

        ...
