"""Generation of OPTIMADE Property Definitions (v1.2+) from the schema data.

The entry listing info endpoints must present each property as an OPTIMADE
Property Definition. These are generated mechanically from the property
information in :mod:`httk.optimade.schema.httk_entries`; standard OPTIMADE
properties reference their official definition URIs at schemas.optimade.org.
"""

from functools import lru_cache
from typing import Any

from .entries import PropertyInfo
from .httk_entries import httk_entry_info, httk_recognized_prefixes

PROPERTY_DEFINITION_META_SCHEMA = "https://schemas.optimade.org/meta/v1.2/optimade/property_definition.json"

_OPTIMADE_DEFS_BASE = "https://schemas.optimade.org/defs/v1.2/properties/optimade"
_HTTK_DEFS_BASE = "https://httk.org/optimade/defs/properties"

_ANGSTROM_UNIT_DEFINITION = {
    "symbol": "angstrom",
    "title": "ångström",
    "description": "The ångström unit of length.",
    "standard": {
        "kind": "gnu units",
        "version": "3.15",
        "symbol": "angstrom",
    },
}

_JSON_TYPE_BY_OPTIMADE_TYPE = {
    "boolean": "boolean",
    "string": "string",
    "integer": "integer",
    "float": "number",
    "list": "array",
    "dictionary": "object",
    "timestamp": "string",
}


def _optimade_type(fulltype: str) -> str:
    if fulltype.startswith("list of "):
        return "list"
    if fulltype == "dict":
        return "dictionary"
    return fulltype


def _type_field(optimade_type: str, nullable: bool) -> list[str]:
    json_type = _JSON_TYPE_BY_OPTIMADE_TYPE[optimade_type]
    return [json_type, "null"] if nullable else [json_type]


def _inner_definition(fulltype: str, unit: str) -> dict[str, Any]:
    optimade_type = _optimade_type(fulltype)
    definition: dict[str, Any] = {
        "x-optimade-type": optimade_type,
        "x-optimade-unit": unit if optimade_type in ("integer", "float", "list") else "dimensionless",
        "type": _type_field(optimade_type, nullable=True),
    }
    if optimade_type == "list":
        definition["items"] = _inner_definition(fulltype[len("list of ") :], unit)
    return definition


def property_definition(entry: str, name: str, info: PropertyInfo) -> dict[str, Any]:
    """Build an OPTIMADE Property Definition for one property."""
    fulltype = info.get("fulltype", "string")
    optimade_type = _optimade_type(fulltype)
    unit = info.get("unit", "dimensionless")
    nullable = not info.get("required_response", False)

    if name.startswith(httk_recognized_prefixes):
        definition_id = f"{_HTTK_DEFS_BASE}/{entry}/{name}"
        source = "httk"
    else:
        definition_id = f"{_OPTIMADE_DEFS_BASE}/{entry}/{name}"
        source = "optimade"

    definition: dict[str, Any] = {
        "$schema": PROPERTY_DEFINITION_META_SCHEMA,
        "$id": definition_id,
        "title": name.replace("_", " ").strip().capitalize(),
        "description": info.get("description", ""),
        "x-optimade-type": optimade_type,
        "x-optimade-unit": unit,
        "x-optimade-definition": {
            "kind": "property",
            "format": "1.2",
            "name": name,
            "label": f"{name.lstrip('_')}_{source}_{entry}",
        },
        "type": _type_field(optimade_type, nullable),
        "x-optimade-implementation": {
            "sortable": info.get("sortable", False),
            "response-default": info.get("default_response", False),
        },
    }
    if optimade_type == "list":
        definition["items"] = _inner_definition(fulltype[len("list of ") :], unit)
    if unit == "angstrom":
        definition["x-optimade-unit-definitions"] = [_ANGSTROM_UNIT_DEFINITION]
    return definition


@lru_cache(maxsize=None)
def entry_property_definitions(entry: str) -> dict[str, dict[str, Any]]:
    """Property Definitions for all properties served for an entry type."""
    return {name: property_definition(entry, name, info) for name, info in httk_entry_info[entry]["properties"].items()}
