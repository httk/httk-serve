"""Load and validate the bundled DSP and local profile schemas offline."""

import json
from collections.abc import Mapping
from functools import cache
from importlib.resources import abc, files
from typing import Any

from jsonschema import FormatChecker, ValidationError
from jsonschema.validators import validator_for
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource


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
def schema_registry() -> Registry:
    """Return the immutable registry of bundled JSON Schemas.

    :return: Registry whose resources are resolved exclusively from package
        data.
    """
    resources = []
    for document in _schema_documents():
        identifier = document.get("$id")
        if not isinstance(identifier, str) or not identifier:
            raise DspSchemaError("bundled JSON Schemas must declare a non-empty $id")
        resources.append((identifier, Resource.from_contents(document)))
    return Registry().with_resources(resources)


def schema_document(identifier: str) -> Mapping[str, Any]:
    """Return one bundled schema by its canonical identifier.

    :param identifier: Canonical ``$id`` of the requested schema.
    :return: Parsed schema mapping.
    :raises DspSchemaError: If the identifier is not bundled.
    """
    try:
        contents = schema_registry().contents(identifier)
    except NoSuchResource as exc:
        raise DspSchemaError(f"schema is not bundled: {identifier}") from exc
    if not isinstance(contents, Mapping):
        raise DspSchemaError(f"schema is not an object: {identifier}")
    return contents


def validate_document(identifier: str, document: Any) -> None:
    """Validate a document against a bundled schema.

    :param identifier: Canonical ``$id`` of the target schema.
    :param document: JSON-compatible value to validate.
    :raises DspSchemaError: If the schema is unavailable or validation fails.
    """
    schema = schema_document(identifier)
    schema_dict = dict(schema)
    validator_class = validator_for(schema_dict)
    validator_class.check_schema(schema_dict)
    validator = validator_class(schema_dict, registry=schema_registry(), format_checker=FormatChecker())
    try:
        validator.validate(document)
    except ValidationError as exc:
        location = "/".join(str(part) for part in exc.absolute_path)
        suffix = f" at {location}" if location else ""
        raise DspSchemaError(f"document does not satisfy {identifier}{suffix}: {exc.message}") from exc


__all__ = ["DspSchemaError", "schema_document", "schema_registry", "validate_document"]
