"""Full provider-to-ASGI coverage for native and stored atomistic structures."""

import datetime
import math
from fractions import Fraction

import pytest
from httk.atomistic import (
    Assembly,
    Species,
    StructureEntry,
    StructureEntryProvider,
    UnitcellStructure,
    UnitcellStructureRecord,
    UnitcellStructureView,
)
from httk.core import Dataset
from httk.store.db import Database, SqlStore, StoredEntrySource
from starlette.testclient import TestClient

from httk.serve.dsp import DspDatasetPublication, DspPublicationEntry
from httk.serve.optimade import adapter_from_providers, adapter_from_store, adapter_from_stores, create_asgi_app

STANDARD_STRUCTURE_PROPERTIES = {
    "id",
    "type",
    "immutable_id",
    "last_modified",
    "elements",
    "nelements",
    "elements_ratios",
    "chemical_formula_descriptive",
    "chemical_formula_reduced",
    "chemical_formula_hill",
    "chemical_formula_anonymous",
    "dimension_types",
    "nperiodic_dimensions",
    "lattice_vectors",
    "space_group_symmetry_operations_xyz",
    "space_group_symbol_hall",
    "space_group_symbol_hermann_mauguin",
    "space_group_symbol_hermann_mauguin_extended",
    "space_group_it_number",
    "cartesian_site_positions",
    "fractional_site_positions",
    "site_coordinate_span",
    "site_coordinate_span_description",
    "nsites",
    "species_at_sites",
    "species",
    "assemblies",
    "wyckoff_positions",
    "structure_features",
    "optimization_type",
}


def test_create_asgi_app_discovers_optimade_families_from_mixed_store_lazily() -> None:
    entries = tuple(_entries().values())
    store = SqlStore(
        Database.sqlite(),
        entry_records={
            StructureEntry: UnitcellStructureRecord,
            DspPublicationEntry: DspDatasetPublication,
        },
    )
    store.save(
        DspDatasetPublication(
            Dataset(
                "https://provider.example/datasets/one",
                "Dataset",
                "Description",
                "https://provider.example/publisher",
                "Publisher",
            ),
            "/files/one.csv",
        )
    )
    store.save(entries[0])
    adapter = adapter_from_store(store)
    assert set(adapter.schema.all_entries) == {"structures"}
    app = create_asgi_app(store, baseurl="http://testserver")

    with TestClient(app, base_url="http://testserver") as client:
        assert client.get("/structures").json()["meta"]["data_available"] == 1
        assert client.get("/dsp-publications").status_code == 404
        store.save(entries[1])
        assert client.get("/structures").json()["meta"]["data_available"] == 2


def _entries() -> dict[str, UnitcellStructure]:
    mixed_species = Species(
        name="mixed",
        chemical_symbols=("Ge", "Si"),
        concentration=(Fraction(5, 8), Fraction(3, 8)),
    )
    mixed = UnitcellStructure(
        [[3, 0, 0], [0, 3, 0], [0, 0, 3]],
        [[0, 0, 0]],
        [mixed_species],
        ["mixed"],
        assemblies=[Assembly(((0,),), (Fraction(1),))],
        chemical_formula_descriptive="Ge5Si3",
        chemical_formula_hill="Ge5Si3",
        optimization_type="local",
        immutable_id="source/mixed",
        last_modified=datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC),
    )
    silicon_species = Species(name="Si", chemical_symbols=("Si",), concentration=(1,))
    silicon = UnitcellStructure(
        [[4, 0, 0], [0, 4, 0], [0, 0, 4]],
        [[0, 0, 0]],
        [silicon_species],
        ["Si"],
        chemical_formula_descriptive="Si",
        chemical_formula_hill="Si",
        optimization_type="experimental",
        immutable_id=None,
        last_modified=datetime.datetime(2024, 1, 2, 3, 4, 5, tzinfo=datetime.UTC),
    )
    return {"mixed": mixed, "silicon": silicon}


def test_stored_structure_preserves_signed_zero_through_view_and_asgi() -> None:
    species = Species(name="Si", chemical_symbols=("Si",), concentration=(1,), mass=(-0.0,))
    structure = UnitcellStructure(
        [[4, 0, 0], [0, 4, 0], [0, 0, 4]],
        [[0, 0, 0]],
        [species],
        ["Si"],
    )
    key = structure.id
    with Database.sqlite() as database:
        SqlStore(database, entry_records={StructureEntry: UnitcellStructureRecord}).save(structure)
        reopened = SqlStore(database)
        record = reopened.fetch_entry(StructureEntry, key)
        assert isinstance(record, UnitcellStructureRecord)
        view = UnitcellStructureView(record)
        assert record.id == key
        assert view.id == key
        assert math.copysign(1.0, view.species[0].mass[0]) == -1.0

        app = create_asgi_app(
            adapter_from_stores([StoredEntrySource(reopened, StructureEntry, "signed-zero")]),
            baseurl="http://testserver",
        )
        with TestClient(app, base_url="http://testserver") as client:
            response = client.get(f"/structures/{key}")
        assert response.status_code == 200
        served_mass = response.json()["data"]["attributes"]["species"][0]["mass"][0]
        assert math.copysign(1.0, served_mass) == -1.0


@pytest.fixture(params=("atomistic", "sqlite-record"))
def structure_api(request):
    entries = _entries()
    if request.param == "atomistic":
        provider = StructureEntryProvider(entries)
        app = create_asgi_app(adapter_from_providers([provider]), baseurl="http://testserver")
        with TestClient(app, base_url="http://testserver") as client:
            yield request.param, client
        return

    with Database.sqlite() as database:
        store = SqlStore(database, entry_records={StructureEntry: UnitcellStructureRecord})
        with store.transaction():
            sids = {entry_id: store.save(structure) for entry_id, structure in entries.items()}
        provider = StructureEntryProvider(
            {entry_id: store.fetch(UnitcellStructureRecord, sid) for entry_id, sid in sids.items()}
        )
        app = create_asgi_app(adapter_from_providers([provider]), baseurl="http://testserver")
        with TestClient(app, base_url="http://testserver") as client:
            yield request.param, client


def test_structure_provider_info_exposes_complete_standard_contract(structure_api) -> None:
    _mode, client = structure_api
    response = client.get("/info/structures")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["id"] == "structures"
    assert payload["data"]["type"] == "info"
    assert STANDARD_STRUCTURE_PROPERTIES <= set(payload["data"]["properties"])
    assert STANDARD_STRUCTURE_PROPERTIES <= set(payload["data"]["output_fields_by_format"]["json"])
    assert payload["data"]["properties"]["elements"]["x-optimade-type"] == "list"
    assert payload["data"]["properties"]["chemical_formula_reduced"]["x-optimade-type"] == "string"


def test_structure_provider_listing_preserves_standard_semantics(structure_api) -> None:
    _mode, client = structure_api
    response = client.get("/structures")

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["data_available"] == 2
    assert payload["meta"]["data_returned"] == 2
    resources = {resource["id"]: resource for resource in payload["data"]}
    mixed = resources["mixed"]
    attributes = mixed["attributes"]
    expected_attributes = STANDARD_STRUCTURE_PROPERTIES - {"id", "type"}
    assert expected_attributes <= set(attributes)
    assert mixed["type"] == "structures"
    assert attributes["immutable_id"] == "source/mixed"
    assert attributes["last_modified"] == "2026-01-02T03:04:05+00:00"
    assert attributes["elements"] == ["Ge", "Si"]
    assert attributes["elements_ratios"] == [0.625, 0.375]
    assert attributes["chemical_formula_descriptive"] == "Ge5Si3"
    assert attributes["chemical_formula_reduced"] == "Ge5Si3"
    assert attributes["chemical_formula_hill"] == "Ge5Si3"
    assert attributes["chemical_formula_anonymous"] == "A5B3"
    assert attributes["dimension_types"] == [1, 1, 1]
    assert attributes["nperiodic_dimensions"] == 3
    assert attributes["lattice_vectors"] == [[3.0, 0.0, 0.0], [0.0, 3.0, 0.0], [0.0, 0.0, 3.0]]
    assert attributes["fractional_site_positions"] == [[0.0, 0.0, 0.0]]
    assert attributes["site_coordinate_span"] == "unit_cell"
    assert attributes["nsites"] == 1
    assert attributes["species_at_sites"] == ["mixed"]
    assert attributes["species"] == [
        {"name": "mixed", "chemical_symbols": ["Ge", "Si"], "concentration": [0.625, 0.375]}
    ]
    assert attributes["assemblies"] == [{"sites_in_groups": [[0]], "group_probabilities": [1.0]}]
    assert attributes["structure_features"] == ["assemblies", "disorder"]
    assert attributes["optimization_type"] == "local"

    silicon = resources["silicon"]["attributes"]
    assert expected_attributes <= set(silicon)
    assert silicon["immutable_id"] is None
    assert silicon["chemical_formula_reduced"] == "Si"
    assert silicon["assemblies"] is None
    assert silicon["wyckoff_positions"] is None
    assert silicon["site_coordinate_span_description"] is None
    assert silicon["space_group_symbol_hall"] is None
    assert silicon["space_group_symbol_hermann_mauguin"] is None
    assert silicon["space_group_symbol_hermann_mauguin_extended"] is None
    assert silicon["space_group_it_number"] is None
    assert silicon["structure_features"] == []


@pytest.mark.parametrize(
    "filter_string",
    (
        'id = "mixed"',
        'last_modified > "2025-01-01T00:00:00Z"',
        'chemical_formula_reduced = "Ge5Si3"',
        'chemical_formula_hill IS KNOWN AND elements HAS "Ge"',
        'structure_features HAS "disorder"',
        'site_coordinate_span = "unit_cell" AND optimization_type = "local"',
    ),
)
def test_structure_provider_standard_filters_reach_asgi(structure_api, filter_string: str) -> None:
    _mode, client = structure_api
    response = client.get("/structures", params={"filter": filter_string})

    assert response.status_code == 200
    payload = response.json()
    assert [resource["id"] for resource in payload["data"]] == ["mixed"]
    assert payload["meta"]["data_available"] == 1


def test_structure_provider_single_resource_response_fields(structure_api) -> None:
    _mode, client = structure_api
    response = client.get(
        "/structures/mixed",
        params={"response_fields": "chemical_formula_reduced,elements,species,assemblies,optimization_type"},
    )

    assert response.status_code == 200
    resource = response.json()["data"]
    assert resource["id"] == "mixed"
    assert resource["type"] == "structures"
    assert set(resource["attributes"]) == {
        "chemical_formula_reduced",
        "elements",
        "species",
        "assemblies",
        "optimization_type",
    }
    assert resource["attributes"]["chemical_formula_reduced"] == "Ge5Si3"
