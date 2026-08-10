"""A generic in-memory store implementing the httk store/searcher protocols.

Rows are plain dicts keyed by backend record keys, and search expressions
evaluate as predicates over those rows. It is the reference
:class:`~httk.data.query.Store` implementation: it backs the
example demo server and is what
:func:`~httk.serve.optimade.backend.providers.adapter_from_providers` loads an
:class:`~httk.core.EntryProvider`'s records into.

Set operations evaluate exactly here — ``has_any``/``has_only`` are plain set
predicates over the row's list value, and ``~`` negates them directly. The SQL
backend needs an aggregate rendering and a second (HAVING) evaluation position
to say the same thing, but that is entirely its own business: the neutral
protocol only ever hands a store one expression per ``searcher.add`` call.

String matching is literal here too (``contains``/``startswith``/``endswith``
are plain :class:`str` operations), which is exactly what the neutral protocol
promises — no pattern language is involved at any point.

Iteration yields :class:`~httk.data.query.SearchResult` values, one entry in
``values`` per :meth:`MemorySearcher.output` call: a variable output yields the
whole row dict, a field output the row's value for that field.
"""

from collections.abc import Callable, Iterator
from typing import Any, NoReturn

from httk.data.query import (
    MultipleResultsError,
    NoResultError,
    ResultRow,
    ResultRowLike,
    ResultSetLike,
    SearchResult,
)

Row = dict[str, Any]
Predicate = Callable[[Row], bool]


class MemoryExpression:
    """Represent a boolean predicate over an in-memory row.

    :param predicate: Function returning whether a row matches.
    """

    def __init__(self, predicate: Predicate) -> None:
        self.predicate = predicate

    def __and__(self, other: "MemoryExpression") -> "MemoryExpression":
        return MemoryExpression(lambda row: self.predicate(row) and other.predicate(row))

    def __or__(self, other: "MemoryExpression") -> "MemoryExpression":
        return MemoryExpression(lambda row: self.predicate(row) or other.predicate(row))

    def __invert__(self) -> "MemoryExpression":
        return MemoryExpression(lambda row: not self.predicate(row))


class MemoryField:
    """Represent a named field in an in-memory row.

    :param name: Row key addressed by the field.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def _value(self, row: Row) -> Any:
        return row.get(self.name)

    def _compare(self, other: Any, compare: Callable[[Any, Any], bool]) -> MemoryExpression:
        if isinstance(other, MemoryField):
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
        """Match string values that start with ``other``.

        :param other: Prefix to match.
        :return: Matching predicate.
        """

        return self._compare(other, lambda a, b: isinstance(a, str) and a.startswith(b))

    def endswith(self, other: str) -> MemoryExpression:
        """Match string values that end with ``other``.

        :param other: Required string suffix.
        :return: Matching predicate.
        """

        return self._compare(other, lambda a, b: isinstance(a, str) and a.endswith(b))

    def contains(self, other: str) -> MemoryExpression:
        """Match string values containing ``other``.

        :param other: Required substring.
        :return: Matching predicate.
        """

        return self._compare(other, lambda a, b: isinstance(a, str) and b in a)

    def is_in(self, *values: Any) -> MemoryExpression:
        """Match a scalar field against supplied values.

        :param \\*values: Candidate scalar values.
        :return: Matching predicate.
        """
        return MemoryExpression(lambda row: self._value(row) in values)

    def has(self, value: Any) -> MemoryExpression:
        """Match list values containing ``value``.

        :param value: Required list member.
        :return: Matching predicate.
        """

        return MemoryExpression(lambda row: value in (self._value(row) or ()))

    def has_any(self, *values: Any) -> MemoryExpression:
        """Match list values containing any supplied member.

        :param \\*values: Candidate list members.
        :return: Matching predicate.
        """

        return MemoryExpression(lambda row: bool(set(self._value(row) or ()) & set(values)))

    def has_only(self, *values: Any) -> MemoryExpression:
        """Match list values containing no members outside the supplied set.

        :param \\*values: Allowed list members.
        :return: Matching predicate.
        """

        return MemoryExpression(lambda row: set(self._value(row) or ()) <= set(values))

    def __getattr__(self, name: str) -> NoReturn:
        """Refuse to chain: rows here are flat, so no field refers to a record.

        The :class:`~httk.data.query.SearchField` contract allows attribute
        access because a field may be a reference in a store that has them;
        this one keeps plain dict rows, so it says so explicitly instead of
        failing as though the name were a mistyped method.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        raise AttributeError(
            f"{self.name!r} is a value in a flat row, not a reference to another record, "
            f"so {name!r} cannot be looked up through it"
        )


class MemoryVariable:
    """A query variable over one table of rows; attribute access yields fields.

    ``always_true``/``always_false`` are real methods, declared before the
    catch-all ``__getattr__`` so they win over it — they are reserved names
    that never resolve to a row key.

    :param target: Table name used by the searcher.
    """

    def __init__(self, target: str) -> None:
        self.target = target

    def always_true(self) -> MemoryExpression:
        """Return a predicate that matches every row.

        :return: Always-true predicate.
        """

        return MemoryExpression(lambda row: True)

    def always_false(self) -> MemoryExpression:
        """Return a predicate that matches no row.

        :return: Always-false predicate.
        """

        return MemoryExpression(lambda row: False)

    def __getattr__(self, name: str) -> MemoryField:
        return MemoryField(name)


class MemorySearcher:
    """Build and execute a query over in-memory row tables.

    :param tables: Row lists keyed by table name.
    """

    def __init__(self, tables: dict[str, list[Row]]) -> None:
        self._tables = tables
        self._rows: list[Row] = []
        self._expressions: list[MemoryExpression] = []
        self._sorts: list[tuple[MemoryField, bool]] = []
        self._outputs: list[tuple[str, Callable[[Row], Any]]] = []
        self._output_values: list[tuple[str, MemoryVariable | MemoryField]] = []
        self.offset = 0
        self._limit: int | None = None

    def variable(self, target: Any) -> MemoryVariable:
        """Select a table and return its query variable.

        :param target: Table name to select.
        :return: Variable addressing the selected table.
        """

        self._rows = self._tables.get(target, [])
        return MemoryVariable(target)

    def output(self, variable: "MemoryVariable | MemoryField", name: str) -> None:
        """Append a whole-row or field output.

        :param variable: Variable for a whole row or field for one value.
        :param name: Output name.
        :raises TypeError: If ``variable`` is neither a variable nor a field.
        """
        if isinstance(variable, MemoryField):
            memory_field = variable
            self._outputs.append((name, lambda row: row.get(memory_field.name)))
            self._output_values.append((name, variable))
        elif isinstance(variable, MemoryVariable):
            self._outputs.append((name, lambda row: row))
            self._output_values.append((name, variable))
        else:
            raise TypeError(f"output() takes a search variable or a search field, got {type(variable).__name__}")

    def add(self, expression: MemoryExpression) -> None:
        """Add a predicate to the query.

        :param expression: Predicate to apply to each row.
        """

        self._expressions.append(expression)

    def add_sort(self, field: MemoryField, descending: bool) -> None:
        """Append a sort key.

        :param field: Field used for ordering.
        :param descending: Sort in descending order when true.
        """

        self._sorts.append((field, descending))

    def _matches(self) -> list[Row]:
        rows = [row for row in self._rows if all(e.predicate(row) for e in self._expressions)]
        # Stable multi-key sort: apply keys in reverse declaration order so the
        # first-declared sort key is the most significant. None always sorts last.
        for sort_field, descending in reversed(self._sorts):
            present = [row for row in rows if sort_field._value(row) is not None]
            missing = [row for row in rows if sort_field._value(row) is None]
            present = sorted(present, key=sort_field._value, reverse=descending)
            rows = present + missing
        return rows

    def count(self) -> int:
        """Return the number of rows matching the current query.

        :return: Number of matching rows before paging.
        """

        return len(self._matches())

    def set_limit(self, limit: int) -> None:
        """Set the maximum number of rows returned by this query.

        :param limit: Maximum result count; a negative value means unbounded.
        """

        self._limit = limit

    def add_offset(self, offset: int) -> None:
        """Advance the query offset.

        :param offset: Number of matching rows to skip.
        """

        self.offset += offset

    def results(self, **outputs: Any) -> ResultSetLike:
        """Return a result set for selected outputs.

        :param \\*\\*outputs: Optional output names mapped to variables or fields.
        :return: Materialized result set.
        :raises TypeError: If an output is not a variable or field.
        :raises ValueError: If no outputs are selected.
        """
        if outputs:
            selected: list[tuple[str, MemoryVariable | MemoryField]] = []
            for name, value in outputs.items():
                if not isinstance(value, (MemoryVariable, MemoryField)):
                    raise TypeError(f"results() output {name!r} is not a search variable or field")
                selected.append((name, value))
        else:
            selected = list(self._output_values)
        if not selected:
            raise ValueError("this searcher has no outputs; declare outputs or pass them to results()")
        rows = self._matches()[self.offset :]
        if self._limit is not None and self._limit >= 0:
            rows = rows[: self._limit]
        return MemoryResultSet(rows, selected)

    def __iter__(self) -> Iterator[SearchResult]:
        """Yield one :class:`~httk.data.query.SearchResult` per match, in output order."""
        if not self._outputs:
            raise ValueError("this searcher has no outputs; call output() before iterating")
        rows = self._matches()[self.offset :]
        if self._limit is not None and self._limit >= 0:
            rows = rows[: self._limit]
        names = tuple(name for name, _extractor in self._outputs)
        return iter([SearchResult(tuple(extractor(row) for _name, extractor in self._outputs), names) for row in rows])


class InMemoryStore:
    """Provide a store over dictionary rows.

    :param tables: Row lists keyed by table name.
    """

    def __init__(self, tables: dict[str, list[Row]]) -> None:
        self.tables = tables

    def searcher(self) -> MemorySearcher:
        """Create a searcher over this store's tables.

        :return: Fresh in-memory searcher.
        """

        return MemorySearcher(self.tables)


class MemoryResultSet:
    """Represent rows projected from an in-memory query.

    :param rows: Matching rows after paging.
    :param outputs: Named variables or fields projected from each row.
    """

    def __init__(self, rows: list[Row], outputs: list[tuple[str, MemoryVariable | MemoryField]]) -> None:
        self._rows = rows
        self._outputs = outputs
        self._names = tuple(name for name, _output in outputs)

    def __len__(self) -> int:
        """Return the number of rows in the result set.

        :return: Result count after paging.
        """

        return len(self._rows)

    def _value(self, output: MemoryVariable | MemoryField, row: Row) -> Any:
        return row if isinstance(output, MemoryVariable) else row.get(output.name)

    def __iter__(self) -> Iterator[ResultRowLike]:
        """Yield projected rows in output order."""

        return iter(
            [
                ResultRow(tuple(self._value(output, row) for _name, output in self._outputs), self._names)
                for row in self._rows
            ]
        )

    def __getitem__(self, item: int | slice) -> Any:
        """Return one row or a sliced result set.

        :param item: Requested row selection.
        :return: Selected row or derived result set.
        """

        if isinstance(item, slice):
            return MemoryResultSet(self._rows[item], self._outputs)
        return list(self)[item]

    def first(self) -> ResultRowLike | None:
        """Return the first result, if present.

        :return: First result or ``None``.
        """

        return next(iter(self), None)

    def one(self) -> ResultRowLike:
        """Return the only result.

        :return: The sole result.
        :raises httk.data.NoResultError: If the result set is empty.
        :raises httk.data.MultipleResultsError: If it has more than one result.
        """

        if not self._rows:
            raise NoResultError("expected exactly one result, found none")
        if len(self._rows) > 1:
            raise MultipleResultsError("expected exactly one result, found more than one")
        return next(iter(self))

    def scalars(self, name: str | None = None) -> Iterator[Any]:
        """Iterate one named scalar output from each result.

        :param name: Output name, or ``None`` when exactly one output exists.
        :return: Iterator over scalar values.
        :raises KeyError: If ``name`` is not declared.
        :raises ValueError: If no name is given and multiple outputs exist.
        """

        if name is None:
            if len(self._names) != 1:
                raise ValueError(f"scalars() without a name requires exactly one output; declared: {self._names}")
            name = self._names[0]
        try:
            index = self._names.index(name)
        except ValueError:
            raise KeyError(name) from None
        return (row[index] for row in self)

    def column(self, name: str) -> NoReturn:
        """Reject SQL-style column access for in-memory results.

        :param name: Unsupported column name.
        :raises NotImplementedError: In-memory results have no column proxy.
        """

        raise NotImplementedError("the in-memory result store does not provide SQL ResultColumn objects")

    def cursor(self) -> NoReturn:
        """Reject SQL-style cursor access for in-memory results.

        :raises NotImplementedError: In-memory results have no cursor proxy.
        """

        raise NotImplementedError("the in-memory result store does not provide cursor proxies")
