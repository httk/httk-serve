"""ASGI coverage for the lazy stored-entry federation adapter."""

import asyncio
import warnings
from collections.abc import Mapping
from contextlib import ExitStack
from fractions import Fraction
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from httk.atomistic import (
    ASUStructure,
    ASUStructureRecord,
    Cell,
    Sites,
    Species,
    StructureEntry,
    UnitcellStructure,
    UnitcellStructureRecord,
    WyckoffSite,
)
from httk.store import Backend, EntryIdScheme, SqlStore
from httk.store.backend.sql import DuplicateEntryIdError, StoredEntrySource
from starlette.testclient import TestClient

from httk.serve.optimade import adapter_from_stores, create_asgi_app


def _structure(*symbols: str, basis_precision: Fraction | None = None) -> UnitcellStructure:
    species = tuple(Species(symbol, (symbol,), (1,)) for symbol in symbols)
    count = len(symbols)
    coordinates = [[Fraction(index, count), 0, 0] for index in range(count)] if count else []
    return UnitcellStructure(
        Cell([[4, 0, 0], [0, 4, 0], [0, 0, 4]], precision=basis_precision),
        Sites(coordinates),
        species,
        symbols,
    )


def _asu() -> ASUStructure:
    sodium = Species("Na", ("Na",), (1,))
    return ASUStructure(
        [[4, 0, 0], [0, 4, 0], [0, 0, 4]],
        225,
        (WyckoffSite("a", (), "Na"),),
        (sodium,),
    )


def _client(adapter) -> TestClient:
    return TestClient(
        create_asgi_app(adapter, baseurl="http://testserver"),
        base_url="http://testserver",
    )


class AsgiSyncClient:
    """Minimal synchronous ASGI client with no network escape hatch."""

    def __init__(self, app: Any, *, base_url: str) -> None:
        self.app = app
        self.base_url = base_url

    def get(self, url: str) -> httpx.Response:
        """GET one local ASGI URL."""
        assert urlsplit(url).netloc == urlsplit(self.base_url).netloc

        async def request() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport) as client:
                return await client.get(url)

        return asyncio.run(request())


def _store(database: Backend, **options: object) -> SqlStore:
    """Build the revision-capable structure store used by this module."""
    return SqlStore(
        database,
        entry_records={StructureEntry: UnitcellStructureRecord},
        entry_ids=EntryIdScheme("httk.test", "1"),
        **options,
    )


def _save_entry(store: SqlStore, entry: UnitcellStructure | ASUStructure) -> str:
    """Save one structure and return its store-minted lineage id."""
    store.save(entry)
    record = store.fetch_entry(StructureEntry, entry.id)
    assert record is not None
    assert isinstance(record.id, str)
    return record.id


def _child_table_names() -> tuple[str, ...]:
    """Resolve every child-table name of the fixture record without hardcoding strings."""
    from httk.store.backend.schema import resolve_schema

    resolved = resolve_schema(UnitcellStructureRecord)
    names = tuple(
        field.child.table_name for field in resolved.fields if field.role == "child" and field.child is not None
    )
    assert names, "fixture record unexpectedly has no child table"
    return names


def test_response_fields_forwarded_as_fields_to_federation(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _structure("Na", "Cl")
    with Backend.sqlite() as database:
        store = _store(database)
        _save_entry(store, source)
        adapter = adapter_from_stores((StoredEntrySource(store, StructureEntry, "main"),))
        federation = adapter.federations["structures"]
        original_query = federation.query
        captured: list[object] = []

        def tracked_query(*args: object, **kwargs: object):
            captured.append(kwargs.get("fields"))
            return original_query(*args, **kwargs)

        monkeypatch.setattr(federation, "query", tracked_query)
        with _client(adapter) as client:
            response = client.get("/structures", params={"response_fields": "id"})
            assert response.status_code == 200

        assert captured
        fields = captured[0]
        assert fields is not None
        assert "id" in fields
        # nelements is served by the fixture but was not requested, so it must be pruned.
        assert "nelements" not in fields


def _collect_sql(database, client, params: dict[str, str]) -> list[str]:  # type: ignore[no-untyped-def]
    import sqlalchemy

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    sqlalchemy.event.listen(database.engine, "before_cursor_execute", record)
    try:
        response = client.get("/structures", params=params)
        assert response.status_code == 200
    finally:
        sqlalchemy.event.remove(database.engine, "before_cursor_execute", record)
    assert statements
    return statements


def test_response_fields_id_only_skips_child_table_hydration() -> None:
    child_tables = _child_table_names()
    source = _structure("Na", "Cl")
    with Backend.sqlite() as database:
        store = _store(database)
        _save_entry(store, source)
        adapter = adapter_from_stores((StoredEntrySource(store, StructureEntry, "main"),))

        with _client(adapter) as client:
            # Positive control: a full render must hydrate at least one child table,
            # so the pruning assertion below fails loudly if the fixture ever stops.
            full = _collect_sql(database, client, {})
            assert any(table in statement for table in child_tables for statement in full)

            # id-only: pruning must skip every child table.
            pruned = _collect_sql(database, client, {"response_fields": "id"})
            assert not any(table in statement for table in child_tables for statement in pruned)


def test_single_store_preserves_public_prefix_and_prefixed_property_name() -> None:
    source = _structure("Na", basis_precision=Fraction(1, 1000))
    with Backend.sqlite() as database:
        store = _store(database)
        entry_id = _save_entry(store, source)
        adapter = adapter_from_stores((StoredEntrySource(store, StructureEntry, "alpha", "alpha-"),))

        assert {"id", "type"} <= set(adapter.schema.sortable_response_fields["structures"])
        public_id = "alpha-" + entry_id
        with _client(adapter) as client:
            response = client.get(
                "/structures",
                params={"response_fields": "id,type,nelements,_httk_basis_precision"},
            )
            assert response.status_code == 200
            resource = response.json()["data"][0]
            assert resource["id"] == public_id
            assert resource["attributes"]["nelements"] == 1
            assert resource["attributes"]["_httk_basis_precision"] == pytest.approx(0.001)
            assert not ({"source", "origin", "backing"} & set(resource["attributes"]))

            filtered = client.get("/structures", params={"filter": f'id = "{public_id}"'})
            assert filtered.status_code == 200
            assert [item["id"] for item in filtered.json()["data"]] == [public_id]

            direct = client.get(f"/structures/{public_id}")
            assert direct.status_code == 200
            assert direct.json()["data"]["id"] == public_id


def test_one_store_serves_multiple_concrete_backings() -> None:
    unitcell = _structure("Na", "Cl")
    asu = _asu()
    with Backend.sqlite() as database:
        store = SqlStore(
            database,
            entry_records={StructureEntry: (UnitcellStructureRecord, ASUStructureRecord)},
            entry_ids=EntryIdScheme("httk.test", "1"),
        )
        unitcell_id = _save_entry(store, unitcell)
        asu_id = _save_entry(store, asu)
        adapter = adapter_from_stores((StoredEntrySource(store, StructureEntry, "mixed"),))

        with _client(adapter) as client:
            response = client.get(
                "/structures",
                params={"sort": "id", "response_fields": "id,type,site_coordinate_span"},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["meta"]["data_available"] == 2
            assert [item["id"] for item in payload["data"]] == sorted((unitcell_id, asu_id))
            assert {item["attributes"]["site_coordinate_span"] for item in payload["data"]} == {
                "unit_cell",
                "asymmetric_unit",
            }


def test_multiple_sources_push_filter_sort_and_pagination() -> None:
    with ExitStack() as stack:
        first_database = stack.enter_context(Backend.sqlite())
        second_database = stack.enter_context(Backend.sqlite())
        first = _store(first_database)
        second = _store(second_database)
        first_sources = (_structure("Na"), _structure("Na", "Cl"))
        second_sources = (_structure("Si"), _structure("Si", "O"))
        first_ids = tuple(_save_entry(first, structure) for structure in first_sources)
        second_ids = tuple(_save_entry(second, structure) for structure in second_sources)

        adapter = adapter_from_stores(
            (
                StoredEntrySource(first, StructureEntry, "first", "a-"),
                StoredEntrySource(second, StructureEntry, "second", "b-"),
            )
        )
        with _client(adapter) as client:
            response = client.get(
                "/structures",
                params={
                    "filter": "nelements = 1",
                    "sort": "id",
                    "page_limit": "1",
                    "response_fields": "id,type,nelements",
                },
            )
            assert response.status_code == 200
            first_page = response.json()
            # Two of the four stored structures match nelements=1: data_returned is
            # that filtered total across all pages, data_available the unfiltered
            # endpoint total, and the first page holds only page_limit=1 resource.
            assert first_page["meta"]["data_available"] == 4
            assert first_page["meta"]["data_returned"] == 2
            assert len(first_page["data"]) == 1
            assert first_page["meta"]["more_data_available"] is True
            first_id = first_page["data"][0]["id"]

            next_response = client.get(first_page["links"]["next"])
            assert next_response.status_code == 200
            second_page = next_response.json()
            second_id = second_page["data"][0]["id"]
            assert [first_id, second_id] == sorted(("a-" + first_ids[0], "b-" + second_ids[0]))
            assert second_page["meta"]["more_data_available"] is False

            descending = client.get(
                "/structures",
                params={"sort": "-id", "response_fields": "id,type"},
            )
            assert descending.status_code == 200
            ids = [item["id"] for item in descending.json()["data"]]
            assert ids == sorted(ids, reverse=True)

            unsupported_sort = client.get("/structures", params={"sort": "nelements"})
            assert unsupported_sort.status_code == 400

            invalid_filter = client.get(
                "/structures",
                params={"filter": 'chemical_formula_reduced = "NaCl"'},
            )
            assert invalid_filter.status_code == 400


def test_duplicate_public_ids_map_to_safe_http_500_and_remain_auditable() -> None:
    duplicated = _structure("Na")
    with ExitStack() as stack:
        first_database = stack.enter_context(Backend.sqlite())
        second_database = stack.enter_context(Backend.sqlite())
        first = _store(first_database)
        second = _store(second_database)
        duplicate_id = _save_entry(first, duplicated)
        assert _save_entry(second, duplicated) == duplicate_id
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            adapter = adapter_from_stores(
                (
                    StoredEntrySource(first, StructureEntry, "first"),
                    StoredEntrySource(second, StructureEntry, "second"),
                )
            )
        assert caught

        with _client(adapter) as client:
            direct = client.get(f"/structures/{duplicate_id}")
            assert direct.status_code == 500
            detail = direct.json()["errors"][0]["detail"]
            assert duplicate_id in detail
            assert "first" in detail and "second" in detail
            assert "audit_duplicate_ids" in detail
            assert "SELECT" not in detail.upper()
            assert "source_index" not in detail

            page = client.get("/structures", params={"page_limit": "1"})
            assert page.status_code == 500

        federation = adapter.federations["structures"]
        with pytest.raises(DuplicateEntryIdError):
            federation.audit_duplicate_ids(batch_size=1)


def test_as_of_link_stabilizes_stored_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    with Backend.sqlite() as database:
        store = _store(database)
        store._clock = lambda: 1_000_000_000
        first = _structure("Na")
        second = _structure("Na", "Cl")
        first_id = _save_entry(store, first)
        second_id = _save_entry(store, second)
        adapter = adapter_from_stores((StoredEntrySource(store, StructureEntry, "main"),))

        monkeypatch.setattr("httk.serve.optimade.engine.processing.time.time_ns", lambda: 3_000_000_000)
        with _client(adapter) as client:
            first_page = client.get("/structures", params={"sort": "id", "page_limit": "1"}).json()
            assert first_page["meta"]["data_available"] == 2
            assert "_httk_as_of=2999999999" in first_page["links"]["next"]

            store._clock = lambda: 4_000_000_000
            added = _structure("Si")
            added_id = _save_entry(store, added)

            pages = [first_page]
            while pages[-1]["links"]["next"] is not None:
                pages.append(client.get(pages[-1]["links"]["next"]).json())

            second_page = pages[-1]
            assert [item["id"] for item in second_page["data"]] == [max(first_id, second_id)]
            assert second_page["meta"]["data_available"] == 2
            assert second_page["meta"]["more_data_available"] is False
            assert second_page["links"]["next"] is None
            assert {item["id"] for page in pages for item in page["data"]} == {first_id, second_id}

            monkeypatch.setattr("httk.serve.optimade.engine.processing.time.time_ns", lambda: 5_000_000_000)
            fresh = client.get("/structures", params={"sort": "id"}).json()
            assert {item["id"] for item in fresh["data"]} == {first_id, second_id, added_id}


def test_resolution_aware_snapshot_excludes_later_row_in_current_second(monkeypatch: pytest.MonkeyPatch) -> None:
    with Backend.sqlite() as database:
        store = _store(database, store_timestamp_resolution=1_000_000_000)
        store._clock = lambda: 9_900_000_000
        old = _structure("Na")
        old_id = _save_entry(store, old)
        store._clock = lambda: 10_500_000_000
        later = _structure("Si")
        later_id = _save_entry(store, later)
        adapter = adapter_from_stores((StoredEntrySource(store, StructureEntry, "main"),))

        monkeypatch.setattr("httk.serve.optimade.engine.processing.time.time_ns", lambda: 10_900_000_000)
        with _client(adapter) as client:
            snapshot = client.get("/structures", params={"sort": "id"}).json()
            assert {item["id"] for item in snapshot["data"]} == {old_id}

            monkeypatch.setattr("httk.serve.optimade.engine.processing.time.time_ns", lambda: 11_100_000_000)
            fresh = client.get("/structures", params={"sort": "id"}).json()
            assert {item["id"] for item in fresh["data"]} == {old_id, later_id}


def test_microsecond_snapshot_includes_previous_unit_and_excludes_current_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Backend.sqlite() as database:
        store = _store(database, store_timestamp_resolution=1_000)
        store._clock = lambda: 2_000_000_999
        previous = _structure("Na")
        previous_id = _save_entry(store, previous)
        store._clock = lambda: 2_000_001_000
        current = _structure("Si")
        _save_entry(store, current)
        adapter = adapter_from_stores((StoredEntrySource(store, StructureEntry, "main"),))

        monkeypatch.setattr("httk.serve.optimade.engine.processing.time.time_ns", lambda: 2_000_001_999)
        with _client(adapter) as client:
            snapshot = client.get("/structures", params={"sort": "id"}).json()
            assert {item["id"] for item in snapshot["data"]} == {previous_id}


def test_timestamp_disabled_federation_skips_snapshot_links_and_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    with Backend.sqlite() as database:
        store = _store(database, store_timestamps=False)
        store.save(_structure("Na"))
        store.save(_structure("Si"))
        adapter = adapter_from_stores((StoredEntrySource(store, StructureEntry, "main"),))
        federation = adapter.federations["structures"]
        original_query = federation.query
        as_of_values: list[object] = []

        def tracked_query(*args: object, **kwargs: object):
            as_of_values.append(kwargs.get("as_of"))
            return original_query(*args, **kwargs)

        monkeypatch.setattr(federation, "query", tracked_query)
        with _client(adapter) as client:
            page = client.get("/structures", params={"page_limit": "1"}).json()
            assert "_httk_as_of" not in page["links"]["next"]
            client.get(page["links"]["next"])
            incoming = client.get("/structures?page_limit=1&_httk_as_of=42").json()
            assert "_httk_as_of" not in incoming["links"]["next"]

        assert as_of_values and set(as_of_values) == {None}


def test_as_of_single_entry_fetch_hides_later_store_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    with Backend.sqlite() as database:
        store = _store(database)
        store._clock = lambda: 1_000_000_000
        saved = _structure("Na")
        _save_entry(store, saved)
        store._clock = lambda: 3_000_000_000
        later = _structure("Si")
        later_id = _save_entry(store, later)
        adapter = adapter_from_stores((StoredEntrySource(store, StructureEntry, "main"),))

        with _client(adapter) as client:
            hidden = client.get(f"/structures/{later_id}", params={"_httk_as_of": 2_000_000_000})
            assert hidden.status_code == 200
            assert hidden.json()["data"] is None
            monkeypatch.setattr("httk.serve.optimade.engine.processing.time.time_ns", lambda: 4_000_000_000)
            visible = client.get(f"/structures/{later_id}")
            assert visible.status_code == 200
            assert visible.json()["data"]["id"] == later_id


def test_structure_revisions_are_available_over_the_stored_federation() -> None:
    """Store-backed structure routes expose latest and immutable revisions."""
    with Backend.sqlite() as database:
        store = _store(database)
        first = _structure("Na")
        first_id = _save_entry(store, first)
        predecessor = store.fetch_entry(StructureEntry, first.id)
        assert predecessor is not None
        store.replace(predecessor, _structure("Na", "Cl"))
        second_id = _save_entry(store, _structure("Si", "O"))
        app = create_asgi_app(
            adapter_from_stores((StoredEntrySource(store, StructureEntry, "main"),)),
            baseurl="http://testserver",
        )
        client = AsgiSyncClient(app, base_url="http://testserver")

        latest = client.get("http://testserver/structures").json()
        assert latest["meta"]["data_returned"] == 2
        latest_first = next(item for item in latest["data"] if item["id"] == first_id)
        assert latest_first["attributes"]["immutable_id"] == first_id + "~2"

        direct = client.get("http://testserver/structures/" + first_id)
        assert direct.status_code == 200
        assert direct.json()["data"]["attributes"]["immutable_id"] == first_id + "~2"

        lineage_revisions = client.get("http://testserver/structures/" + first_id + "/_httk_revs")
        assert lineage_revisions.status_code == 200
        lineage_payload: Mapping[str, Any] = lineage_revisions.json()
        assert [item["id"] for item in lineage_payload["data"]] == [first_id + "~1", first_id + "~2"]
        assert {item["attributes"]["_httk_id"] for item in lineage_payload["data"]} == {first_id}
        assert lineage_payload["meta"]["data_returned"] == 2
        assert lineage_payload["meta"]["data_available"] == 2

        first_revision = client.get("http://testserver/structures/" + first_id + "/_httk_revs/1")
        assert first_revision.status_code == 200
        assert first_revision.json()["data"]["id"] == first_id + "~1"
        assert client.get("http://testserver/structures/" + first_id + "/_httk_revs/7").status_code == 404
        assert client.get("http://testserver/structures/" + second_id + "/_httk_revs/2").status_code == 404

        all_revisions = client.get("http://testserver/_httk_structures~revs")
        assert all_revisions.status_code == 200
        all_payload: Mapping[str, Any] = all_revisions.json()
        assert all_payload["meta"]["data_available"] == 3
        assert len(all_payload["data"]) == 3
        filtered = client.get("http://testserver/_httk_structures~revs?filter=nelements%3D1").json()
        assert [item["id"] for item in filtered["data"]] == [first_id + "~1"]
        global_revision = client.get("http://testserver/_httk_structures~revs/" + first_id + "~1")
        assert global_revision.status_code == 200
        assert global_revision.json()["data"]["id"] == first_id + "~1"

        info = client.get("http://testserver/info").json()
        assert "_httk_structures~revs" in info["data"]["attributes"]["available_endpoints"]
        revision_info = client.get("http://testserver/info/_httk_structures~revs").json()
        assert "_httk_id" in revision_info["data"]["properties"]
        next_link = client.get("http://testserver/structures/" + first_id + "/_httk_revs?page_limit=1").json()["links"][
            "next"
        ]
        assert "/structures/" + first_id + "/_httk_revs?" in next_link


def test_structure_alternatives_are_available_over_the_stored_federation() -> None:
    """Store-backed structure routes expose named alternatives with composite ids, mains-only elsewhere."""
    with Backend.sqlite() as database:
        store = _store(database)
        main = _structure("Na")
        main_id = _save_entry(store, main)
        # A conventional alternative, replaced once (latest revision must win), plus a primitive one.
        conventional_sid = store.save(_structure("Na"), alternative_of=main_id, alternative_kind="conventional")
        conventional = store.fetch(UnitcellStructureRecord, conventional_sid, eager=True)
        store.replace(conventional, _structure("Na", "Cl", "O"))
        store.save(_structure("Si"), alternative_of=main_id, alternative_kind="primitive")
        other_id = _save_entry(store, _structure("Si", "O"))
        app = create_asgi_app(
            adapter_from_stores((StoredEntrySource(store, StructureEntry, "main"),)),
            baseurl="http://testserver",
        )
        client = AsgiSyncClient(app, base_url="http://testserver")

        mains = client.get("http://testserver/structures").json()
        assert {item["id"] for item in mains["data"]} == {main_id, other_id}

        direct = client.get("http://testserver/structures/" + main_id)
        assert direct.status_code == 200
        assert direct.json()["data"]["id"] == main_id

        group = client.get("http://testserver/structures/" + main_id + "/_httk_alts")
        assert group.status_code == 200
        group_payload: Mapping[str, Any] = group.json()
        assert {item["id"] for item in group_payload["data"]} == {
            main_id + "~conventional",
            main_id + "~primitive",
        }
        assert {item["attributes"]["_httk_id"] for item in group_payload["data"]} == {main_id}
        assert {item["attributes"]["_httk_kind"] for item in group_payload["data"]} == {"conventional", "primitive"}
        assert group_payload["meta"]["data_returned"] == 2
        assert group_payload["meta"]["data_available"] == 2

        conventional_single = client.get("http://testserver/structures/" + main_id + "/_httk_alts/conventional")
        assert conventional_single.status_code == 200
        assert conventional_single.json()["data"]["id"] == main_id + "~conventional"
        assert conventional_single.json()["data"]["attributes"]["_httk_kind"] == "conventional"
        # The replaced conventional alternative resolves to its latest revision (3 elements).
        assert conventional_single.json()["data"]["attributes"]["nelements"] == 3
        assert client.get("http://testserver/structures/" + main_id + "/_httk_alts/tetragonal").status_code == 404

        all_alternatives = client.get("http://testserver/_httk_structures~alts")
        assert all_alternatives.status_code == 200
        all_payload: Mapping[str, Any] = all_alternatives.json()
        assert all_payload["meta"]["data_available"] == 2
        assert {item["id"] for item in all_payload["data"]} == {
            main_id + "~conventional",
            main_id + "~primitive",
        }
        filtered = client.get('http://testserver/_httk_structures~alts?filter=_httk_kind%3D%22conventional%22').json()
        assert [item["id"] for item in filtered["data"]] == [main_id + "~conventional"]
        global_single = client.get("http://testserver/_httk_structures~alts/" + main_id + "~conventional")
        assert global_single.status_code == 200
        assert global_single.json()["data"]["id"] == main_id + "~conventional"

        # Composite ids are not served as mains on the base endpoint (store defaults serve mains only).
        base_composite = client.get("http://testserver/structures/" + main_id + "~conventional")
        assert base_composite.status_code == 200
        assert base_composite.json()["data"] is None
        # Revision routes remain mains-only: an alternative's composite revision id never resolves.
        assert client.get("http://testserver/_httk_structures~revs/" + main_id + "~conventional~1").status_code == 404

        info = client.get("http://testserver/info").json()
        assert "_httk_structures~alts" in info["data"]["attributes"]["available_endpoints"]
        alternative_info = client.get("http://testserver/info/_httk_structures~alts").json()
        assert "_httk_kind" in alternative_info["data"]["properties"]
        assert "_httk_id" in alternative_info["data"]["properties"]
        next_link = client.get("http://testserver/structures/" + main_id + "/_httk_alts?page_limit=1").json()["links"][
            "next"
        ]
        assert "/structures/" + main_id + "/_httk_alts?" in next_link


def test_prefixed_source_resolves_alternatives_by_composite_id() -> None:
    """A source public prefix (and non-conforming lineage ids) must survive composite alternative routing."""
    with Backend.sqlite() as database:
        store = _store(database)
        main_id = _save_entry(store, _structure("Na"))
        store.save(_structure("Na", "Cl"), alternative_of=main_id, alternative_kind="conventional")
        adapter = adapter_from_stores((StoredEntrySource(store, StructureEntry, "alpha", "alpha-"),))
        client = AsgiSyncClient(create_asgi_app(adapter, baseurl="http://testserver"), base_url="http://testserver")
        public_id = "alpha-" + main_id

        per_group = client.get("http://testserver/structures/" + public_id + "/_httk_alts/conventional")
        assert per_group.status_code == 200
        assert per_group.json()["data"]["id"] == public_id + "~conventional"

        global_single = client.get("http://testserver/_httk_structures~alts/" + public_id + "~conventional")
        assert global_single.status_code == 200
        assert global_single.json()["data"]["id"] == public_id + "~conventional"
