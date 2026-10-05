"""A generic in-memory store implementing the httk store/searcher protocols.

Rows are plain dicts keyed by backend record keys, and search expressions
evaluate as predicates over those rows. It is the reference
:class:`~httk.store.query.Store` implementation: it backs the
example demo server and is what
:func:`~httk.serve.optimade.backend.providers.adapter_from_providers` loads an
:class:`~httk.core.EntryProvider`'s records into.

Set operations evaluate exactly here — ``has_any``/``has_only`` are plain
membership predicates over the row's list value (a NULL list is the empty set),
and ``~`` negates them directly (a convention shared with the generic SQL
searcher). Two exceptions treat a missing list as unknown instead, so neither
the predicate nor its negation matches: a dotted field name (an OPTIMADE nested
property name addressing a dictionary member), and ``length()`` (OPTIMADE
``LENGTH``) on any field, top-level ones included. Scalar comparisons against
NULL are unknown too (see :class:`MemoryExpression`). The SQL
backend needs an aggregate rendering and a second (HAVING) evaluation position
to say the same thing, but that is entirely its own business: the neutral
protocol only ever hands a store one expression per ``searcher.add`` call.

String matching is literal here too (``contains``/``startswith``/``endswith``
are plain :class:`str` operations), which is exactly what the neutral protocol
promises — no pattern language is involved at any point.

``results()`` and the backend-internal ``_matches()`` yield one projected row
per match, one entry per named output: a variable output yields the whole row
dict, a field output the row's value for that field.
"""

from collections.abc import Callable, Iterator, Mapping
from typing import Any, NoReturn

from httk.store.query import (
    MultipleResultsError,
    NoResultError,
    ResultRow,
    ResultRowLike,
    ResultSetLike,
)
from httk.store.query.protocols import SearchResult

Row = dict[str, Any]
Predicate = Callable[[Row], bool | None]


class MemoryExpression:
    """Represent a three-valued predicate over an in-memory row.

    A predicate returns ``True``, ``False`` or ``None`` (unknown, from comparing
    a NULL value); only ``True`` matches. ``&``, ``|`` and ``~`` follow SQL
    (Kleene) logic, as OPTIMADE requires unknown values never to match.

    :param predicate: Function returning whether a row matches, or ``None`` if unknown.
    """

    def __init__(self, predicate: Predicate) -> None:
        self.predicate = predicate

    def __and__(self, other: "MemoryExpression") -> "MemoryExpression":
        def both(row: Row) -> bool | None:
            a, b = self.predicate(row), other.predicate(row)
            return False if a is False or b is False else (None if a is None or b is None else True)

        return MemoryExpression(both)

    def __or__(self, other: "MemoryExpression") -> "MemoryExpression":
        def either(row: Row) -> bool | None:
            a, b = self.predicate(row), other.predicate(row)
            return True if a is True or b is True else (None if a is None or b is None else False)

        return MemoryExpression(either)

    def __invert__(self) -> "MemoryExpression":
        return MemoryExpression(lambda row: None if (p := self.predicate(row)) is None else not p)


class MemoryField:
    """Represent a named field in an in-memory row.

    A dotted ``name`` (``"key.member.sub"``) walks the row: the first segment
    is the row key, each further segment a dictionary key. Once a list of
    dictionaries is crossed the value is completely flattened per OPTIMADE (one
    flat list of the members' values; dictionaries lacking the member add
    nothing); without a crossed list a list-valued member stays as is. A missing
    key or ``None`` before any list gives ``None``.

    :param name: Row key, or dotted member path, addressed by the field.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def _value(self, row: Row) -> Any:
        if "." not in self.name:
            return row.get(self.name)
        head, *path = self.name.split(".")
        value = row.get(head)
        for key in path:
            value = _member(value, key)
        return value

    def _items(self, row: Row) -> Any:
        """The list value for a set predicate: ``()`` for NULL, ``None`` (unknown) for a missing member."""
        value = self._value(row)
        if value is None:
            return None if "." in self.name else ()
        return value

    def _set_predicate(self, test: Callable[[Any], bool]) -> MemoryExpression:
        return MemoryExpression(lambda row: None if (items := self._items(row)) is None else test(items))

    def length(self) -> "MemoryField":
        """Return a field holding the length of this list field's value (``None`` when it is unknown).

        :return: Derived length field, comparable like any numeric field.
        """

        return _MemoryLength(self.name)

    def _compare(self, other: Any, compare: Callable[[Any, Any], bool]) -> MemoryExpression:
        def predicate(row: Row) -> bool | None:
            a, b = self._value(row), other._value(row) if isinstance(other, MemoryField) else other
            if a is None or b is None:
                return None  # comparing an unknown value is unknown
            try:
                return compare(a, b)
            except TypeError:
                return None

        return MemoryExpression(predicate)

    def __eq__(self, other: object) -> MemoryExpression:  # type: ignore[override]
        if other is None:
            return MemoryExpression(lambda row: self._value(row) is None)
        return self._compare(other, lambda a, b: a == b)

    def __ne__(self, other: object) -> MemoryExpression:  # type: ignore[override]
        if other is None:
            return MemoryExpression(lambda row: self._value(row) is not None)
        return self._compare(other, lambda a, b: a != b)

    def __lt__(self, other: Any) -> MemoryExpression:
        return self._compare(other, lambda a, b: a < b)

    def __le__(self, other: Any) -> MemoryExpression:
        return self._compare(other, lambda a, b: a <= b)

    def __gt__(self, other: Any) -> MemoryExpression:
        return self._compare(other, lambda a, b: a > b)

    def __ge__(self, other: Any) -> MemoryExpression:
        return self._compare(other, lambda a, b: a >= b)

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

        return self._set_predicate(lambda items: value in items)

    def has_any(self, *values: Any) -> MemoryExpression:
        """Match list values containing any supplied member.

        :param \\*values: Candidate list members.
        :return: Matching predicate.
        """

        # List membership, not sets: members may be unhashable (a list in a list).
        return self._set_predicate(lambda items: any(value in items for value in values))

    def has_only(self, *values: Any) -> MemoryExpression:
        """Match list values containing no members outside the supplied set.

        :param \\*values: Allowed list members.
        :return: Matching predicate.
        """

        return self._set_predicate(lambda items: all(item in values for item in items))

    def __getattr__(self, name: str) -> NoReturn:
        """Refuse to chain: rows here are flat, so no field refers to a record.

        The :class:`~httk.store.query.SearchField` contract allows attribute
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


def _flatten(value: list[Any] | tuple[Any, ...]) -> Iterator[Any]:
    """Yield the non-list leaves of a (possibly nested) list."""
    for item in value:
        if isinstance(item, list | tuple):
            yield from _flatten(item)
        else:
            yield item


def _member(value: Any, key: str) -> Any:
    """Return member ``key`` of a dictionary, or the completely flattened members of a list of dictionaries.

    Per OPTIMADE, once a list is crossed the result is one flat list: list-valued
    members extend it, and a dictionary lacking the member (or holding ``None``)
    contributes nothing.
    """
    if isinstance(value, Mapping):
        return value.get(key)
    if not isinstance(value, list | tuple):
        return None
    out: list[Any] = []
    for item in _flatten(value):
        member = item.get(key) if isinstance(item, Mapping) else None
        if isinstance(member, list | tuple):
            out.extend(_flatten(member))
        elif member is not None:
            out.append(member)
    return out


class _MemoryLength(MemoryField):
    """The length of a list field's value, ``None`` when the list is unknown."""

    def _value(self, row: Row) -> Any:
        value = super()._value(row)
        return None if value is None else len(value)


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

    def _output(self, variable: "MemoryVariable | MemoryField", name: str) -> None:
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
            raise TypeError(f"_output() takes a search variable or a search field, got {type(variable).__name__}")

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

    def _filtered_rows(self) -> list[Row]:
        rows = [row for row in self._rows if all(e.predicate(row) is True for e in self._expressions)]
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

        return len(self._filtered_rows())

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
        rows = self._filtered_rows()[self.offset :]
        if self._limit is not None and self._limit >= 0:
            rows = rows[: self._limit]
        return MemoryResultSet(rows, selected)

    def _matches(self) -> Iterator[SearchResult]:
        """Yield one ``SearchResult`` per match, in output order.

        Backend-internal: the ``BackendSearcher`` raw path used by ``results()``
        and by code that owns this concrete class directly, in the
        backend-internal ``_matches()`` shape. Ordinary consumers use
        :meth:`results`.
        """
        if not self._outputs:
            raise ValueError("this searcher has no outputs; call _output() before matching")
        rows = self._filtered_rows()[self.offset :]
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

    def searcher(self, *, as_of: object = None) -> MemorySearcher:
        """Create a searcher over this store's tables.

        :param as_of: Optional historic timestamp cutoff; unsupported here.
        :return: Fresh in-memory searcher.
        :raises ValueError: If a historic cutoff is requested.
        """
        if as_of is not None:
            raise ValueError("InMemoryStore does not support historic as_of queries")

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
        :raises httk.store.NoResultError: If the result set is empty.
        :raises httk.store.MultipleResultsError: If it has more than one result.
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
