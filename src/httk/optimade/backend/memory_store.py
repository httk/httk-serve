"""A generic in-memory store implementing the httk-optimade backend protocols.

Rows are plain dicts keyed by backend column names, and search expressions
evaluate as predicates over those rows. It is the reference
:class:`~httk.optimade.backend.protocols.Store` implementation: it backs the
example demo server and is what
:func:`~httk.optimade.backend.providers.adapter_from_providers` loads an
:class:`~httk.core.EntryProvider`'s records into.

Set-operation caveat: the httk database layer expresses NOT-inverted and
"for all" set semantics through inverse relations plus a post filter
(``searcher.add_all``). This in-memory store instead evaluates set predicates
exactly and ignores ``add_all``; for that to compose correctly with the
translation layer, ``has_inv_any``/``has_inv_only`` behave like their
non-inverse counterparts (the surrounding NOT then produces the correct
result).
"""

import re
from typing import Any, Callable, Iterator

Row = dict[str, Any]
Predicate = Callable[[Row], bool]


class MemoryExpression:
    def __init__(self, predicate: Predicate) -> None:
        self.predicate = predicate

    def __and__(self, other: "MemoryExpression") -> "MemoryExpression":
        return MemoryExpression(lambda row: self.predicate(row) and other.predicate(row))

    def __or__(self, other: "MemoryExpression") -> "MemoryExpression":
        return MemoryExpression(lambda row: self.predicate(row) or other.predicate(row))

    def __invert__(self) -> "MemoryExpression":
        return MemoryExpression(lambda row: not self.predicate(row))


def _like_to_regex(pattern: str) -> "re.Pattern[str]":
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        if c == "%":
            out.append(".*")
        elif c == "_":
            out.append(".")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$", re.DOTALL)


class MemoryColumn:
    def __init__(self, name: str) -> None:
        self.name = name

    def _value(self, row: Row) -> Any:
        return row.get(self.name)

    def _compare(self, other: Any, compare: Callable[[Any, Any], bool]) -> MemoryExpression:
        if isinstance(other, MemoryColumn):
            return MemoryExpression(lambda row: compare(self._value(row), other._value(row)))
        return MemoryExpression(lambda row: compare(self._value(row), other))

    def __eq__(self, other: object) -> MemoryExpression:  # type: ignore[override]
        return self._compare(other, lambda a, b: a == b)

    def __ne__(self, other: object) -> MemoryExpression:  # type: ignore[override]
        return self._compare(other, lambda a, b: a != b)

    def __lt__(self, other: Any) -> MemoryExpression:
        return self._compare(other, lambda a, b: a is not None and a < b)

    def __le__(self, other: Any) -> MemoryExpression:
        return self._compare(other, lambda a, b: a is not None and a <= b)

    def __gt__(self, other: Any) -> MemoryExpression:
        return self._compare(other, lambda a, b: a is not None and a > b)

    def __ge__(self, other: Any) -> MemoryExpression:
        return self._compare(other, lambda a, b: a is not None and a >= b)

    def __hash__(self) -> int:
        return hash(self.name)

    def startswith(self, other: str) -> MemoryExpression:
        return self._compare(other, lambda a, b: isinstance(a, str) and a.startswith(b))

    def endswith(self, other: str) -> MemoryExpression:
        return self._compare(other, lambda a, b: isinstance(a, str) and a.endswith(b))

    def like(self, pattern: str) -> MemoryExpression:
        regex = _like_to_regex(pattern)
        return MemoryExpression(lambda row: isinstance(self._value(row), str) and bool(regex.match(self._value(row))))

    def has_any(self, *values: Any) -> MemoryExpression:
        return MemoryExpression(lambda row: bool(set(self._value(row) or ()) & set(values)))

    def has_inv_any(self, *values: Any) -> MemoryExpression:
        return self.has_any(*values)

    def has_only(self, *values: Any) -> MemoryExpression:
        return MemoryExpression(lambda row: set(self._value(row) or ()) <= set(values))

    def has_inv_only(self, *values: Any) -> MemoryExpression:
        return self.has_only(*values)


class MemoryVariable:
    def __init__(self, target: str) -> None:
        self.target = target

    def __getattr__(self, name: str) -> MemoryColumn:
        return MemoryColumn(name)


class MemorySearcher:
    def __init__(self, tables: dict[str, list[Row]]) -> None:
        self._tables = tables
        self._rows: list[Row] = []
        self._expressions: list[MemoryExpression] = []
        self._sorts: list[tuple[MemoryColumn, bool]] = []
        self.offset = 0
        self._limit: int | None = None

    def variable(self, target: Any) -> MemoryVariable:
        self._rows = self._tables.get(target, [])
        return MemoryVariable(target)

    def output(self, variable: MemoryVariable, name: str) -> None:
        pass

    def add(self, expression: MemoryExpression) -> None:
        self._expressions.append(expression)

    def add_all(self, expression: MemoryExpression) -> None:
        # Post filtering is not needed: set predicates evaluate exactly here.
        pass

    def add_sort(self, column: MemoryColumn, descending: bool) -> None:
        self._sorts.append((column, descending))

    def _matches(self) -> list[Row]:
        rows = [row for row in self._rows if all(e.predicate(row) for e in self._expressions)]
        # Stable multi-key sort: apply keys in reverse declaration order so the
        # first-declared sort key is the most significant. None sorts first.
        for column, descending in reversed(self._sorts):

            def key(row: Row, c: MemoryColumn = column) -> tuple[bool, Any]:
                value = c._value(row)
                return (value is None, value)

            rows = sorted(rows, key=key, reverse=descending)
        return rows

    def count(self) -> int:
        return len(self._matches())

    def set_limit(self, limit: int) -> None:
        self._limit = limit

    def add_offset(self, offset: int) -> None:
        self.offset += offset

    def __iter__(self) -> Iterator[Any]:
        rows = self._matches()[self.offset :]
        if self._limit is not None and self._limit >= 0:
            rows = rows[: self._limit]
        return iter([((row,),) for row in rows])


class InMemoryStore:
    """A store over dict rows: ``InMemoryStore({'structures': [ {...}, ... ]})``."""

    def __init__(self, tables: dict[str, list[Row]]) -> None:
        self.tables = tables

    def searcher(self) -> MemorySearcher:
        return MemorySearcher(self.tables)
