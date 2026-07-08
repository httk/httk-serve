from dataclasses import dataclass
from typing import Any

from httk.optimade.backend import BackendAdapter, EntrySource, execute_query

from fake_backend import FakeStore


@dataclass
class Row:
    sid: str
    nelements: int = 1
    extra: str = "x"


STRUCTURE_FIELDS: dict[str, Any] = {
    "type": lambda x: "structures",
    "id": lambda x: x.sid,
    "nelements": lambda x: x.nelements,
}

CALC_FIELDS: dict[str, Any] = {
    "type": lambda x: "calculations",
    "id": lambda x: x.sid,
}


def make_adapter(structures: list[Row], aimd: list[Row] = [], elastic: list[Row] = []) -> BackendAdapter:
    store = FakeStore(
        rows_by_target={
            "structure-table": structures,
            "aimd-table": aimd,
            "elastic-table": elastic,
        }
    )
    return BackendAdapter(
        store=store,
        sources={
            "structures": (EntrySource(target="structure-table", fields=STRUCTURE_FIELDS),),
            "calculations": (
                EntrySource(target="aimd-table", fields=CALC_FIELDS),
                EntrySource(target="elastic-table", fields=CALC_FIELDS),
            ),
        },
    )


def rows(n: int, prefix: str = "s") -> list[Row]:
    return [Row(sid=f"{prefix}{i}") for i in range(n)]


def test_basic_query_maps_fields() -> None:
    adapter = make_adapter([Row(sid="a", nelements=2)])
    results = execute_query(adapter, ["structures"], ["id", "type", "nelements"], [], 10, 0)
    out = list(results)
    assert out == [{"id": "a", "type": "structures", "nelements": 2}]
    assert results.more_data_available is False


def test_unknown_response_fields_are_null() -> None:
    adapter = make_adapter([Row(sid="a")])
    results = execute_query(adapter, ["structures"], ["id", "type"], ["species"], 10, 0)
    out = list(results)
    assert out[0]["species"] is None


def test_prefixed_field_fallback_reads_attribute() -> None:
    adapter = make_adapter([Row(sid="a", extra="hello")])
    results = execute_query(adapter, ["structures"], ["id", "type", "_httk_extra"], [], 10, 0)
    out = list(results)
    # Parity with httk v1: the prefix is stripped in the output key.
    assert out[0]["extra"] == "hello"


def test_limit_truncates_and_reports_more_data() -> None:
    adapter = make_adapter(rows(5))
    results = execute_query(adapter, ["structures"], ["id", "type"], [], 3, 0)
    out = list(results)
    assert [d["id"] for d in out] == ["s0", "s1", "s2"]
    assert results.more_data_available is True


def test_offset_within_first_searcher() -> None:
    adapter = make_adapter(rows(5))
    results = execute_query(adapter, ["structures"], ["id", "type"], [], 10, 2)
    out = list(results)
    assert [d["id"] for d in out] == ["s2", "s3", "s4"]
    assert results.more_data_available is False


def test_offset_spanning_searcher_boundary() -> None:
    adapter = make_adapter([], aimd=rows(3, "a"), elastic=rows(3, "e"))
    results = execute_query(adapter, ["calculations"], ["id", "type"], [], 10, 4)
    out = list(results)
    assert [d["id"] for d in out] == ["e1", "e2"]


def test_offset_sets_dummy_limit_for_sqlite() -> None:
    adapter = make_adapter(rows(5))
    execute_query(adapter, ["structures"], ["id", "type"], [], None, 2)
    searcher = adapter.store.searchers[0]  # type: ignore[attr-defined]
    assert searcher.limit == -1
    assert searcher.offset == 2


def test_limit_spanning_searcher_boundary() -> None:
    adapter = make_adapter([], aimd=rows(3, "a"), elastic=rows(3, "e"))
    results = execute_query(adapter, ["calculations"], ["id", "type"], [], 4, 0)
    out = list(results)
    assert [d["id"] for d in out] == ["a0", "a1", "a2", "e0"]
    assert results.more_data_available is True


def test_offset_beyond_total_returns_nothing() -> None:
    # httk v1 returned results from offset 0 in this case; the port fixes that.
    adapter = make_adapter(rows(3))
    results = execute_query(adapter, ["structures"], ["id", "type"], [], 10, 7)
    assert list(results) == []
    assert results.count() == 0


def test_count_subtracts_offsets() -> None:
    adapter = make_adapter(rows(5))
    results = execute_query(adapter, ["structures"], ["id", "type"], [], None, 2)
    assert results.count() == 3


def test_zero_limit_counts_without_rows() -> None:
    adapter = make_adapter(rows(4))
    results = execute_query(adapter, ["structures"], [], [], 0, 0)
    assert results.count() == 4
    assert list(results) == []
