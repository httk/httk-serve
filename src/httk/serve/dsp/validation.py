"""Load and validate the bundled DSP and local profile schemas offline."""

import json
from collections.abc import Mapping
from functools import cache
from importlib.resources import abc, files
from typing import Any

from httk.serve.openapi import OpenAPISchemaError, OpenAPISchemaRegistry


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


def _walk(resource: abc.Traversable) -> tuple[abc.Traversable, ...]:
    """Return files below an importlib resource in stable order."""
    found: list[abc.Traversable] = []
    for child in sorted(resource.iterdir(), key=lambda item: item.name):
        if child.is_dir():
            found.extend(_walk(child))
        else:
            found.append(child)
    return tuple(found)


def _schema_documents() -> tuple[dict[str, Any], ...]:
    """Load every bundled JSON Schema document without network access."""
    root = files("httk.serve.dsp").joinpath("schemas")
    documents: list[dict[str, Any]] = []
    for resource in _walk(root):
        if not resource.name.endswith(".json"):
            continue
        document = _normalized_references(json.loads(resource.read_text(encoding="utf-8")))
        if isinstance(document, dict) and "$schema" in document:
            documents.append(document)
    return tuple(documents)


@cache
def schema_registry() -> OpenAPISchemaRegistry:
    """Return the immutable registry of bundled JSON Schemas.

    :return: Registry whose resources are resolved exclusively from package
        data.
    """
    try:
        return OpenAPISchemaRegistry(_schema_documents())
    except OpenAPISchemaError as error:
        raise DspSchemaError(str(error)) from error


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


__all__ = ["DspSchemaError", "schema_document", "schema_registry", "validate_document"]
