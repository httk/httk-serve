"""Load packaged OpenAPI contracts and bundled JSON Schema documents offline."""

import json
from importlib.resources import abc, files
from typing import Any

import yaml

from .schemas import OpenAPISchemaRegistry


def _walk(resource: abc.Traversable) -> tuple[abc.Traversable, ...]:
    """Return files below an importlib resource in stable order."""
    found: list[abc.Traversable] = []
    for child in sorted(resource.iterdir(), key=lambda item: item.name):
        if child.is_dir():
            found.extend(_walk(child))
        else:
            found.append(child)
    return tuple(found)


def load_packaged_contract(package: str, *path: str) -> dict[str, Any]:
    """Load an OpenAPI contract from package data without network access.

    The resource is read as UTF-8 and parsed according to its suffix:
    ``.yaml`` and ``.yml`` through ``yaml.safe_load``, ``.json`` through
    :func:`json.loads`. Both formats yield the same mapping shape that
    :func:`httk.serve.openapi.create_openapi_app` consumes.

    :param package: Importable package that ships the contract as package data.
    :param \\*path: Path segments joined below the package to reach the resource.
    :return: Parsed contract mapping.
    :raises ValueError: If the resource suffix is not a supported contract format.
    :raises RuntimeError: If the parsed contract is not a JSON object.
    """
    resource = files(package).joinpath(*path)
    text = resource.read_text(encoding="utf-8")
    if resource.name.endswith((".yaml", ".yml")):
        document = yaml.safe_load(text)
    elif resource.name.endswith(".json"):
        document = json.loads(text)
    else:
        raise ValueError(f"unsupported contract format: {resource.name}")
    if not isinstance(document, dict):
        raise RuntimeError("bundled OpenAPI document must be an object")
    return document


def packaged_schema_documents(package: str, *path: str) -> tuple[dict[str, Any], ...]:
    """Load every bundled JSON Schema document below a package resource.

    The resource tree is walked in stable sorted order; every ``*.json`` file
    is parsed, and those that decode to a mapping carrying a ``$schema`` key are
    returned unchanged.

    :param package: Importable package that ships the schema documents.
    :param \\*path: Path segments joined below the package to reach the schema root.
    :return: Parsed JSON Schema documents in stable order.
    """
    root = files(package).joinpath(*path)
    documents: list[dict[str, Any]] = []
    for resource in _walk(root):
        if not resource.name.endswith(".json"):
            continue
        document = json.loads(resource.read_text(encoding="utf-8"))
        if isinstance(document, dict) and "$schema" in document:
            documents.append(document)
    return tuple(documents)


def packaged_schema_registry(package: str, *path: str) -> OpenAPISchemaRegistry:
    """Build an offline schema registry from bundled JSON Schema documents.

    :param package: Importable package that ships the schema documents.
    :param \\*path: Path segments joined below the package to reach the schema root.
    :return: Registry over the bundled documents.
    :raises httk.serve.openapi.OpenAPISchemaError: If any document is invalid.
    """
    return OpenAPISchemaRegistry(packaged_schema_documents(package, *path))


__all__ = ["load_packaged_contract", "packaged_schema_documents", "packaged_schema_registry"]
