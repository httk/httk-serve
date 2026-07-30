from typing import Any

from definition_fixtures import served_schema
from fake_backend import FakeStore
from starlette.testclient import TestClient

from httk.serve.optimade import BackendAdapter, EntrySource, create_asgi_app
from httk.serve.optimade.endpoints.info import generate_info_endpoint_reply
from httk.serve.optimade.endpoints.meta import generate_meta
from httk.serve.optimade.engine.validate import validate_optimade_request
from httk.serve.optimade.model import OptimadeConfig, RawRequest


def make_request(representation: str) -> RawRequest:
    return RawRequest(baseurl="http://localhost/", representation=representation)


def _schema() -> Any:
    return served_schema(
        {"structures": ["id", "type", "elements", "nelements"]},
        default_response_overrides={"structures": ["elements", "nelements"]},
    )


class Row:
    def __init__(self, sid: str) -> None:
        self.sid = sid


def make_client(config: OptimadeConfig | None = None, ids: list[str] | None = None) -> TestClient:
    if ids is None:
        ids = ["demo-1", "demo-2"]
    store = FakeStore(rows_by_target={"structure-table": [Row(i) for i in ids]})
    schema = _schema()
    fields: dict[str, Any] = {
        "type": lambda x: "structures",
        "id": lambda x: x.sid,
        "elements": lambda x: ["Si", "O"],
        "nelements": lambda x: 2,
    }
    adapter = BackendAdapter(
        store=store,
        sources={"structures": (EntrySource(target="structure-table", fields=fields),)},
        schema=schema,
    )
    app = create_asgi_app(adapter, config, baseurl="http://testserver/")
    return TestClient(app, base_url="http://testserver")


# --- base-info license fields -------------------------------------------------


def test_base_info_license_fields_present_when_configured() -> None:
    config = OptimadeConfig(
        license={"href": "https://creativecommons.org/licenses/by/4.0/"},
        available_licenses=["CC-BY-4.0", "CC0-1.0"],
        available_licenses_for_entries=["CC-BY-4.0"],
    )
    request = validate_optimade_request(make_request("/info"), "1.3.0", _schema())
    reply = generate_info_endpoint_reply(request, config, _schema())
    attributes = reply["data"]["attributes"]
    assert attributes["license"] == {"href": "https://creativecommons.org/licenses/by/4.0/"}
    assert attributes["available_licenses"] == ["CC-BY-4.0", "CC0-1.0"]
    assert attributes["available_licenses_for_entries"] == ["CC-BY-4.0"]


def test_base_info_license_fields_absent_by_default() -> None:
    request = validate_optimade_request(make_request("/info"), "1.3.0", _schema())
    reply = generate_info_endpoint_reply(request, OptimadeConfig(), _schema())
    attributes = reply["data"]["attributes"]
    assert "license" not in attributes
    assert "available_licenses" not in attributes
    assert "available_licenses_for_entries" not in attributes


# --- warnings -----------------------------------------------------------------


def test_validate_produces_warning_for_foreign_prefix_response_field() -> None:
    validated = validate_optimade_request(
        make_request("/structures?response_fields=elements,_other_prop"), "1.3.0", _schema()
    )
    assert "_other_prop" in validated.unrecognized_response_fields
    assert len(validated.warnings) == 1
    warning = validated.warnings[0]
    assert warning["type"] == "warning"
    assert "_other_prop" in warning["detail"]
    assert "status" not in warning


def test_meta_emits_warnings_for_foreign_prefix_response_field() -> None:
    client = make_client()
    response = client.get("/structures", params={"response_fields": "elements,_other_prop"})
    assert response.status_code == 200
    warnings = response.json()["meta"]["warnings"]
    assert len(warnings) == 1
    assert warnings[0]["type"] == "warning"
    assert "detail" in warnings[0]
    assert "status" not in warnings[0]


def test_meta_warnings_absent_without_foreign_prefix() -> None:
    client = make_client()
    response = client.get("/structures", params={"response_fields": "elements"})
    assert response.status_code == 200
    assert "warnings" not in response.json()["meta"]


def test_generate_meta_warnings_and_last_id_omitted_when_empty() -> None:
    meta = generate_meta(representation="/structures", api_version="1.3.0", config=OptimadeConfig())
    assert "warnings" not in meta
    assert "last_id" not in meta
    meta_empty = generate_meta(representation="/structures", api_version="1.3.0", config=OptimadeConfig(), warnings=[])
    assert "warnings" not in meta_empty


# --- last_id ------------------------------------------------------------------


def test_last_id_equals_last_data_id_on_listing() -> None:
    client = make_client(ids=["demo-1", "demo-2", "demo-3"])
    response = client.get("/structures")
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["last_id"] == payload["data"][-1]["id"]
    assert payload["meta"]["last_id"] == "demo-3"


def test_last_id_absent_on_empty_listing() -> None:
    client = make_client(ids=[])
    response = client.get("/structures")
    assert response.status_code == 200
    assert "last_id" not in response.json()["meta"]


# --- links.describedby --------------------------------------------------------


def test_describedby_present_on_listing_when_schema_url_set() -> None:
    config = OptimadeConfig(schema_url="https://schemas.optimade.org/openapi/v1.3/optimade.json")
    client = make_client(config)
    response = client.get("/structures")
    assert response.status_code == 200
    assert response.json()["links"]["describedby"] == "https://schemas.optimade.org/openapi/v1.3/optimade.json"


def test_describedby_present_on_single_when_schema_url_set() -> None:
    config = OptimadeConfig(schema_url="https://schemas.optimade.org/openapi/v1.3/optimade.json")
    client = make_client(config, ids=["demo-1"])
    response = client.get("/structures/demo-1")
    assert response.status_code == 200
    assert response.json()["links"]["describedby"] == "https://schemas.optimade.org/openapi/v1.3/optimade.json"


def test_describedby_absent_when_schema_url_unset() -> None:
    client = make_client()
    response = client.get("/structures")
    assert response.status_code == 200
    assert "describedby" not in response.json()["links"]
