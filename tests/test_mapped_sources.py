import asyncio
import datetime
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import pytest

from httk.serve.optimade import (
    InMemoryStore,
    MappedSource,
    adapter_from_sources,
    create_asgi_app,
    execute_query,
    parse_optimade_filter,
)

pytest.importorskip("httk.atomistic")

KEYS = {
    "id": "mat_id",
    "chemical_formula_descriptive": "formula",
    "nsites": "nsites",
    "elements": "elements_list",
    "last_modified": "updated",
}
ROWS: list[dict[str, Any]] = [
    {
        "mat_id": i,
        "formula": f"F{i}",
        "nsites": Decimal(i),
        "elements_list": ["Na", "Cl"] if i % 2 else ["Ti"],
        "updated": datetime.datetime(2026, 1, i),  # noqa: DTZ001 - naive on purpose
        "energy": Decimal("1.5"),
    }
    for i in range(1, 6)
]
COMPUTED = {"nelements": lambda row: len(row["elements_list"])}


def make(**options: Any) -> Any:
    source = MappedSource("materials", KEYS, fields=COMPUTED)
    return adapter_from_sources(InMemoryStore({"materials": ROWS}), {"structures": source}, **options)


def query(adapter: Any, filt: str | None = None, fields: list[str] | None = None, **kw: Any) -> list[dict[str, Any]]:
    ast = parse_optimade_filter(filt) if filt else None
    results = execute_query(adapter, ["structures"], fields or ["id", "type"], [], kw.pop("limit", 10), 0, ast, **kw)
    return [dict(row.values) for row in results]


def get(app: Any, path: str) -> httpx2.Response:
    async def request() -> httpx2.Response:
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(request())


def test_filter_sort_page() -> None:
    adapter = make()
    # The in-memory store compares exactly, so an integer column needs a matching filter value type.
    assert query(adapter, 'id="3"') == []
    string_ids = [{"mat_id": f"m{i}"} for i in range(3)]
    other = adapter_from_sources(InMemoryStore({"m": string_ids}), {"structures": MappedSource("m", {"id": "mat_id"})})
    assert [r["id"] for r in query(other, 'id="m1"')] == ["m1"]
    assert [r["id"] for r in query(adapter, 'elements HAS "Na"')] == ["1", "3", "5"]
    out = query(adapter, sort=[("nsites", True)], limit=2)
    assert [r["id"] for r in out] == ["5", "4"]


def test_http() -> None:
    app = create_asgi_app(make(), baseurl="http://testserver/")
    listing = get(app, "/v1/structures?page_limit=2").json()
    assert [d["id"] for d in listing["data"]] == ["1", "2"]
    # The in-memory store compares exactly, so by-id lookup needs a string column.
    text = adapter_from_sources(
        InMemoryStore({"m": [{"mat_id": "m4"}]}), {"structures": MappedSource("m", {"id": "mat_id"})}
    )
    assert get(create_asgi_app(text, baseurl="http://testserver/"), "/v1/structures/m4").json()["data"]["id"] == "m4"
    props = get(app, "/v1/info/structures").json()["data"]["properties"]
    assert props["nelements"] and props["nsites"]["sortable"] is True
    assert props["elements"]["sortable"] is False
    assert get(app, "/v1/structures?filter=nelements=2").status_code == 501


def test_presentation() -> None:
    out = query(make(), "nsites=1", ["id", "last_modified", "nsites", "nelements"])
    assert out == [{"id": "1", "last_modified": "2026-01-01T00:00:00Z", "nsites": 1, "nelements": 2}]
    from httk.serve.optimade.backend.sources import _present

    assert _present(Decimal("1.5"), "x") == 1.5
    assert _present(datetime.datetime(2026, 1, 1, 0, 0, 0, 5, tzinfo=datetime.UTC), "x").endswith(".000005Z")


@pytest.mark.parametrize("value", ["NaN", "Infinity"])
def test_non_finite_decimal(value: str) -> None:
    from httk.serve.optimade.backend.sources import _present

    with pytest.raises(TypeError, match="non-finite"):
        _present(Decimal(value), "nsites")


def test_unpresentable_value() -> None:
    rows = [{"mat_id": 1, "formula": b"x"}]
    source = MappedSource("m", {"id": "mat_id", "chemical_formula_descriptive": "formula"})
    adapter = adapter_from_sources(InMemoryStore({"m": rows}), {"structures": source})
    with pytest.raises(TypeError, match="chemical_formula_descriptive.*bytes"):
        query(adapter, fields=["id", "chemical_formula_descriptive"])


@pytest.mark.parametrize(
    ("source", "match"),
    [
        (MappedSource("materials", {"nsites": "nsites"}), "'id'"),
        (MappedSource("materials", {"id": "mat_id", "type": "formula"}), "'type'"),
        (MappedSource("materials", {"id": "mat_id"}, fields={"type": str}), "'type'"),
    ],
)
def test_invalid_source(source: MappedSource, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        adapter_from_sources(InMemoryStore({"materials": ROWS}), {"structures": source})


def test_invalid_groups() -> None:
    store = InMemoryStore({"materials": ROWS})
    a, b = MappedSource("materials", {"id": "mat_id"}), MappedSource("materials", {"id": "formula"})
    with pytest.raises(ValueError, match="identical keys"):
        adapter_from_sources(store, {"structures": [a, b]})
    with pytest.raises(ValueError, match="structures"):
        adapter_from_sources(store, {"structures": []})


def test_missing_backend_field() -> None:
    class Variable:
        def __getattr__(self, name: str) -> Any:
            if name != "mat_id":
                raise AttributeError(name)
            return name

    class Searcher:
        def variable(self, target: Any) -> Variable:
            return Variable()

    class FakeStore:
        def searcher(self) -> Searcher:
            return Searcher()

    source = MappedSource("t", {"id": "mat_id", "nsites": "nope"})
    with pytest.raises(ValueError, match="'nsites' maps to 'nope'"):
        adapter_from_sources(FakeStore(), {"structures": source})  # type: ignore[arg-type]


def test_caller_options_win() -> None:
    adapter = make(sortable={"structures": ["id"]}, default_response_overrides={"structures": ["nsites"]})
    info = adapter.schema.entry_info["structures"]["properties"]
    assert info["id"]["sortable"] is True
    assert not info["nsites"]["sortable"]


def test_two_sources() -> None:
    store = InMemoryStore({"a": ROWS[:2], "b": ROWS[2:]})
    keys = {"id": "mat_id"}
    adapter = adapter_from_sources(store, {"structures": [MappedSource("a", keys), MappedSource("b", keys)]})
    assert sorted(r["id"] for r in query(adapter)) == ["1", "2", "3", "4", "5"]


def test_table_store(tmp_path: Path) -> None:
    pytest.importorskip("sqlalchemy")
    from httk.store import ListTable, TableSource, TableStore

    path = tmp_path / "m.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE materials (mat_id INTEGER PRIMARY KEY, nsites INTEGER);
            CREATE TABLE elems (mat_id INTEGER, element TEXT);
            INSERT INTO materials VALUES (1, 2), (2, 2), (3, 8);
            INSERT INTO elems VALUES (1, 'Na'), (1, 'Cl'), (2, 'Si'), (3, 'Na'), (3, 'Si');
            """
        )
    tables = {"materials": TableSource("materials", lists={"elements": ListTable("elems", "mat_id", "element")})}
    store = TableStore(f"sqlite:///{path}", tables)
    keys = {"id": "mat_id", "nsites": "nsites", "elements": "elements"}
    try:
        adapter = adapter_from_sources(
            store,
            {"structures": MappedSource("materials", keys, fields={"nelements": lambda row: len(row["elements"])})},
        )
        app = create_asgi_app(adapter, baseurl="http://testserver/")
        assert get(app, "/v1/structures/3").json()["data"]["id"] == "3"
        found = get(app, '/v1/structures?filter=elements HAS "Na"').json()["data"]
        assert [d["id"] for d in found] == ["1", "3"]
        ordered = get(app, "/v1/structures?sort=-nsites").json()["data"]
        assert ordered[0]["id"] == "3"
        assert get(app, "/v1/structures?filter=nelements=2").status_code == 501
        with pytest.raises(ValueError, match="nope"):
            adapter_from_sources(store, {"structures": MappedSource("materials", {"id": "mat_id", "nsites": "nope"})})
    finally:
        store.close()
