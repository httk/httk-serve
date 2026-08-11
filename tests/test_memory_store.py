"""Tests for the reference in-memory store (httk.serve.optimade.backend.memory_store).

The SQL backend in httk-store is checked against this store for parity; these
tests pin the parts of the neutral contract that only show up here — literal
string matching, the reserved constant-expression methods, and the named
multi-output ``SearchResult`` shape.
"""

import pytest
from httk.store.query import MultipleResultsError, NoResultError, SearchResult

from httk.serve.optimade.backend.memory_store import (
    InMemoryStore,
    MemoryExpression,
    MemoryField,
    MemoryVariable,
)

LABELS = [
    {"text": "50% Mg", "note": None},
    {"text": "5012 Mg", "note": None},
    {"text": "a_b", "note": None},
    {"text": "axb", "note": None},
    {"text": "Mg 50%", "note": None},
    {"text": "Mg 5012", "note": None},
    {"text": "Mg a_b", "note": None},
    {"text": "Mg axb", "note": None},
]

ALL_LABELS = {row["text"] for row in LABELS}


def store() -> InMemoryStore:
    return InMemoryStore({"labels": [dict(row) for row in LABELS]})


def searcher_over_labels():
    memory_store = store()
    searcher = memory_store.searcher()
    variable = searcher.variable("labels")
    searcher.output(variable, "label")
    return searcher, variable


def test_historic_query_is_rejected() -> None:
    with pytest.raises(ValueError, match="InMemoryStore.*historic"):
        store().searcher(as_of=42)


def texts(searcher) -> set[str]:
    return {item[0][0]["text"] for item in searcher}


# ------------------------------------------------------------- literal string matching


def test_string_matching_is_literal():
    # `%` and `_` are ordinary characters here, exactly as the protocol says.
    for build, expected in [
        (lambda v: v.text.contains("50%"), {"50% Mg", "Mg 50%"}),
        (lambda v: v.text.contains("a_b"), {"a_b", "Mg a_b"}),
        (lambda v: v.text.startswith("50%"), {"50% Mg"}),
        (lambda v: v.text.startswith("a_b"), {"a_b"}),
        (lambda v: v.text.endswith("50%"), {"Mg 50%"}),
        (lambda v: v.text.endswith("a_b"), {"a_b", "Mg a_b"}),
    ]:
        searcher, variable = searcher_over_labels()
        searcher.add(build(variable))
        assert texts(searcher) == expected


def test_string_matching_guards_non_string_values():
    searcher, variable = searcher_over_labels()
    searcher.add(variable.note.contains("x"))  # every note is None
    assert texts(searcher) == set()


def test_like_is_gone_from_the_field_surface():
    assert not hasattr(MemoryField("text"), "like")


def test_scalar_membership_includes_none() -> None:
    searcher, variable = searcher_over_labels()
    searcher.add(variable.note.is_in(None))
    assert texts(searcher) == ALL_LABELS

    searcher, variable = searcher_over_labels()
    searcher.add(variable.text.is_in("a_b", "axb"))
    assert texts(searcher) == {"a_b", "axb"}


# ------------------------------------------------------------- constant expressions


def test_always_true_and_always_false_are_real_methods():
    # Runtime conformance: MemoryVariable's catch-all __getattr__ would happily
    # serve `always_true` as a *field*, which type checkers cannot see. Assert
    # the real methods win and return expressions.
    variable = MemoryVariable("labels")
    assert isinstance(variable.always_true(), MemoryExpression)
    assert isinstance(variable.always_false(), MemoryExpression)
    assert not isinstance(variable.always_true(), MemoryField)
    assert variable.always_true().predicate({}) is True
    assert variable.always_false().predicate({}) is False


def test_constant_expressions_over_rows():
    searcher, variable = searcher_over_labels()
    searcher.add(variable.always_true())
    assert texts(searcher) == ALL_LABELS
    assert searcher.count() == len(ALL_LABELS)

    searcher, variable = searcher_over_labels()
    searcher.add(variable.always_false())
    assert texts(searcher) == set()
    assert searcher.count() == 0


# ------------------------------------------------------------- SearchResult outputs


def test_single_output_yields_named_search_results():
    searcher, _variable = searcher_over_labels()
    results = list(searcher)
    assert isinstance(results[0], SearchResult)
    assert results[0].names == ("label",)
    assert len(results[0].values) == 1
    values, names = results[0]  # unpacks as a 2-tuple
    assert names == ("label",)
    assert values[0]["text"] == "50% Mg"


def test_multiple_outputs_are_recorded_in_declaration_order():
    # The object-plus-field shape of the SQL store's reference-join test.
    memory_store = store()
    searcher = memory_store.searcher()
    variable = searcher.variable("labels")
    searcher.output(variable, "label")
    searcher.output(variable.text, "text")
    searcher.add(variable.text == "a_b")
    (result,) = list(searcher)
    assert result.names == ("label", "text")
    assert len(result.values) == 2
    assert result.values[0]["text"] == "a_b"
    assert result.values[1] == "a_b"
    assert result[0][0] is result.values[0]


def test_iteration_without_outputs_raises():
    memory_store = store()
    searcher = memory_store.searcher()
    searcher.variable("labels")
    try:
        iter(searcher)
    except ValueError as error:
        assert "output" in str(error)
    else:  # pragma: no cover - the assertion above is the test
        raise AssertionError("iterating without outputs must raise")


def test_result_set_one_uses_neutral_errors() -> None:
    memory_store = store()
    searcher = memory_store.searcher()
    variable = searcher.variable("labels")

    with pytest.raises(MultipleResultsError):
        searcher.results(label=variable).one()

    searcher.add(variable.text == "not present")
    with pytest.raises(NoResultError):
        searcher.results(label=variable).one()


@pytest.mark.parametrize(
    ("descending", "expected"),
    [(False, [1, 2, 3, None, None]), (True, [3, 2, 1, None, None])],
)
def test_sort_keeps_nulls_last_and_paging_stable(descending: bool, expected: list[int | None]) -> None:
    memory_store = InMemoryStore(
        {
            "values": [
                {"id": "first-null", "value": None},
                {"id": "one", "value": 1},
                {"id": "second-null", "value": None},
                {"id": "three", "value": 3},
                {"id": "two", "value": 2},
            ]
        }
    )
    searcher = memory_store.searcher()
    variable = searcher.variable("values")
    searcher.output(variable.value, "value")
    searcher.add_sort(variable.value, descending)
    assert [result.values[0] for result in searcher] == expected

    page = memory_store.searcher()
    variable = page.variable("values")
    page.output(variable.value, "value")
    page.add_sort(variable.value, descending)
    page.add_offset(3)
    page.set_limit(2)
    assert [result.values[0] for result in page] == [None, None]

    stable = memory_store.searcher()
    variable = stable.variable("values")
    stable.output(variable, "row")
    stable.add_sort(variable.value, descending)
    assert [result.values[0]["id"] for result in stable][-2:] == ["first-null", "second-null"]
