"""A lightweight fake store/searcher implementation for tests.

``FakeSearcher`` records the expression trees added to it (for translation
tests) and serves a preloaded list of rows honouring offset/limit (for
execution tests). Expressions are plain nested tuples built by ``FakeColumn``.
"""

from collections.abc import Iterator
from typing import Any

from httk.data.query import ResultRow, ResultRowLike, ResultSetLike, SearchResult


class FakeExpression:
    def __init__(self, tree: tuple[Any, ...]) -> None:
        self.tree = tree

    def __and__(self, other: "FakeExpression") -> "FakeExpression":
        return FakeExpression(("AND", self.tree, other.tree))

    def __or__(self, other: "FakeExpression") -> "FakeExpression":
        return FakeExpression(("OR", self.tree, other.tree))

    def __invert__(self) -> "FakeExpression":
        return FakeExpression(("NOT", self.tree))

    def __repr__(self) -> str:
        return f"FakeExpression({self.tree!r})"


class FakeColumn:
    def __init__(self, name: str) -> None:
        self.name = name

    def _binary(self, op: str, other: Any) -> FakeExpression:
        if isinstance(other, FakeColumn):
            other = ("column", other.name)
        return FakeExpression((op, ("column", self.name), other))

    def __eq__(self, other: object) -> FakeExpression:  # type: ignore[override]
        return self._binary("eq", other)

    def __ne__(self, other: object) -> FakeExpression:  # type: ignore[override]
        return self._binary("ne", other)

    def __lt__(self, other: Any) -> FakeExpression:
        return self._binary("lt", other)

    def __le__(self, other: Any) -> FakeExpression:
        return self._binary("le", other)

    def __gt__(self, other: Any) -> FakeExpression:
        return self._binary("gt", other)

    def __ge__(self, other: Any) -> FakeExpression:
        return self._binary("ge", other)

    def __hash__(self) -> int:
        return hash(self.name)

    def startswith(self, other: Any) -> FakeExpression:
        return self._binary("startswith", other)

    def endswith(self, other: Any) -> FakeExpression:
        return self._binary("endswith", other)

    def contains(self, text: str) -> FakeExpression:
        return self._binary("contains", text)

    def has_any(self, *values: Any) -> FakeExpression:
        return FakeExpression(("has_any", ("column", self.name), values))

    def has_only(self, *values: Any) -> FakeExpression:
        return FakeExpression(("has_only", ("column", self.name), values))

    def __getattr__(self, name: str) -> "FakeColumn":
        # A field may refer to another record; chaining yields a field again.
        if name.startswith("_"):
            raise AttributeError(name)
        return FakeColumn(name)


class FakeVariable:
    def __init__(self, target: Any) -> None:
        self.target = target

    # Real methods, declared before the catch-all __getattr__ so they win: these
    # are reserved names, never row keys.
    def always_true(self) -> FakeExpression:
        return FakeExpression(("always_true",))

    def always_false(self) -> FakeExpression:
        return FakeExpression(("always_false",))

    def __getattr__(self, name: str) -> FakeColumn:
        return FakeColumn(name)


class FakeSearcher:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows: list[Any] = rows if rows is not None else []
        self.offset = 0
        self.limit: int | None = None
        self.variables: list[FakeVariable] = []
        self.outputs: list[tuple[FakeVariable | FakeColumn, str]] = []
        self.expressions: list[FakeExpression] = []
        self.sorts: list[tuple[str, bool]] = []

    def variable(self, target: Any) -> FakeVariable:
        variable = FakeVariable(target)
        self.variables.append(variable)
        return variable

    def output(self, variable: "FakeVariable | FakeColumn", name: str) -> None:
        self.outputs.append((variable, name))

    def add(self, expression: FakeExpression) -> None:
        self.expressions.append(expression)

    def count(self) -> int:
        return len(self.rows)

    def set_limit(self, limit: int) -> None:
        self.limit = limit

    def add_offset(self, offset: int) -> None:
        self.offset += offset

    def add_sort(self, column: FakeColumn, descending: bool) -> None:
        self.sorts.append((column.name, descending))

    def results(self, **outputs: Any) -> ResultSetLike:
        selected = [(output, name) for name, output in outputs.items()] if outputs else list(self.outputs)
        rows = self._sorted_rows()[self.offset :]
        if self.limit is not None and self.limit >= 0:
            rows = rows[: self.limit]
        if not selected:
            selected = [(FakeVariable(None), "row")]
        return FakeResultSet(rows, selected)

    def _sorted_rows(self) -> list[Any]:
        rows = list(self.rows)
        # Stable multi-key sort: apply keys in reverse declaration order so the
        # first-declared sort key is the most significant. None sorts first.
        for name, descending in reversed(self.sorts):
            def key(row: Any, n: str = name) -> tuple[bool, Any]:
                return getattr(row, n) is None, getattr(row, n)

            rows.sort(key=key, reverse=descending)
        return rows

    def _value(self, output: "FakeVariable | FakeColumn", row: Any) -> Any:
        return getattr(row, output.name) if isinstance(output, FakeColumn) else row

    def __iter__(self) -> Iterator[SearchResult]:
        rows = self._sorted_rows()[self.offset :]
        if self.limit is not None and self.limit >= 0:
            rows = rows[: self.limit]
        outputs = self.outputs if self.outputs else [(FakeVariable(None), "row")]
        names = tuple(name for _output, name in outputs)
        return iter(
            [SearchResult(tuple(self._value(output, row) for output, _name in outputs), names) for row in rows]
        )


class FakeStore:
    def __init__(self, rows_by_target: dict[Any, list[Any]] | None = None) -> None:
        self.rows_by_target = rows_by_target if rows_by_target is not None else {}
        self.searchers: list[FakeSearcher] = []

    def searcher(self) -> FakeSearcher:
        searcher = _TargetAwareFakeSearcher(self.rows_by_target)
        self.searchers.append(searcher)
        return searcher


class FakeResultSet:
    def __init__(self, rows: list[Any], outputs: list[tuple[Any, str]]) -> None:
        self._rows = rows
        self._outputs = outputs
        self._names = tuple(name for _output, name in outputs)

    def __len__(self) -> int:
        return len(self._rows)

    def _value(self, output: Any, row: Any) -> Any:
        return getattr(row, output.name) if isinstance(output, FakeColumn) else row

    def __iter__(self) -> Iterator[ResultRowLike]:
        return iter(
            [
                ResultRow(tuple(self._value(output, row) for output, _name in self._outputs), self._names)
                for row in self._rows
            ]
        )

    def first(self) -> ResultRowLike | None:
        return next(iter(self), None)

    def one(self) -> ResultRowLike:
        if not self._rows:
            raise LookupError("expected exactly one result, found none")
        if len(self._rows) > 1:
            raise LookupError("expected exactly one result, found more than one")
        return next(iter(self))

    def scalars(self, name: str | None = None) -> Iterator[Any]:
        if name is None:
            if len(self._names) != 1:
                raise ValueError("scalars() without a name requires exactly one output")
            name = self._names[0]
        index = self._names.index(name)
        return (row[index] for row in self)


class _TargetAwareFakeSearcher(FakeSearcher):
    """A fake searcher that picks up its rows when a variable is bound."""

    def __init__(self, rows_by_target: dict[Any, list[Any]]) -> None:
        super().__init__()
        self._rows_by_target = rows_by_target

    def variable(self, target: Any) -> FakeVariable:
        self.rows = list(self._rows_by_target.get(target, []))
        return super().variable(target)
