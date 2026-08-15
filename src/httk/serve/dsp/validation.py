"""Load and validate the bundled DSP and local profile schemas offline."""

from collections.abc import Mapping
from typing import Any

from httk.serve.http.openapi import OpenAPIContract, OpenAPISchemaError, OpenAPISchemaRegistry

_CONTRACT_PACKAGE = "httk.serve.dsp"


class DspSchemaError(ValueError):
    """Report a DSP schema-validation failure."""


def _normalized_references(value: Any) -> Any:
    """Correct the three pinned upstream JSON-Pointer fragment typos in memory."""
    if isinstance(value, dict):
        return {key: _normalized_references(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_normalized_references(child) for child in value]
    if isinstance(value, str) and ".json#definitions/" in value:
        return value.replace(".json#definitions/", ".json#/definitions/")
    return value


def dsp_contract() -> OpenAPIContract:
    """Return the cached bundled DSP OpenAPI contract and offline schema registry.

    :return: Contract parsed exclusively from package data.
    :raises DspSchemaError: If any bundled schema document is invalid.
    """
    try:
        return OpenAPIContract.from_package(_CONTRACT_PACKAGE, schema_transform=_normalized_references)
    except OpenAPISchemaError as error:
        raise DspSchemaError(str(error)) from error


def schema_registry() -> OpenAPISchemaRegistry:
    """Return the immutable registry of bundled JSON Schemas.

    :return: Registry whose resources are resolved exclusively from package
        data.
    """
    return dsp_contract().schemas


def schema_document(identifier: str) -> Mapping[str, Any]:
    """Return one bundled schema by its canonical identifier.

    :param identifier: Canonical ``$id`` of the requested schema.
    :return: Parsed schema mapping.
    :raises DspSchemaError: If the identifier is not bundled.
    """
    try:
        return schema_registry().lookup(identifier)
    except OpenAPISchemaError as exc:
        raise DspSchemaError(f"schema is not bundled: {identifier}") from exc


def validate_document(identifier: str, document: Any) -> None:
    """Validate a document against a bundled schema.

    :param identifier: Canonical ``$id`` of the target schema.
    :param document: JSON-compatible value to validate.
    :raises DspSchemaError: If the schema is unavailable or validation fails.
    """
    try:
        schema_registry().validate(identifier, document)
    except OpenAPISchemaError as exc:
        raise DspSchemaError(str(exc)) from exc


__all__ = ["DspSchemaError", "dsp_contract", "schema_document", "schema_registry", "validate_document"]
