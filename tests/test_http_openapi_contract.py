"""Exercise the OpenAPIContract value object bundling a contract and its schemas."""

import json
from typing import Any

import pytest

from httk.serve.http.openapi import (
    OpenAPIContract,
    OpenAPIContractError,
    OpenAPISchemaRegistry,
    create_openapi_app,
)
from httk.serve.http.openapi.app import OpenAPIResponse

PACKAGE = "httk.serve.dsp"


def test_from_package_builds_operations_and_schemas() -> None:
    """A package with a bundled contract and schemas parses into both parts."""
    contract = OpenAPIContract.from_package(PACKAGE)
    assert len(contract.operations) == 15
    assert len(contract.schemas.identifiers) == 27


def test_document_returns_an_independent_deep_copy() -> None:
    """Mutating a returned document must not affect later calls."""
    contract = OpenAPIContract.from_package(PACKAGE)
    first = contract.document()
    first["paths"].clear()
    second = contract.document()
    assert second["paths"]
    assert len(second["paths"]) == 15


def test_from_package_is_cached_for_identical_arguments() -> None:
    """Repeated calls with the same arguments must return the same object."""
    first = OpenAPIContract.from_package(PACKAGE)
    second = OpenAPIContract.from_package(PACKAGE)
    assert first is second


def test_schema_transform_applies_only_to_schema_documents() -> None:
    """schema_transform touches bundled schema documents, never the OpenAPI document."""

    def mark(document: dict[str, Any]) -> dict[str, Any]:
        return {**document, "x-marked": True}

    contract = OpenAPIContract.from_package(PACKAGE, schema_transform=mark)
    for identifier in contract.schemas.identifiers:
        assert contract.schemas.lookup(identifier)["x-marked"] is True
    assert "x-marked" not in contract.document()


def test_schema_transform_fixes_the_pinned_upstream_reference_typo() -> None:
    """The real DSP typo-fix transform must resolve through the registry."""

    def fix(document: dict[str, Any]) -> dict[str, Any]:
        text = json.dumps(document).replace(".json#definitions/", ".json#/definitions/")
        return json.loads(text)

    contract = OpenAPIContract.from_package(PACKAGE, schema_transform=fix)
    raw = contract.schemas.lookup("https://w3id.org/dspace/2025/1/transfer/transfer-error-schema.json")
    assert ".json#definitions/" not in json.dumps(raw)


def test_operation_returns_known_operation_and_raises_for_unknown_id() -> None:
    """operation() looks up by id and raises OpenAPIContractError otherwise."""
    contract = OpenAPIContract.from_package(PACKAGE)
    operation = contract.operation("version_discovery")
    assert operation.operation_id == "version_discovery"
    with pytest.raises(OpenAPIContractError, match="unknown OpenAPI operation id"):
        contract.operation("not-a-real-operation")


def test_create_openapi_app_rejects_contract_with_schemas() -> None:
    """Passing schemas alongside a contract is contradictory and rejected."""
    contract = OpenAPIContract.from_package(PACKAGE)

    async def handler(
        id: str | None = None, provider_pid: str | None = None, body: Any = None
    ) -> OpenAPIResponse:
        raise AssertionError("handler should not run")

    def error_response(error: Any) -> OpenAPIResponse:
        raise AssertionError("error handler should not run")

    handlers = {operation.operation_id: handler for operation in contract.operations}
    with pytest.raises(OpenAPIContractError, match="schemas must not be supplied"):
        create_openapi_app(contract, handlers, schemas=contract.schemas, request_error_handler=error_response)


def test_create_openapi_app_requires_schemas_for_a_plain_mapping() -> None:
    """A bare OpenAPI mapping without an explicit schemas registry is rejected."""
    contract = OpenAPIContract.from_package(PACKAGE)
    document = contract.document()

    async def handler(
        id: str | None = None, provider_pid: str | None = None, body: Any = None
    ) -> OpenAPIResponse:
        raise AssertionError("handler should not run")

    def error_response(error: Any) -> OpenAPIResponse:
        raise AssertionError("error handler should not run")

    handlers = {operation.operation_id: handler for operation in contract.operations}
    with pytest.raises(OpenAPIContractError, match="schemas is required"):
        create_openapi_app(document, handlers, request_error_handler=error_response)


def test_create_openapi_app_accepts_a_plain_mapping_with_schemas() -> None:
    """The pre-existing document + schemas calling convention keeps working."""
    contract = OpenAPIContract.from_package(PACKAGE)
    document = contract.document()
    schemas: OpenAPISchemaRegistry = contract.schemas

    async def handler(
        id: str | None = None, provider_pid: str | None = None, body: Any = None
    ) -> OpenAPIResponse:
        raise AssertionError("handler should not run")

    def error_response(error: Any) -> OpenAPIResponse:
        raise AssertionError("error handler should not run")

    handlers = {operation.operation_id: handler for operation in contract.operations}
    app = create_openapi_app(document, handlers, schemas=schemas, request_error_handler=error_response)
    assert app is not None
