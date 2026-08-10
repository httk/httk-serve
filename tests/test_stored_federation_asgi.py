"""ASGI coverage for the lazy stored-entry federation adapter."""

import warnings
from contextlib import ExitStack
from fractions import Fraction

import pytest
from httk.atomistic import (
    WyckoffSite,
    ASUStructure,
    ASUStructureRecord,
    Cell,
    Sites,
    Species,
    UnitcellStructure,
    StructureEntry,
    UnitcellStructureRecord,
)
from httk.store.db import (
    Database,
    DuplicateEntryIdError,
    SqlStore,
    StoredEntrySource,
)
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


def test_single_store_preserves_public_prefix_and_prefixed_property_name() -> None:
    source = _structure("Na", basis_precision=Fraction(1, 1000))
    with Database.sqlite() as database:
        store = SqlStore(database, entry_records={StructureEntry: UnitcellStructureRecord})
        store.save(source)
        adapter = adapter_from_stores((StoredEntrySource(store, StructureEntry, "alpha", "alpha-"),))

        assert {"id", "type"} <= set(adapter.schema.sortable_response_fields["structures"])
        public_id = "alpha-" + source.id
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
    with Database.sqlite() as database:
        store = SqlStore(
            database,
            entry_records={StructureEntry: (UnitcellStructureRecord, ASUStructureRecord)},
        )
        store.save(unitcell)
        store.save(asu)
        adapter = adapter_from_stores((StoredEntrySource(store, StructureEntry, "mixed"),))

        with _client(adapter) as client:
            response = client.get(
                "/structures",
                params={"sort": "id", "response_fields": "id,type,site_coordinate_span"},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["meta"]["data_available"] == 2
            assert [item["id"] for item in payload["data"]] == sorted((unitcell.id, asu.id))
            assert {item["attributes"]["site_coordinate_span"] for item in payload["data"]} == {
                "unit_cell",
                "asymmetric_unit",
            }


def test_multiple_sources_push_filter_sort_and_pagination() -> None:
    with ExitStack() as stack:
        first_database = stack.enter_context(Database.sqlite())
        second_database = stack.enter_context(Database.sqlite())
        first = SqlStore(first_database, entry_records={StructureEntry: UnitcellStructureRecord})
        second = SqlStore(second_database, entry_records={StructureEntry: UnitcellStructureRecord})
        first_sources = (_structure("Na"), _structure("Na", "Cl"))
        second_sources = (_structure("Si"), _structure("Si", "O"))
        for structure in first_sources:
            first.save(structure)
        for structure in second_sources:
            second.save(structure)

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
            assert first_page["meta"]["data_available"] == 2
            assert first_page["meta"]["data_returned"] == 1
            assert first_page["meta"]["more_data_available"] is True
            first_id = first_page["data"][0]["id"]

            next_response = client.get(first_page["links"]["next"])
            assert next_response.status_code == 200
            second_page = next_response.json()
            second_id = second_page["data"][0]["id"]
            assert [first_id, second_id] == sorted(("a-" + first_sources[0].id, "b-" + second_sources[0].id))
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
        first_database = stack.enter_context(Database.sqlite())
        second_database = stack.enter_context(Database.sqlite())
        first = SqlStore(first_database, entry_records={StructureEntry: UnitcellStructureRecord})
        second = SqlStore(second_database, entry_records={StructureEntry: UnitcellStructureRecord})
        first.save(duplicated)
        second.save(duplicated)
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
            direct = client.get(f"/structures/{duplicated.id}")
            assert direct.status_code == 500
            detail = direct.json()["errors"][0]["detail"]
            assert duplicated.id in detail
            assert "first" in detail and "second" in detail
            assert "audit_duplicate_ids" in detail
            assert "SELECT" not in detail.upper()
            assert "source_index" not in detail

            page = client.get("/structures", params={"page_limit": "1"})
            assert page.status_code == 500

        federation = adapter.federations["structures"]
        with pytest.raises(DuplicateEntryIdError):
            federation.audit_duplicate_ids(batch_size=1)
