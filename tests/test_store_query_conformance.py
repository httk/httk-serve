"""SQL versus in-memory query conformance used by the OPTIMADE adapter."""

from dataclasses import dataclass
from fractions import Fraction
from typing import Annotated

import pytest
from httk.core import FracVector
from httk.core.storage import Shape
from httk.store.backend.sql import Backend, SqlStore
from store_test_support import clickhouse_database, postgres_database

from httk.serve.optimade.backend.memory_store import InMemoryStore


@dataclass(frozen=True)
class Reference:
    doi: str
    title: str


@dataclass(frozen=True)
class Rec:
    formula: str
    spacegroup: int
    energy: Fraction
    symbols: list[str]
    ref: Reference | None = None


@dataclass(frozen=True)
class Label:
    text: str
    note: str | None = None


@dataclass(frozen=True)
class Cell:
    name: str
    basis: Annotated[FracVector, Shape(3, 3)]


REF_A = Reference("10.1/a", "Alpha")
REF_B = Reference("10.1/b", "Beta")
RECORDS = [
    Rec("CaTiO3", 221, Fraction(-1, 3), ["O", "Ca", "Ti"], REF_A),
    Rec("NaCl", 225, Fraction(1, 2), ["Na", "Cl"], REF_A),
    Rec("MgO", 225, Fraction(-5, 4), ["Mg", "O"], REF_B),
    Rec("CaO", 225, Fraction(0), ["Ca", "O"], None),
    Rec("SrCaTiO", 62, Fraction(3, 2), ["O", "Ca", "Ti", "Sr"], REF_B),
    Rec("X", 1, Fraction(7, 8), [], None),
]
LABELS = [
    Label("50% Mg"),
    Label("5012 Mg"),
    Label("a_b"),
    Label("axb"),
    Label("Mg 50%"),
    Label("Mg 5012"),
    Label("Mg a_b"),
    Label("Mg axb"),
]
ALL_LABELS = {label.text for label in LABELS}
LITERAL_MATCH_CASES = [
    (lambda v: v.text.contains("50%"), {"50% Mg", "Mg 50%"}),
    (lambda v: v.text.contains("a_b"), {"a_b", "Mg a_b"}),
    (lambda v: v.text.startswith("50%"), {"50% Mg"}),
    (lambda v: v.text.startswith("a_b"), {"a_b"}),
    (lambda v: v.text.endswith("50%"), {"Mg 50%"}),
    (lambda v: v.text.endswith("a_b"), {"a_b", "Mg a_b"}),
]


@pytest.fixture(scope="module", params=("sqlite", "duckdb", "clickhouse", "postgresql"))
def query_store(request):
    kind = request.param
    if kind == "sqlite":
        manager = Backend.sqlite()
    elif kind == "duckdb":
        pytest.importorskip("duckdb_engine")
        manager = Backend.duckdb()
    elif kind == "clickhouse":
        manager = clickhouse_database()
    else:
        manager = postgres_database()
    with manager as database:
        store = SqlStore(database, entry_records={})
        if kind == "clickhouse":
            with store.bulk_ingest(finalize="deferred") as bulk:
                for record in (*RECORDS, *LABELS):
                    bulk.save(record)
        else:
            with store.transaction():
                for record in (*RECORDS, *LABELS):
                    store.save(record)
        yield store


def _program_has_any(searcher, v):
    searcher.add(v.symbols.has_any("O", "Na"))


def _program_has_only(searcher, v):
    searcher.add(v.symbols.has_only("O", "Ca", "Ti"))


def _program_not_has_any(searcher, v):
    searcher.add(~v.symbols.has_any("Ca", "Ti"))


def _program_not_has_only(searcher, v):
    searcher.add(~v.symbols.has_only("O", "Ca", "Ti"))


def _program_not_has_all(searcher, v):
    searcher.add(~(v.symbols.has_any("Ca") & v.symbols.has_any("Ti")))


def _program_not_inside_and(searcher, v):
    searcher.add((v.spacegroup == 225) & ~v.symbols.has_any("Ca"))


def _program_not_over_mixed_and(searcher, v):
    searcher.add(~((v.spacegroup == 225) & v.symbols.has_any("Ca")))


def _program_not_over_mixed_or(searcher, v):
    searcher.add(~((v.spacegroup == 225) | v.symbols.has_any("Ti")))


def _program_double_not(searcher, v):
    searcher.add(~~v.symbols.has_any("Ca", "Ti"))


def _program_string_ops(searcher, v):
    searcher.add(v.formula.startswith("Ca") | v.formula.endswith("O"))
    searcher.add(v.formula.contains("a"))


def _program_numeric_range(searcher, v):
    searcher.add((v.energy > 0.0) & (v.energy <= 1.0))


def _program_mixed_scalar_and_set(searcher, v):
    searcher.add((v.spacegroup == 225) & v.symbols.has_only("Na", "Cl"))


PARITY_PROGRAMS = [
    _program_has_any,
    _program_has_only,
    _program_not_has_any,
    _program_string_ops,
    _program_numeric_range,
    _program_mixed_scalar_and_set,
    _program_not_has_only,
    _program_not_has_all,
    _program_not_inside_and,
    _program_not_over_mixed_and,
    _program_not_over_mixed_or,
    _program_double_not,
]


def _label_searcher(store):
    searcher = store.searcher()
    return searcher, searcher.variable(Label)


def _texts(searcher, variable=None):
    rows = searcher.results(label=variable) if variable is not None else searcher.results()
    return {row[0].text for row in rows}


def test_parity_with_in_memory_store(query_store):
    memory_rows = [
        {
            "formula": rec.formula,
            "spacegroup": rec.spacegroup,
            "energy": float(rec.energy),
            "symbols": list(rec.symbols),
        }
        for rec in RECORDS
    ]
    memory_store = InMemoryStore({"recs": memory_rows})
    for program in PARITY_PROGRAMS:
        memory_searcher = memory_store.searcher()
        memory_variable = memory_searcher.variable("recs")
        program(memory_searcher, memory_variable)
        memory_ids = {row.rec["formula"] for row in memory_searcher.results(rec=memory_variable)}
        sql_searcher = query_store.searcher()
        sql_variable = sql_searcher.variable(Rec)
        program(sql_searcher, sql_variable)
        sql_ids = {row.rec.formula for row in sql_searcher.results(rec=sql_variable)}
        assert sql_ids == memory_ids, program.__name__
        assert sql_searcher.count() == memory_searcher.count(), program.__name__


def test_literal_string_matching_parity_with_in_memory_store(query_store):
    memory_store = InMemoryStore({"labels": [{"text": label.text, "note": label.note} for label in LABELS]})
    for build, expected in LITERAL_MATCH_CASES:
        memory_searcher = memory_store.searcher()
        memory_variable = memory_searcher.variable("labels")
        memory_searcher.add(build(memory_variable))
        memory_texts = {row.label["text"] for row in memory_searcher.results(label=memory_variable)}
        sql_searcher, sql_variable = _label_searcher(query_store)
        sql_searcher.add(build(sql_variable))
        assert _texts(sql_searcher, sql_variable) == memory_texts == expected
        assert sql_searcher.count() == memory_searcher.count()


def test_constant_expression_parity_with_in_memory_store(query_store):
    memory_store = InMemoryStore({"labels": [{"text": label.text, "note": label.note} for label in LABELS]})
    for build, expected in [(lambda v: v.always_true(), ALL_LABELS), (lambda v: v.always_false(), set())]:
        memory_searcher = memory_store.searcher()
        memory_variable = memory_searcher.variable("labels")
        memory_searcher.add(build(memory_variable))
        memory_texts = {row.label["text"] for row in memory_searcher.results(label=memory_variable)}
        sql_searcher, sql_variable = _label_searcher(query_store)
        sql_searcher.add(build(sql_variable))
        assert _texts(sql_searcher, sql_variable) == memory_texts == expected


def test_search_result_names_parity_with_in_memory_store(query_store):
    memory_store = InMemoryStore({"labels": [{"text": label.text, "note": label.note} for label in LABELS]})
    memory_searcher = memory_store.searcher()
    memory_variable = memory_searcher.variable("labels")
    memory_searcher._output(memory_variable, "label")
    memory_searcher._output(memory_variable.text, "text")
    sql_searcher = query_store.searcher()
    sql_variable = sql_searcher.variable(Label)
    sql_searcher._output(sql_variable, "label")
    sql_searcher._output(sql_variable.text, "text")
    memory_results = list(memory_searcher._matches())
    sql_results = list(sql_searcher._matches())
    assert {result.names for result in memory_results} == {("label", "text")}
    assert {result.names for result in sql_results} == {("label", "text")}
    assert {result[0][1] for result in memory_results} == {result[0][1] for result in sql_results} == ALL_LABELS
