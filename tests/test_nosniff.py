"""Every OPTIMADE response carries X-Content-Type-Options: nosniff."""

from fake_backend import FakeStore
from materials_fixtures import materials_field_handlers, materials_schema
from starlette.testclient import TestClient

from httk.serve.optimade import BackendAdapter, EntrySource, create_asgi_app


def _client() -> TestClient:
    adapter = BackendAdapter(
        store=FakeStore(),
        sources={
            "structures": (EntrySource(target="structure-table", fields={}),),
            "calculations": (EntrySource(target="calc-table", fields={}),),
        },
        schema=materials_schema(),
        field_handlers=materials_field_handlers(),
    )
    return TestClient(create_asgi_app(adapter, baseurl="http://testserver"), base_url="http://testserver")


def test_json_response_has_nosniff() -> None:
    with _client() as client:
        response = client.get("/v1/info")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.api+json"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_versions_csv_response_has_nosniff() -> None:
    with _client() as client:
        response = client.get("/versions")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_error_response_has_nosniff() -> None:
    with _client() as client:
        response = client.get("/nosuch-endpoint")
    assert response.status_code == 404
    assert response.headers["x-content-type-options"] == "nosniff"
