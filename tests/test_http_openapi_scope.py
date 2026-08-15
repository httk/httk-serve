"""Exercise the per-request scope hook of the constrained OpenAPI adapter."""

from contextlib import asynccontextmanager
from typing import Any

import pytest
from starlette.background import BackgroundTask
from starlette.testclient import TestClient

from httk.serve.http.openapi import (
    OpenAPIContractError,
    OpenAPIRequest,
    OpenAPIRequestError,
    OpenAPIResponse,
    OpenAPISchemaRegistry,
    OperationContext,
    create_openapi_app,
    operation,
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
    """Return a contract whose 200 declares two media types and 400 declares one."""
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


def response_body(item: str, view: str | None, x_trace: str | None, body: Any) -> dict[str, str]:
    """Build a body satisfying the synthetic response schema."""
    return {"path": item, "query": view or "", "header": x_trace or "", "value": body["value"]}


def build_app(
    item_entry: Any,
    *,
    request_scope: Any = None,
    scope_names: Any = (),
    exception_handlers: Any = None,
    delete_entry: Any = None,
) -> Any:
    """Create an app from the two-operation contract with a supplied item entry."""
    return create_openapi_app(
        document(),
        {"item": item_entry, "delete": delete_entry or (lambda path: OpenAPIResponse(204))},
        schemas=OpenAPISchemaRegistry(SCHEMAS),
        request_error_handler=error_response,
        request_scope=request_scope,
        scope_names=scope_names,
        exception_handlers=exception_handlers,
        path_converters={"path": "path"},
    )


class Boom(Exception):
    """A synthetic protocol exception adapted to a 400 response."""


def adapt_boom(error: Exception, request: OpenAPIRequest) -> OpenAPIResponse:
    """Convert a raised protocol exception to a neutral 400 response."""
    return OpenAPIResponse(400, {"detail": "boom"})


def test_scope_extra_reaches_only_the_declaring_handler() -> None:
    """A scope extra binds to the operation that declares it and to no other."""
    seen: dict[str, Any] = {}

    def item(item: str, batch: Any, view: str | None = None, x_trace: str | None = None, *, body: Any) -> Any:
        seen["batch"] = batch
        return response_body(item, view, x_trace, body)

    def delete(path: str) -> OpenAPIResponse:
        seen["delete_ran"] = True
        return OpenAPIResponse(204)

    @asynccontextmanager
    async def scope(request: OpenAPIRequest) -> Any:
        context = OperationContext(extras={"batch": {"id": request.operation.operation_id}})
        yield context
        if request.operation.operation_id == "item":
            context.media_type = "application/json"

    app = build_app(
        operation(item, extras=["batch"]),
        request_scope=scope,
        scope_names=["batch"],
        delete_entry=delete,
    )
    with TestClient(app) as client:
        response = client.post("/items/one", json={"value": "yes"})
        deleted = client.get("/files/gone")
    assert response.status_code == 200
    assert seen["batch"] == {"id": "item"}
    assert deleted.status_code == 204
    assert seen["delete_ran"] is True


def test_scope_media_type_and_headers_land_on_a_successful_response() -> None:
    """The scope resolves an ambiguous media type and contributes response headers."""

    def item(item: str, view: str | None = None, x_trace: str | None = None, *, body: Any) -> dict[str, str]:
        return response_body(item, view, x_trace, body)

    @asynccontextmanager
    async def scope(request: OpenAPIRequest) -> Any:
        context = OperationContext(extras={})
        yield context
        context.media_type = "application/vnd.example+json"
        context.headers["X-Scope"] = "yes"

    app = build_app(item, request_scope=scope)
    with TestClient(app) as client:
        response = client.post("/items/one", json={"value": "yes"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.example+json"
    assert response.headers["x-scope"] == "yes"


def test_scope_background_runs_after_the_handler_completed() -> None:
    """A scope background task runs after the handler produced its response body."""
    order: list[str] = []

    def item(item: str, view: str | None = None, x_trace: str | None = None, *, body: Any) -> dict[str, str]:
        order.append("handler")
        return response_body(item, view, x_trace, body)

    @asynccontextmanager
    async def scope(request: OpenAPIRequest) -> Any:
        context = OperationContext(extras={})
        yield context
        context.media_type = "application/json"
        context.background = BackgroundTask(lambda: order.append("background"))

    app = build_app(item, request_scope=scope)
    with TestClient(app) as client:
        response = client.post("/items/one", json={"value": "yes"})
    assert response.status_code == 200
    assert order == ["handler", "background"]


def test_scope_metadata_never_reaches_an_error_response() -> None:
    """A success-only media type set by the scope must not corrupt an error response."""

    def item(item: str, *, body: Any) -> dict[str, str]:
        raise Boom

    @asynccontextmanager
    async def scope(request: OpenAPIRequest) -> Any:
        context = OperationContext(extras={})
        context.media_type = "application/vnd.example+json"
        context.headers["X-Scope"] = "leaked"
        yield context

    app = build_app(item, request_scope=scope, exception_handlers={Boom: adapt_boom})
    with TestClient(app) as client:
        response = client.post("/items/one", json={"value": "yes"})
    assert response.status_code == 400
    assert response.json() == {"detail": "boom"}
    assert response.headers["content-type"] == "application/json"
    assert "x-scope" not in response.headers


def test_scope_post_yield_code_is_skipped_when_the_handler_raises() -> None:
    """Deferred post-yield scope work does not run when an exception is thrown in."""
    markers: list[str] = []

    def item(item: str, *, body: Any) -> dict[str, str]:
        raise Boom

    @asynccontextmanager
    async def scope(request: OpenAPIRequest) -> Any:
        markers.append("enter")
        yield OperationContext(extras={})
        markers.append("post")

    app = build_app(item, request_scope=scope, exception_handlers={Boom: adapt_boom})
    with TestClient(app) as client:
        response = client.post("/items/one", json={"value": "yes"})
    assert response.status_code == 400
    assert markers == ["enter"]


def test_explicit_response_overrides_scope_and_merges_headers() -> None:
    """A returned response wins on media type and per-key headers; the scope fills gaps."""

    def item(item: str, view: str | None = None, x_trace: str | None = None, *, body: Any) -> OpenAPIResponse:
        return OpenAPIResponse(
            200,
            response_body(item, view, x_trace, body),
            media_type="application/json",
            headers={"X-Shared": "handler", "X-Handler": "h"},
        )

    @asynccontextmanager
    async def scope(request: OpenAPIRequest) -> Any:
        context = OperationContext(extras={})
        yield context
        context.media_type = "application/vnd.example+json"
        context.headers["X-Shared"] = "scope"
        context.headers["X-Scope"] = "s"

    app = build_app(item, request_scope=scope)
    with TestClient(app) as client:
        response = client.post("/items/one", json={"value": "yes"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["x-shared"] == "handler"
    assert response.headers["x-handler"] == "h"
    assert response.headers["x-scope"] == "s"


def test_no_scope_leaves_behaviour_unchanged() -> None:
    """With no request scope, a plain handler behaves exactly as without the hook."""

    def item(item: str, view: str | None = None, x_trace: str | None = None, *, body: Any) -> OpenAPIResponse:
        return OpenAPIResponse(200, response_body(item, view, x_trace, body), media_type="application/json")

    app = build_app(item)
    with TestClient(app) as client:
        response = client.post("/items/one?view=full", headers={"X-Trace": "t"}, json={"value": "yes"})
    assert response.status_code == 200
    assert response.json() == {"path": "one", "query": "full", "header": "t", "value": "yes"}


def test_construction_rejects_extra_outside_scope_names() -> None:
    """A declared extra absent from the available scope names fails at construction."""

    def item(item: str, nope: Any = None, *, body: Any) -> dict[str, str]:
        return response_body(item, None, None, body)

    @asynccontextmanager
    async def scope(request: OpenAPIRequest) -> Any:
        yield OperationContext(extras={})

    with pytest.raises(OpenAPIContractError, match="extra 'nope' is not an available request-scope value"):
        build_app(operation(item, extras=["nope"]), request_scope=scope, scope_names=["real"])
