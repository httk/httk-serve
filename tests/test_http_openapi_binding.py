"""Exercise the name-based operation-binding layer for the OpenAPI adapter."""

import asyncio
import functools
from typing import Any

import pytest

from httk.serve.http.openapi import (
    OpenAPIContractError,
    OpenAPIOperation,
    OpenAPIRequest,
    OpenAPIResponse,
    bind_operation,
    convert_result,
    normalize_parameter_name,
    operation,
    parse_openapi_operations,
)

JSON = {"application/json": {"schema": {"$ref": "https://example.test/x"}}}


def build_operation(
    path: str,
    *,
    method: str = "post",
    parameters: list[dict[str, Any]] | None = None,
    body: bool = False,
    bodyless: bool = False,
) -> OpenAPIOperation:
    """Parse a one-operation OpenAPI document and return its single operation."""
    spec: dict[str, Any] = {
        "operationId": "op",
        "responses": {"204": {"description": "ok"}} if bodyless else {"200": {"description": "ok", "content": JSON}},
    }
    if parameters is not None:
        spec["parameters"] = parameters
    if body:
        spec["requestBody"] = {"required": True, "content": JSON}
    document = {"openapi": "3.1.0", "paths": {path: {method: spec}}}
    return parse_openapi_operations(document)[0]


def path_param(name: str) -> dict[str, Any]:
    """Return a required string path parameter contract."""
    return {"name": name, "in": "path", "required": True, "schema": {"type": "string"}}


def query_param(name: str, *, required: bool = False) -> dict[str, Any]:
    """Return a string query parameter contract."""
    return {"name": name, "in": "query", "required": required, "schema": {"type": "string"}}


def header_param(name: str, *, required: bool = False) -> dict[str, Any]:
    """Return a string header parameter contract."""
    return {"name": name, "in": "header", "required": required, "schema": {"type": "string"}}


def make_request(
    op: OpenAPIOperation,
    *,
    path: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    body: Any = None,
) -> OpenAPIRequest:
    """Build a normalized request value for one operation."""
    return OpenAPIRequest(op, path or {}, query or {}, headers or {}, body)


def run(coroutine: Any) -> Any:
    """Drive one coroutine to completion without a pytest asyncio plugin."""
    return asyncio.run(coroutine)


def test_normalize_parameter_name_reproduces_the_verified_examples() -> None:
    """The normalization contract matches every verified wire-name example."""
    assert normalize_parameter_name("providerPid") == "provider_pid"
    assert normalize_parameter_name("X-Request-ID") == "x_request_id"
    assert normalize_parameter_name("HTTPVersion") == "http_version"
    assert normalize_parameter_name("id") == "id"
    assert normalize_parameter_name("view") == "view"
    assert not normalize_parameter_name("filter[name]").isidentifier()


def test_sync_handler_is_bound_and_called() -> None:
    """A plain synchronous handler binds its declared inputs and returns a body."""
    op = build_operation("/items/{item}", parameters=[path_param("item")])

    def handler(item: str) -> dict[str, str]:
        return {"item": item}

    bound = bind_operation(op, handler)
    response = run(bound(make_request(op, path={"item": "one"})))
    assert response.body == {"item": "one"}


def test_async_handler_is_awaited() -> None:
    """An asynchronous handler is awaited before its result is converted."""
    op = build_operation("/items/{item}", parameters=[path_param("item")])

    async def handler(item: str) -> dict[str, str]:
        return {"item": item}

    bound = bind_operation(op, handler)
    response = run(bound(make_request(op, path={"item": "two"})))
    assert response.body == {"item": "two"}


def test_functools_wraps_handler_signature_is_followed() -> None:
    """A handler wrapped with functools.wraps binds against the wrapped signature."""
    op = build_operation("/items/{item}", parameters=[path_param("item")])

    def decorate(function: Any) -> Any:
        @functools.wraps(function)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return function(*args, **kwargs)

        return wrapper

    @decorate
    def handler(item: str) -> dict[str, str]:
        return {"item": item}

    bound = bind_operation(op, handler)
    response = run(bound(make_request(op, path={"item": "wrapped"})))
    assert response.body == {"item": "wrapped"}


def test_convert_result_handles_every_supported_return_shape() -> None:
    """None, mappings, lists, and explicit responses convert; other types raise."""
    assert run(convert_result("op", None)) == OpenAPIResponse()
    assert run(convert_result("op", {"a": 1})).body == {"a": 1}
    assert run(convert_result("op", [1, 2])).body == [1, 2]
    explicit = OpenAPIResponse(201, {"a": 1})
    assert run(convert_result("op", explicit)) is explicit
    with pytest.raises(TypeError, match="op.*unsupported result type"):
        run(convert_result("op", 7))


def test_handler_returning_none_and_bodyless_operation() -> None:
    """A handler returning None yields the bodyless success response."""
    op = build_operation("/files/{path}", method="get", parameters=[path_param("path")], bodyless=True)

    def handler(path: str) -> None:
        return None

    bound = bind_operation(op, handler)
    response = run(bound(make_request(op, path={"path": "gone"})))
    assert response == OpenAPIResponse()


def test_optional_parameters_absent_use_defaults_and_present_bind() -> None:
    """Absent optional query/header values omit the kwarg; present values bind."""
    op = build_operation(
        "/items/{item}",
        parameters=[path_param("item"), query_param("view"), header_param("X-Trace")],
    )

    def handler(item: str, view: str | None = None, x_trace: str | None = None) -> dict[str, Any]:
        return {"item": item, "view": view, "x_trace": x_trace}

    bound = bind_operation(op, handler)
    absent = run(bound(make_request(op, path={"item": "one"})))
    assert absent.body == {"item": "one", "view": None, "x_trace": None}
    present = run(
        bound(make_request(op, path={"item": "one"}, query={"view": "full"}, headers={"x-trace": "trace"}))
    )
    assert present.body == {"item": "one", "view": "full", "x_trace": "trace"}


def test_multiple_path_parameters_bind_by_name_in_opposite_order() -> None:
    """Path parameters declared opposite to the template still bind by name."""
    op = build_operation(
        "/{parent}/{child}",
        method="get",
        parameters=[path_param("child"), path_param("parent")],
    )
    assert tuple(parameter.name for parameter in op.parameters) == ("child", "parent")

    def handler(parent: str, child: str) -> dict[str, str]:
        return {"parent": parent, "child": child}

    bound = bind_operation(op, handler)
    response = run(bound(make_request(op, path={"parent": "P", "child": "C"})))
    assert response.body == {"parent": "P", "child": "C"}


def test_alias_binds_a_wire_name_to_a_renamed_handler_parameter() -> None:
    """An alias maps a wire parameter name onto a different handler parameter."""
    op = build_operation("/datasets/{id}", method="get", parameters=[path_param("id")])

    def handler(dataset_id: str) -> dict[str, str]:
        return {"dataset_id": dataset_id}

    bound = bind_operation(op, operation(handler, aliases={"id": "dataset_id"}))
    response = run(bound(make_request(op, path={"id": "9"})))
    assert response.body == {"dataset_id": "9"}


def test_body_binds_to_the_body_parameter() -> None:
    """A declared request body binds to a handler parameter named body."""
    op = build_operation("/items/{item}", parameters=[path_param("item")], body=True)

    def handler(item: str, *, body: Any) -> dict[str, Any]:
        return {"item": item, "body": body}

    bound = bind_operation(op, handler)
    response = run(bound(make_request(op, path={"item": "one"}, body={"value": "yes"})))
    assert response.body == {"item": "one", "body": {"value": "yes"}}


def test_request_is_injected_by_annotation() -> None:
    """A parameter annotated OpenAPIRequest receives the whole request value."""
    op = build_operation("/ping", method="get", parameters=[query_param("view")])

    def handler(request: OpenAPIRequest) -> dict[str, str]:
        return {"view": request.query["view"]}

    bound = bind_operation(op, handler)
    response = run(bound(make_request(op, query={"view": "whole"})))
    assert response.body == {"view": "whole"}


def test_request_is_injected_by_string_annotation() -> None:
    """A string OpenAPIRequest annotation is resolved and injected."""
    op = build_operation("/ping", method="get", parameters=[query_param("view")])

    def handler(request: "OpenAPIRequest") -> dict[str, str]:
        return {"view": request.query["view"]}

    bound = bind_operation(op, handler)
    response = run(bound(make_request(op, query={"view": "string"})))
    assert response.body == {"view": "string"}


def test_extras_are_validated_and_passed_from_the_request_scope() -> None:
    """A declared extra is validated against scope names and passed at call time."""
    op = build_operation("/items/{item}", parameters=[path_param("item")])

    def handler(item: str, ctx: Any = None) -> dict[str, Any]:
        return {"item": item, "ctx": ctx}

    bound = bind_operation(op, operation(handler, extras=["ctx"]), scope_names=["ctx"])
    response = run(bound(make_request(op, path={"item": "one"}), {"ctx": "scoped"}))
    assert response.body == {"item": "one", "ctx": "scoped"}


class _Base:
    """Base implementation whose method is overridden by a subclass."""

    def op(self, item: str) -> dict[str, str]:
        """Return the base marker."""
        return {"who": "base", "item": item}


class _Sub(_Base):
    """Subclass overriding the bound method."""

    def op(self, item: str) -> dict[str, str]:
        """Return the subclass marker."""
        return {"who": "sub", "item": item}


def test_implementation_resolves_subclass_override() -> None:
    """Resolving a class function against an instance honours subclass overrides."""
    op = build_operation("/items/{item}", parameters=[path_param("item")])
    bound = bind_operation(op, _Base.op, implementation=_Sub())
    response = run(bound(make_request(op, path={"item": "one"})))
    assert response.body == {"who": "sub", "item": "one"}


def test_construction_rejects_unbound_required_parameter() -> None:
    """A required declared parameter with no handler parameter is rejected."""
    op = build_operation("/items/{item}", parameters=[path_param("item")])

    def handler() -> None:
        return None

    with pytest.raises(OpenAPIContractError, match="required path parameter 'item'"):
        bind_operation(op, handler)


def test_construction_rejects_duplicate_normalized_name() -> None:
    """Two parameters normalizing to one handler name demand an alias."""
    op = build_operation("/items/{id}", method="get", parameters=[path_param("id"), query_param("id")])

    def handler(id: str) -> None:
        return None

    with pytest.raises(OpenAPIContractError, match="both bind to handler parameter 'id'"):
        bind_operation(op, handler)


def test_construction_rejects_alias_for_undeclared_parameter() -> None:
    """An alias naming a parameter the operation does not declare is rejected."""
    op = build_operation("/items/{item}", parameters=[path_param("item")])

    def handler(item: str) -> None:
        return None

    with pytest.raises(OpenAPIContractError, match="alias names undeclared parameter 'missing'"):
        bind_operation(op, operation(handler, aliases={"missing": "item"}))


def test_construction_rejects_var_keyword_parameter() -> None:
    """A handler with **kwargs cannot be bound by name."""
    op = build_operation("/items/{item}", parameters=[path_param("item")])

    def handler(**kwargs: Any) -> None:
        return None

    with pytest.raises(OpenAPIContractError, match=r"\*\*kwargs"):
        bind_operation(op, handler)


def test_construction_rejects_positional_only_parameter() -> None:
    """A positional-only handler parameter cannot be bound by name."""
    op = build_operation("/items/{item}", parameters=[path_param("item")])

    def handler(item: str, /) -> None:
        return None

    with pytest.raises(OpenAPIContractError, match="positional-only"):
        bind_operation(op, handler)


def test_construction_rejects_unknown_extra() -> None:
    """An extra outside the available scope names is rejected."""
    op = build_operation("/items/{item}", parameters=[path_param("item")])

    def handler(item: str, ctx: Any = None) -> None:
        return None

    with pytest.raises(OpenAPIContractError, match="extra 'ctx' is not an available request-scope value"):
        bind_operation(op, operation(handler, extras=["ctx"]), scope_names=())


def test_construction_rejects_body_declared_but_not_accepted() -> None:
    """An operation declaring a body needs a handler that accepts body."""
    op = build_operation("/items/{item}", parameters=[path_param("item")], body=True)

    def handler(item: str) -> None:
        return None

    with pytest.raises(OpenAPIContractError, match="declares a request body but the handler does not accept 'body'"):
        bind_operation(op, handler)


def test_construction_rejects_staticmethod_against_implementation() -> None:
    """A staticmethod entry cannot be resolved against an implementation."""
    op = build_operation("/items/{item}", parameters=[path_param("item")])

    class Implementation:
        pass

    with pytest.raises(OpenAPIContractError, match="staticmethod/classmethod"):
        bind_operation(op, staticmethod(lambda item: {"item": item}), implementation=Implementation())
