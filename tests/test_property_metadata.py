from typing import Any

from fake_backend import FakeStore
from materials_fixtures import materials_schema
from starlette.testclient import TestClient

from httk.optimade import BackendAdapter, EntrySource, create_asgi_app
from httk.optimade.engine.validate import validate_optimade_request
from httk.optimade.model import RawRequest
from httk.optimade.schema.property_definitions import property_definition
from httk.optimade.schema.served import build_served_schema


def make_request(representation: str) -> RawRequest:
    return RawRequest(baseurl="http://localhost/", representation=representation)


# --- property definitions emit dimensions + metadata definition ---------------


def test_lattice_vectors_definition_has_dimensions() -> None:
    schema = materials_schema()
    definition = schema.property_definitions["structures"]["lattice_vectors"]
    assert definition["x-optimade-dimensions"] == {
        "names": ["dim_lattice", "dim_spatial"],
        "sizes": [3, 3],
    }


def test_cartesian_site_positions_dimensions_open_ended() -> None:
    schema = materials_schema()
    definition = schema.property_definitions["structures"]["cartesian_site_positions"]
    assert definition["x-optimade-dimensions"] == {
        "names": ["dim_sites", "dim_spatial"],
        "sizes": [None, 3],
    }


def test_generated_metadata_definition_has_list_axes() -> None:
    schema = materials_schema()
    definition = schema.property_definitions["structures"]["lattice_vectors"]
    metadata_definition = definition["x-optimade-metadata-definition"]
    assert metadata_definition["type"] == ["object", "null"]
    list_axes = metadata_definition["properties"]["list_axes"]
    item_properties = list_axes["items"]["properties"]
    for field in ("dimension_name", "requested_slice", "length", "sliceable", "available_slice"):
        assert field in item_properties
    assert item_properties["dimension_name"]["type"] == ["string"]
    assert set(item_properties["requested_slice"]["properties"]) == {"start", "stop", "step"}


def test_property_without_dimensions_has_no_metadata_definition() -> None:
    schema = materials_schema()
    definition = schema.property_definitions["structures"]["nelements"]
    assert "x-optimade-dimensions" not in definition
    assert "x-optimade-metadata-definition" not in definition


def test_explicit_metadata_definition_is_used() -> None:
    explicit = {"title": "explicit", "type": ["object", "null"]}
    info: dict[str, Any] = {
        "fulltype": "list of float",
        "dimensions": {"names": ["dim_x"], "sizes": [None]},
        "metadata_definition": explicit,
    }
    definition = property_definition("structures", "_httk_thing", info)  # type: ignore[arg-type]
    assert definition["x-optimade-metadata-definition"] == explicit


# --- response_fields=property_metadata is accepted ----------------------------


def _metadata_schema() -> Any:
    return build_served_schema(
        {"structures": ["id", "type", "elements", "nelements"]},
        default_response_overrides={"structures": ["elements", "nelements"]},
    )


def test_response_fields_accepts_property_metadata_token() -> None:
    schema = _metadata_schema()
    validated = validate_optimade_request(
        make_request("/structures?response_fields=elements,property_metadata"), "1.3.0", schema
    )
    assert validated.property_metadata_requested is True
    assert "property_metadata" not in validated.recognized_response_fields
    assert "property_metadata" not in validated.unrecognized_response_fields
    assert validated.recognized_response_fields == ["elements", "id", "type"]


def test_property_metadata_token_not_requested_by_default() -> None:
    schema = _metadata_schema()
    validated = validate_optimade_request(make_request("/structures"), "1.3.0", schema)
    assert validated.property_metadata_requested is False


# --- end to end: resource meta.property_metadata ------------------------------


class Row:
    def __init__(self, sid: str) -> None:
        self.sid = sid


def make_client() -> TestClient:
    store = FakeStore(rows_by_target={"structure-table": [Row("demo-1")]})
    schema = _metadata_schema()
    fields: dict[str, Any] = {
        "type": lambda x: "structures",
        "id": lambda x: x.sid,
        "elements": lambda x: ["Si", "O"],
        "nelements": lambda x: 2,
    }
    property_metadata: dict[str, Any] = {
        "elements": lambda x: {"_httk_originates_from": "demo_project"},
    }
    adapter = BackendAdapter(
        store=store,
        sources={
            "structures": (EntrySource(target="structure-table", fields=fields, property_metadata=property_metadata),),
        },
        schema=schema,
    )
    app = create_asgi_app(adapter, baseurl="http://testserver/")
    return TestClient(app, base_url="http://testserver")


def test_asgi_resource_carries_property_metadata() -> None:
    client = make_client()
    response = client.get("/structures/demo-1")
    assert response.status_code == 200
    payload = response.json()
    meta = payload["data"]["meta"]
    assert meta["property_metadata"]["elements"] == {"_httk_originates_from": "demo_project"}


def test_asgi_response_fields_property_metadata_accepted() -> None:
    client = make_client()
    response = client.get("/structures", params={"response_fields": "elements,property_metadata"})
    assert response.status_code == 200
    payload = response.json()
    attributes = payload["data"][0]["attributes"]
    assert "property_metadata" not in attributes
    assert attributes["elements"] == ["Si", "O"]
