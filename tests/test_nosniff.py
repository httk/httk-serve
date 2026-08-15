"""Every OPTIMADE response carries X-Content-Type-Options: nosniff."""

import pytest
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


@pytest.mark.parametrize(
    "path,status",
    (
        ("/v1/info", 200),
        ("/versions", 200),
        ("/nosuch-endpoint", 404),
    ),
)
def test_every_response_has_nosniff(path: str, status: int) -> None:
    with _client() as client:
        response = client.get(path)
    assert response.status_code == status
    assert response.headers["x-content-type-options"] == "nosniff"
