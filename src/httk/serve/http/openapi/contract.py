"""Bundle a parsed OpenAPI contract with its offline schema registry."""

from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Self, cast

from .app import OpenAPIContractError, OpenAPIOperation, parse_openapi_operations
from .resources import load_packaged_contract, packaged_schema_documents
from .schemas import OpenAPISchemaRegistry


@dataclass(frozen=True, slots=True)
class OpenAPIContract:
    """Bundle a parsed OpenAPI contract with its offline JSON Schema registry.

    :param operations: Supported operations in document order.
    :param schemas: Offline schema registry for external body references.
    """

    operations: tuple[OpenAPIOperation, ...]
    schemas: OpenAPISchemaRegistry
    _document: dict[str, Any] = field(repr=False)

    @classmethod
    def from_package(
        cls,
        package: str,
        *,
        contract: Sequence[str] = ("schemas", "openapi.yaml"),
        schemas: Sequence[str] = ("schemas",),
        schema_transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> Self:
        """Load and parse a packaged OpenAPI contract and its bundled schemas.

        Results are cached by the exact ``package``, ``contract``, ``schemas``,
        and ``schema_transform`` arguments, so repeated calls with the same
        arguments do not re-parse or re-validate the packaged data.

        :param package: Importable package that ships the contract as package data.
        :param contract: Path segments below the package to the OpenAPI document.
        :param schemas: Path segments below the package to the schema root.
        :param schema_transform: Optional per-document transform applied to each
            bundled JSON Schema document before it is registered. It is not
            applied to the OpenAPI document itself.
        :return: The parsed contract and its offline schema registry.
        :raises httk.serve.http.openapi.OpenAPIContractError: If the OpenAPI
            document uses an unsupported construct.
        :raises httk.serve.http.openapi.OpenAPISchemaError: If any bundled
            schema document is invalid.
        """
        # The cached builder always returns a plain OpenAPIContract; this frozen,
        # slotted value type is not meant to be subclassed, so the cast is safe.
        return cast(Self, _build(package, tuple(contract), tuple(schemas), schema_transform))

    def document(self) -> dict[str, Any]:
        """Return an independent deep copy of the parsed OpenAPI document.

        :return: Caller-owned copy of the OpenAPI document mapping.
        """
        return deepcopy(self._document)

    def operation(self, operation_id: str) -> OpenAPIOperation:
        """Return the operation registered under an operation id.

        :param operation_id: Operation identifier to look up.
        :return: The matching operation.
        :raises httk.serve.http.openapi.OpenAPIContractError: If no operation
            has that id.
        """
        for operation in self.operations:
            if operation.operation_id == operation_id:
                return operation
        known = sorted(operation.operation_id for operation in self.operations)
        raise OpenAPIContractError(f"unknown OpenAPI operation id: {operation_id} (known: {known})")

    def validate(self, schema_id: str, document: Any) -> None:
        """Validate a JSON-compatible value against a bundled schema.

        :param schema_id: Schema ``$id``.
        :param document: JSON-compatible value to validate.
        :raises httk.serve.http.openapi.OpenAPISchemaError: If the schema is
            unavailable or validation fails.
        """
        self.schemas.validate(schema_id, document)


@cache
def _build(
    package: str,
    contract: tuple[str, ...],
    schemas: tuple[str, ...],
    schema_transform: Callable[[dict[str, Any]], dict[str, Any]] | None,
) -> OpenAPIContract:
    """Load, parse, and validate a packaged contract once per distinct arguments."""
    document = load_packaged_contract(package, *contract)
    operations = parse_openapi_operations(document)
    documents = packaged_schema_documents(package, *schemas)
    if schema_transform is not None:
        documents = tuple(schema_transform(schema_document) for schema_document in documents)
    registry = OpenAPISchemaRegistry(documents)
    return OpenAPIContract(operations, registry, document)


__all__ = ["OpenAPIContract"]
