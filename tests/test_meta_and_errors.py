import pytest

from httk.serve.optimade.endpoints.error import format_optimade_error
from httk.serve.optimade.endpoints.meta import generate_meta
from httk.serve.optimade.model import OptimadeConfig, OptimadeError, RawRequest


def test_generate_meta_basic() -> None:
    config = OptimadeConfig()
    meta = generate_meta(representation="/structures", api_version="1.3.0", config=config)
    assert meta["query"]["representation"] == "/structures"
    assert meta["api_version"] == "1.3.0"
    assert meta["implementation"]["name"] == "httk-serve"
    assert meta["provider"]["prefix"] == "httk"
    assert meta["more_data_available"] is False
    assert "data_returned" not in meta
    assert "data_available" not in meta


def test_generate_meta_counts() -> None:
    config = OptimadeConfig()
    meta = generate_meta(
        representation="/structures",
        api_version="1.3.0",
        config=config,
        data_returned=7,
        more_data_available=True,
        data_available=42,
    )
    assert meta["data_returned"] == 7
    assert meta["data_available"] == 42
    assert meta["more_data_available"] is True


def test_format_optimade_error_from_optimade_error() -> None:
    config = OptimadeConfig()
    request = RawRequest(baseurl="http://localhost/", representation="/nosuch")
    output = format_optimade_error(OptimadeError("no such endpoint", 404, "Not Found"), request, config)
    assert output.response_code == 404
    assert output.response_msg == "Not Found"
    assert output.content_type == "application/vnd.api+json"
    assert output.json_response is not None
    error = output.json_response["errors"][0]
    assert error["status"] == 404
    assert error["title"] == "Not Found"
    assert error["detail"] == "no such endpoint"
    # Error replies carry no data member, so data_returned is omitted.
    assert "data_returned" not in output.json_response["meta"]


def test_format_optimade_error_from_generic_exception() -> None:
    config = OptimadeConfig()
    request = RawRequest(baseurl="http://localhost/", representation="/structures")
    output = format_optimade_error(ValueError("boom"), request, config, version="1.3.0")
    assert output.response_code == 500
    assert output.response_msg == "Internal Server Error"
    assert output.json_response is not None
    assert output.json_response["errors"][0]["detail"] == "boom"
    assert output.json_response["meta"]["api_version"] == "1.3.0"


def test_optimade_error_longmsg() -> None:
    err = OptimadeError("short", 400, "Bad request", longmsg="much longer explanation")
    assert err.content == "much longer explanation"
    err2 = OptimadeError("short", 400, "Bad request")
    assert err2.content == "short"


def test_generate_meta_implementation_fields() -> None:
    meta = generate_meta(representation="/", api_version="1.3.0", config=OptimadeConfig())
    implementation = meta["implementation"]
    assert implementation["source_url"] == "https://github.com/httk/httk-serve"
    assert implementation["issue_tracker"] == "https://github.com/httk/httk-serve/issues"


def test_generate_meta_implementation_override() -> None:
    config = OptimadeConfig(implementation={"name": "my-server", "maintainer": {"email": "admin@example.org"}})
    meta = generate_meta(representation="/", api_version="1.3.0", config=config)
    assert meta["implementation"]["name"] == "my-server"
    assert meta["implementation"]["maintainer"] == {"email": "admin@example.org"}


def test_generate_meta_optional_v12_fields() -> None:
    config = OptimadeConfig(
        database={"id": "example_db", "name": "Example"},
        schema_url="https://schemas.optimade.org/openapi/v1.3/optimade.json",
        request_delay=0.1,
    )
    meta = generate_meta(representation="/", api_version="1.3.0", config=config)
    assert meta["database"]["id"] == "example_db"
    assert meta["schema"] == "https://schemas.optimade.org/openapi/v1.3/optimade.json"
    assert meta["request_delay"] == 0.1


def test_generate_meta_optional_fields_absent_by_default() -> None:
    meta = generate_meta(representation="/", api_version="1.3.0", config=OptimadeConfig())
    assert "database" not in meta
    assert "schema" not in meta
    assert "request_delay" not in meta


def test_page_limit_max_defaults_to_50_and_rejects_invalid() -> None:
    assert OptimadeConfig().page_limit_max == 50
    with pytest.raises(ValueError):
        OptimadeConfig(page_limit_max=0)
