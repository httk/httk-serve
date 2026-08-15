"""Filter-length cap and deep-nesting robustness for the query validator."""

import pytest
from fake_backend import FakeStore
from materials_fixtures import materials_field_handlers, materials_schema
from starlette.testclient import TestClient

from httk.serve.optimade import BackendAdapter, EntrySource, create_asgi_app
from httk.serve.optimade.engine.validate import FILTER_LENGTH_MAX, validate_optimade_request
from httk.serve.optimade.model import OptimadeError, RawRequest

SCHEMA = materials_schema()


def _request(filter_value: str) -> RawRequest:
    return RawRequest(  # type: ignore[call-arg]
        baseurl="http://localhost/",
        representation="/structures",
        relurl="/structures",
        query={"filter": filter_value},
    )


def test_overlong_filter_is_rejected() -> None:
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(_request("x" * (FILTER_LENGTH_MAX + 1)), "1.3.0", SCHEMA)
    assert excinfo.value.response_code == 400


def test_maximum_length_filter_passes_validation() -> None:
    # A filter of exactly the cap length is accepted by validation (its parse is
    # deferred to the filter parser and is not exercised here).
    validated = validate_optimade_request(_request("x" * FILTER_LENGTH_MAX), "1.3.0", SCHEMA)
    assert validated.query.filter == "x" * FILTER_LENGTH_MAX


def _client() -> TestClient:
    adapter = BackendAdapter(
        store=FakeStore(),
        sources={
            "structures": (EntrySource(target="structure-table", fields={}),),
            "calculations": (EntrySource(target="calc-table", fields={}),),
        },
        schema=SCHEMA,
        field_handlers=materials_field_handlers(),
    )
    return TestClient(create_asgi_app(adapter, baseurl="http://testserver"), base_url="http://testserver")


def test_deeply_nested_filter_is_a_bad_request_not_a_server_error() -> None:
    # ~1500 nested parentheses (~3 KB) stays well under the length cap so it
    # reaches the parser; a RecursionError there maps to 400, never 500.
    depth = 1500
    nested = "(" * depth + "nelements=1" + ")" * depth
    assert len(nested) < FILTER_LENGTH_MAX
    with _client() as client:
        response = client.get("/structures", params={"filter": nested})
    assert response.status_code == 400
    assert int(response.json()["errors"][0]["status"]) == 400
