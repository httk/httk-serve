"""Typed protocols for the store/searcher contract the OPTIMADE backend uses.

These mirror the query interface of the httk v1 database layer
(``httk.db`` ``FilteredCollection`` searchers), so that a future httk v2
database module can plug in by implementing them, and so that tests can use
lightweight fakes.
"""

from typing import Any, Iterator, Protocol

from ..model.results import QueryFunction, QueryResults

__all__ = [
    "SearchExpression",
    "SearchColumn",
    "SearchVariable",
    "Searcher",
    "Store",
    "QueryFunction",
    "QueryResults",
]


class SearchExpression(Protocol):
    def __and__(self, other: "SearchExpression") -> "SearchExpression": ...

    def __or__(self, other: "SearchExpression") -> "SearchExpression": ...

    def __invert__(self) -> "SearchExpression": ...


class SearchColumn(Protocol):
    """A queryable column of a search variable.

    In addition to the methods below, columns support the rich comparison
    operators (``==``, ``!=``, ``<``, ``<=``, ``>``, ``>=``) and
    ``startswith``/``endswith``, returning :class:`SearchExpression`. The
    handlers invoke those via ``getattr(column, '__eq__')(value)`` since the
    comparison dunders cannot be typed as expression-returning.
    """

    def has_any(self, *values: Any) -> SearchExpression: ...

    def has_inv_any(self, *values: Any) -> SearchExpression: ...

    def has_only(self, *values: Any) -> SearchExpression: ...

    def has_inv_only(self, *values: Any) -> SearchExpression: ...

    def like(self, pattern: str) -> SearchExpression: ...


class SearchVariable(Protocol):
    """A query variable bound to a target table/type; attribute access yields columns."""

    def __getattr__(self, name: str) -> SearchColumn: ...


class Searcher(Protocol):
    """A single query under construction, and its results once iterated.

    Iteration yields items where ``item[0][0]`` is the matched row object.
    """

    offset: int

    def variable(self, target: Any) -> SearchVariable: ...

    def output(self, variable: SearchVariable, name: str) -> None: ...

    def add(self, expression: SearchExpression) -> None: ...

    def add_all(self, expression: SearchExpression) -> None: ...

    def count(self) -> int: ...

    def set_limit(self, limit: int) -> None: ...

    def add_offset(self, offset: int) -> None: ...

    def __iter__(self) -> Iterator[Any]: ...


class Store(Protocol):
    def searcher(self) -> Searcher: ...
