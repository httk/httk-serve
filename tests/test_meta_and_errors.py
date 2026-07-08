from httk.optimade.endpoints.error import format_optimade_error
from httk.optimade.endpoints.meta import generate_meta
from httk.optimade.model import OptimadeConfig, OptimadeError, RawRequest


def test_generate_meta_basic() -> None:
    config = OptimadeConfig()
    meta = generate_meta(representation="/structures", api_version="1.3.0", config=config)
    assert meta["query"]["representation"] == "/structures"
    assert meta["api_version"] == "1.3.0"
    assert meta["implementation"]["name"] == "httk"
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
        data_count=7,
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
    assert output.json_response["meta"]["data_returned"] == 1


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
