"""Exercise the public constrained OpenAPI adapter."""

from typing import Any

import pytest
from starlette.testclient import TestClient

from httk.serve.http.openapi import (
    OpenAPIContractError,
    OpenAPIRequest,
    OpenAPIRequestError,
    OpenAPIResponse,
    OpenAPISchemaError,
    OpenAPISchemaRegistry,
    create_openapi_app,
    parse_openapi_operations,
)

SCHEMAS = (
    {
        "$id": "https://example.test/request",
        "type": "object",
        "required": ["value"],
        "properties": {"value": {"type": "string"}},
        "additionalProperties": False,
    },
    {
        "$id": "https://example.test/response",
        "type": "object",
        "required": ["path", "query", "header", "value"],
        "properties": {
            "path": {"type": "string"},
            "query": {"type": "string"},
            "header": {"type": "string"},
            "value": {"type": "string"},
        },
        "additionalProperties": False,
    },
    {"$id": "https://example.test/error", "type": "object", "required": ["detail"]},
)


def document() -> dict[str, Any]:
    """Return a synthetic contract covering the supported public subset."""
    return {
        "openapi": "3.1.0",
        "paths": {
            "/items/{item}": {
                "post": {
                    "operationId": "item",
                    "parameters": [
                        {"name": "item", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "view", "in": "query", "schema": {"type": "string"}},
                        {"name": "X-Trace", "in": "header", "schema": {"type": "string"}},
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "https://example.test/request"}}},
                    },
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {"schema": {"$ref": "https://example.test/response"}},
                                "application/vnd.example+json": {"schema": {"$ref": "https://example.test/response"}},
                            },
                        },
                        "400": {
                            "description": "Bad request",
                            "content": {"application/json": {"schema": {"$ref": "https://example.test/error"}}},
                        },
                    },
                }
            },
            "/files/{path}": {
                "get": {
                    "operationId": "delete",
                    "parameters": [{"name": "path", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"204": {"description": "Deleted"}},
                }
            },
        },
    }


def error_response(error: OpenAPIRequestError) -> OpenAPIResponse:
    """Serialize neutral request errors for the synthetic protocol."""
    return OpenAPIResponse(400, {"detail": error.detail})


def test_schema_registry_owns_documents_and_does_not_expose_them() -> None:
    """Nested caller and lookup mutations cannot alter offline schema validation."""
    source: dict[str, Any] = {
        "$id": "https://example.test/owned",
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }
    registry = OpenAPISchemaRegistry((source,))
    source["properties"]["value"]["type"] = "integer"
    exposed = registry.lookup("https://example.test/owned")
    assert isinstance(exposed, dict)
    exposed["properties"]["value"]["type"] = "integer"
    registry.validate("https://example.test/owned", {"value": "still a string"})
    with pytest.raises(OpenAPISchemaError):
        registry.validate("https://example.test/owned", {"value": 1})


def test_schema_registry_rejects_invalid_schema_at_construction() -> None:
    """Schema configuration errors are reported before an app handles a request."""
    with pytest.raises(OpenAPISchemaError, match="invalid JSON Schema"):
        OpenAPISchemaRegistry(({"$id": "https://example.test/invalid", "type": "not-a-json-schema-type"},))


def test_openapi_app_maps_operations_and_normalizes_request_values() -> None:
    """A handler sees normalized path, query, header, and schema-checked JSON values."""
    seen: list[OpenAPIRequest] = []

    def item(request: OpenAPIRequest) -> OpenAPIResponse:
        seen.append(request)
        return OpenAPIResponse(
            200,
            {
                "path": request.path_params["item"],
                "query": request.query["view"],
                "header": request.header("x-trace") or "",
                "value": request.body["value"],
            },
            media_type="application/vnd.example+json",
        )

    app = create_openapi_app(
        document(),
        {"item": item, "delete": lambda _request: OpenAPIResponse(204)},
        schemas=OpenAPISchemaRegistry(SCHEMAS),
        request_error_handler=error_response,
        path_converters={"path": "path"},
    )
    with TestClient(app) as client:
        response = client.post("/items/one?view=full", headers={"X-Trace": "trace"}, json={"value": "yes"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.example+json"
    assert response.json() == {"path": "one", "query": "full", "header": "trace", "value": "yes"}
    assert seen[0].headers["x-trace"] == "trace"


def test_openapi_app_enforces_required_and_enum_parameter_contracts() -> None:
    """Required query/header values and simple string enums are checked before handlers run."""
    contract = document()
    parameters = contract["paths"]["/items/{item}"]["post"]["parameters"]
    assert isinstance(parameters, list)
    parameters.extend(
        (
            {"name": "mode", "in": "query", "required": True, "schema": {"type": "string", "enum": ["full"]}},
            {"name": "X-Required", "in": "header", "required": True, "schema": {"type": "string"}},
        )
    )

    def item(request: OpenAPIRequest) -> OpenAPIResponse:
        return OpenAPIResponse(
            200,
            {
                "path": request.path_params["item"],
                "query": request.query["view"],
                "header": request.header("x-trace") or "",
                "value": request.body["value"],
            },
            media_type="application/json",
        )

    app = create_openapi_app(
        contract,
        {"item": item, "delete": lambda _request: OpenAPIResponse(204)},
        schemas=OpenAPISchemaRegistry(SCHEMAS),
        request_error_handler=error_response,
        path_converters={"path": "path"},
    )
    with TestClient(app) as client:
        missing = client.post("/items/one?view=one", headers={"X-Required": "yes"}, json={"value": "yes"})
        invalid_enum = client.post(
            "/items/one?view=one&mode=brief",
            headers={"X-Required": "yes"},
            json={"value": "yes"},
        )
        missing_header = client.post("/items/one?view=one&mode=full", json={"value": "yes"})
        accepted = client.post(
            "/items/one?view=one&mode=full",
            headers={"X-Required": "yes"},
            json={"value": "yes"},
        )
    assert missing.status_code == 400
    assert "required query parameter missing: mode" in missing.json()["detail"]
    assert invalid_enum.status_code == 400
    assert "invalid query parameter: mode" in invalid_enum.json()["detail"]
    assert missing_header.status_code == 400
    assert "required header parameter missing: X-Required" in missing_header.json()["detail"]
    assert accepted.status_code == 200


def test_openapi_app_adapts_invalid_request_and_supports_bodyless_and_path_converter() -> None:
    """Adapter validation uses the caller's error response and routing converter."""
    app = create_openapi_app(
        document(),
        {
            "item": lambda _request: OpenAPIResponse(200, {"not": "the contract"}),
            "delete": lambda request: OpenAPIResponse(204, headers={"X-Deleted": request.path_params["path"]}),
        },
        schemas=OpenAPISchemaRegistry(SCHEMAS),
        request_error_handler=error_response,
        path_converters={"path": "path"},
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        invalid = client.post("/items/one", content=b'{"value": 2}', headers={"Content-Type": "application/json"})
        deleted = client.get("/files/a/nested/file")
        invalid_response = client.post("/items/one", json={"value": "yes"})
    assert invalid.status_code == 400
    assert "document does not satisfy" in invalid.json()["detail"]
    assert deleted.status_code == 204
    assert deleted.headers["x-deleted"] == "a/nested/file"
    assert invalid_response.status_code == 500


def test_openapi_app_rejects_incomplete_handlers_and_unsupported_constructs() -> None:
    """Contract errors happen during application construction rather than at request time."""
    registry = OpenAPISchemaRegistry(SCHEMAS)
    with pytest.raises(OpenAPIContractError, match="missing handlers"):
        create_openapi_app(
            document(),
            {"item": lambda _request: OpenAPIResponse(200)},
            schemas=registry,
            request_error_handler=error_response,
        )
    unsupported = document()
    unsupported["paths"]["/items/{item}"]["post"]["callbacks"] = {}
    with pytest.raises(OpenAPIContractError, match="unsupported"):
        create_openapi_app(
            unsupported,
            {"item": lambda _request: OpenAPIResponse(200), "delete": lambda _request: OpenAPIResponse(204)},
            schemas=registry,
            request_error_handler=error_response,
        )


def test_openapi_parser_rejects_parameter_contract_mismatches_and_reference_cycles() -> None:
    """Path declarations, unsupported constraints, and local reference loops fail eagerly."""
    consistent = parse_openapi_operations(document())
    item = next(operation for operation in consistent if operation.operation_id == "item")
    assert item.parameters[0].name == "item"
    assert item.parameters[0].location == "path"
    inconsistent = document()
    parameters = inconsistent["paths"]["/items/{item}"]["post"]["parameters"]
    assert isinstance(parameters, list)
    parameters.pop(0)
    with pytest.raises(OpenAPIContractError, match="exactly match template"):
        parse_openapi_operations(inconsistent)
    for keyword, value in (("format", "uuid"), ("default", "item")):
        unsupported_schema = document()
        unsupported_parameters = unsupported_schema["paths"]["/items/{item}"]["post"]["parameters"]
        assert isinstance(unsupported_parameters, list)
        unsupported_parameters[0]["schema"] = {"type": "string", keyword: value}
        with pytest.raises(OpenAPIContractError, match="simple string schema"):
            parse_openapi_operations(unsupported_schema)
    cycle = document()
    cycle["paths"]["/items/{item}"]["post"] = {"$ref": "#/paths/~1items~1{item}/post"}
    with pytest.raises(OpenAPIContractError, match="cyclic local"):
        parse_openapi_operations(cycle)


@pytest.mark.parametrize(
    "extra",
    [
        {"security": []},
        {"components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}}},
    ],
)
def test_openapi_parser_rejects_unsupported_security(extra: dict[str, object]) -> None:
    """Authentication declarations must never be accepted without enforcement."""
    contract: dict[str, object] = {"openapi": "3.1.0", "paths": {}, **extra}

    with pytest.raises(OpenAPIContractError, match="security"):
        parse_openapi_operations(contract)


def test_openapi_app_preflights_schema_references_and_response_media_types() -> None:
    """Unregistered schemas and non-JSON response bodies are construction errors."""
    registry = OpenAPISchemaRegistry(SCHEMAS)
    handlers = {"item": lambda _request: OpenAPIResponse(200), "delete": lambda _request: OpenAPIResponse(204)}
    missing_request = document()
    missing_request["paths"]["/items/{item}"]["post"]["requestBody"]["content"]["application/json"]["schema"] = {
        "$ref": "https://example.test/missing"
    }
    with pytest.raises(OpenAPIContractError, match="not registered"):
        create_openapi_app(missing_request, handlers, schemas=registry, request_error_handler=error_response)
    missing_response = document()
    missing_response["paths"]["/items/{item}"]["post"]["responses"]["200"]["content"]["application/json"]["schema"] = {
        "$ref": "https://example.test/missing"
    }
    with pytest.raises(OpenAPIContractError, match="not registered"):
        create_openapi_app(missing_response, handlers, schemas=registry, request_error_handler=error_response)
    non_json = document()
    content = non_json["paths"]["/items/{item}"]["post"]["responses"]["200"]["content"]
    assert isinstance(content, dict)
    content["text/plain"] = content.pop("application/json")
    with pytest.raises(OpenAPIContractError, match="only JSON response media types"):
        create_openapi_app(non_json, handlers, schemas=registry, request_error_handler=error_response)
    malformed_parameters = document()
    malformed_parameters["paths"]["/items/{item}"]["post"]["parameters"] = None
    with pytest.raises(OpenAPIContractError, match="parameters"):
        create_openapi_app(
            malformed_parameters,
            {"item": lambda _request: OpenAPIResponse(200), "delete": lambda _request: OpenAPIResponse(204)},
            schemas=registry,
            request_error_handler=error_response,
        )
