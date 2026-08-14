"""Offline JSON Schema registries for OpenAPI applications."""

from collections.abc import Iterable, Mapping
from copy import deepcopy
from types import MappingProxyType
from typing import Any

from jsonschema import FormatChecker, ValidationError
from jsonschema.validators import validator_for
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource
from referencing.jsonschema import DRAFT202012


class OpenAPISchemaError(ValueError):
    """Report an unavailable or invalid JSON Schema document."""


class OpenAPISchemaRegistry:
    """Validate caller-supplied JSON Schema documents without network retrieval.

    The registry deep-copies supplied documents, so later caller mutations do
    not alter validation or offline reference resolution.

    :param documents: JSON Schema documents, each with a non-empty ``$id``.
    """

    def __init__(self, documents: Iterable[Mapping[str, Any]]) -> None:
        copied: dict[str, Mapping[str, Any]] = {}
        resources: list[tuple[str, Resource]] = []
        for source in documents:
            document = deepcopy(dict(source))
            identifier = document.get("$id")
            if not isinstance(identifier, str) or not identifier:
                raise OpenAPISchemaError("JSON Schemas must declare a non-empty $id")
            if identifier in copied:
                raise OpenAPISchemaError(f"duplicate JSON Schema $id: {identifier}")
            try:
                validator_for(document).check_schema(document)
                resource = Resource.from_contents(document, default_specification=DRAFT202012)
            except Exception as error:
                raise OpenAPISchemaError(f"invalid JSON Schema {identifier}: {error}") from error
            copied[identifier] = MappingProxyType(document)
            resources.append((identifier, resource))
        self._documents = MappingProxyType(copied)
        self._registry = Registry().with_resources(resources)

    def lookup(self, identifier: str) -> Mapping[str, Any]:
        """Return a schema document by its canonical identifier.

        :param identifier: Schema ``$id``.
        :return: Independent copy of the registered schema mapping.
        :raises OpenAPISchemaError: If no supplied schema has that identifier.
        """
        try:
            self._registry.contents(identifier)
        except NoSuchResource as error:
            raise OpenAPISchemaError(f"schema is not registered: {identifier}") from error
        return deepcopy(dict(self._documents[identifier]))

    def validate(self, identifier: str, value: Any) -> None:
        """Validate a JSON-compatible value against an offline schema.

        :param identifier: Schema ``$id``.
        :param value: JSON-compatible value to validate.
        :raises OpenAPISchemaError: If the schema is unavailable or validation fails.
        """
        schema = dict(self.lookup(identifier))
        validator_class = validator_for(schema)
        try:
            validator_class.check_schema(schema)
            validator = validator_class(schema, registry=self._registry, format_checker=FormatChecker())
            validator.validate(value)
        except ValidationError as error:
            location = "/".join(str(part) for part in error.absolute_path)
            suffix = f" at {location}" if location else ""
            raise OpenAPISchemaError(f"document does not satisfy {identifier}{suffix}: {error.message}") from error
        except Exception as error:
            if isinstance(error, OpenAPISchemaError):
                raise
            raise OpenAPISchemaError(f"invalid JSON Schema {identifier}: {error}") from error

    @property
    def identifiers(self) -> tuple[str, ...]:
        """Return supplied schema identifiers in caller order."""
        return tuple(self._documents)

    def __iter__(self):
        """Iterate over registered schema identifiers for lightweight inspection."""
        return iter(self._documents)


__all__ = ["OpenAPISchemaError", "OpenAPISchemaRegistry"]
