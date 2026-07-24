from dataclasses import dataclass
from typing import Any

import pytest
from definition_fixtures import served_schema
from fake_backend import FakeStore
from starlette.testclient import TestClient
from test_asgi import STRUCTURE_FIELDS, Row

from httk.optimade import (
    BackendAdapter,
    EntrySource,
    OptimadeError,
    RawRequest,
    create_asgi_app,
)
from httk.optimade.backend import execute_query, translate_filter
from httk.optimade.engine.validate import validate_optimade_request
from httk.optimade.model import TranslatorError


def sortable_structures_schema() -> Any:
    return served_schema(
        {
            "structures": [
                "id",
                "type",
                "elements",
                "nelements",
                "chemical_formula_descriptive",
                "dimension_types",
                "nperiodic_dimensions",
                "lattice_vectors",
                "structure_features",
                "nsites",
                "species_at_sites",
                "cartesian_site_positions",
                "chemical_formula_anonymous",
                "chemical_formula_reduced",
            ],
            "calculations": ["id", "type"],
        },
        default_response_overrides={
            "structures": [
                "structure_features",
                "lattice_vectors",
                "elements",
                "nelements",
                "chemical_formula_descriptive",
                "dimension_types",
                "nperiodic_dimensions",
                "nsites",
                "species_at_sites",
                "cartesian_site_positions",
                "chemical_formula_anonymous",
                "chemical_formula_reduced",
            ],
        },
        sortable={"structures": ["id", "nelements"]},
    )


def make_request(representation: str, **kwargs: object) -> RawRequest:
    return RawRequest(baseurl="http://localhost/", representation=representation, **kwargs)  # type: ignore[arg-type]


# --- Validation ---------------------------------------------------------------


def test_sort_parses_into_pairs() -> None:
    schema = sortable_structures_schema()
    validated = validate_optimade_request(make_request("/structures?sort=nelements,-id"), "1.3.0", schema)
    assert validated.sort_fields == [("nelements", False), ("id", True)]
    assert validated.query.sort == "nelements,-id"


def test_sort_non_sortable_field_400() -> None:
    schema = sortable_structures_schema()
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(make_request("/structures?sort=chemical_formula_descriptive"), "1.3.0", schema)
    assert excinfo.value.response_code == 400


def test_sort_echoed_in_as_query_dict() -> None:
    schema = sortable_structures_schema()
    validated = validate_optimade_request(make_request("/structures?sort=-nelements"), "1.3.0", schema)
    assert validated.query.as_query_dict()["sort"] == "-nelements"


def test_sort_ignored_on_non_entry_endpoint() -> None:
    schema = sortable_structures_schema()
    validated = validate_optimade_request(make_request("/info?sort=whatever"), "1.3.0", schema)
    assert validated.query.sort is None
    assert validated.sort_fields == []


# --- Translation / execution --------------------------------------------------


def _structures_adapter(store: FakeStore) -> BackendAdapter:
    schema = served_schema(
        {"structures": ["id", "type", "nelements"]},
        sortable={"structures": ["id", "nelements"]},
    )
    return BackendAdapter(
        store=store,
        sources={
            "structures": (
                EntrySource(
                    target="structure-table",
                    fields=STRUCTURE_FIELDS,
                    sort_keys={"id": "__id", "nelements": "number_of_elements"},
                ),
            ),
        },
        schema=schema,
    )


def test_add_sort_recorded_in_declared_order() -> None:
    store = FakeStore(rows_by_target={"structure-table": []})
    adapter = _structures_adapter(store)
    pairs = translate_filter(None, ["structures"], adapter, [("nelements", False), ("id", True)])
    _, searcher = pairs[0]
    assert searcher.sorts == [("number_of_elements", False), ("__id", True)]  # type: ignore[attr-defined]


def test_sort_across_multiple_sources_501() -> None:
    schema = served_schema(
        {"calculations": ["id", "type"]},
        sortable={"calculations": ["id"]},
    )
    store = FakeStore(rows_by_target={"aimd-table": [], "elastic-table": []})
    adapter = BackendAdapter(
        store=store,
        sources={
            "calculations": (
                EntrySource(target="aimd-table", fields={}, sort_keys={"id": "__id"}),
                EntrySource(target="elastic-table", fields={}, sort_keys={"id": "__id"}),
            ),
        },
        schema=schema,
    )
    with pytest.raises(TranslatorError) as excinfo:
        execute_query(adapter, ["calculations"], ["id", "type"], [], 10, 0, sort=[("id", False)])
    assert excinfo.value.response_code == 501


def test_missing_sort_key_raises_on_construction() -> None:
    schema = served_schema(
        {"structures": ["id", "type", "nelements"]},
        sortable={"structures": ["nelements"]},
    )
    with pytest.raises(ValueError):
        BackendAdapter(
            store=FakeStore(),
            sources={"structures": (EntrySource(target="structure-table", fields={}),)},
            schema=schema,
        )


# --- Schema property definitions ----------------------------------------------


def test_property_definition_top_level_sortable_flag() -> None:
    schema = sortable_structures_schema()
    definitions = schema.property_definitions["structures"]
    assert definitions["nelements"]["sortable"] is True
    assert definitions["chemical_formula_descriptive"]["sortable"] is False


# --- ASGI end to end ----------------------------------------------------------


def make_sorting_client(n: int = 5) -> TestClient:
    rows = [Row(sid=f"s{i}", nelements=i) for i in range(n)]
    store = FakeStore(rows_by_target={"structure-table": rows, "calc-table": []})
    schema = sortable_structures_schema()
    adapter = BackendAdapter(
        store=store,
        sources={
            "structures": (
                EntrySource(
                    target="structure-table",
                    fields=STRUCTURE_FIELDS,
                    sort_keys={"id": "sid", "nelements": "nelements"},
                ),
            ),
            "calculations": (EntrySource(target="calc-table", fields={}, sort_keys={"id": "sid"}),),
        },
        schema=schema,
    )
    app = create_asgi_app(adapter, baseurl="http://testserver/")
    return TestClient(app, base_url="http://testserver")


def test_asgi_sort_descending() -> None:
    client = make_sorting_client()
    response = client.get("/structures", params={"sort": "-nelements"})
    assert response.status_code == 200
    payload = response.json()
    ids = [d["id"] for d in payload["data"]]
    assert ids == ["s4", "s3", "s2", "s1", "s0"]
    nelements = [d["attributes"]["nelements"] for d in payload["data"]]
    assert nelements == sorted(nelements, reverse=True)


def test_asgi_sort_bad_field_400() -> None:
    client = make_sorting_client()
    response = client.get("/structures", params={"sort": "bogus"})
    assert response.status_code == 400
    assert response.json()["errors"][0]["status"] == 400


def test_asgi_sort_next_link_carries_sort() -> None:
    client = make_sorting_client()
    response = client.get("/structures", params={"sort": "-nelements", "page_limit": "2"})
    assert response.status_code == 200
    payload = response.json()
    assert [d["id"] for d in payload["data"]] == ["s4", "s3"]
    next_link = payload["links"]["next"]
    assert "sort=-nelements" in next_link
