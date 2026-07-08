"""Generation of OPTIMADE Property Definitions (v1.2+) from the schema data.

The entry listing info endpoints must present each property as an OPTIMADE
Property Definition. These are generated mechanically from the property
information in a served schema; standard OPTIMADE properties reference their
official definition URIs at schemas.optimade.org.
"""

from typing import Any

from .entries import EntryInfo, PropertyInfo

RECOGNIZED_PREFIXES: tuple[str, ...] = ('_httk_', '_omdb_')

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
    if optimade_type == "timestamp":
        definition["format"] = "date-time"
    return definition


def _slice_object_definition() -> dict[str, Any]:
    """A modest definition of a "slice object" (start/stop/step)."""
    integer_field = {
        "x-optimade-type": "integer",
        "x-optimade-unit": "inapplicable",
        "type": ["integer", "null"],
    }
    return {
        "x-optimade-type": "dictionary",
        "x-optimade-unit": "inapplicable",
        "type": ["object", "null"],
        "properties": {
            "start": dict(integer_field),
            "stop": dict(integer_field),
            "step": dict(integer_field),
        },
    }


def _generated_metadata_definition(name: str) -> dict[str, Any]:
    """A standard ``x-optimade-metadata-definition`` describing ``list_axes``.

    Generated for properties that declare dimensions but do not carry an
    explicit metadata definition. It describes the ``list_axes`` metadata field
    used by the slicing protocol (see the OPTIMADE specification section
    "Slices of list properties").
    """
    return {
        "title": f"Metadata for the {name} field",
        "description": f"This field contains the per-entry metadata for the {name} field.",
        "x-optimade-type": "dictionary",
        "x-optimade-unit": "inapplicable",
        "type": ["object", "null"],
        "properties": {
            "list_axes": {
                "title": "List axes",
                "description": (
                    "Descriptive information related to the axes of this list property, including "
                    "sliceable axes. Each item, in order, represents a list axis as declared in the "
                    "property definition."
                ),
                "x-optimade-type": "list",
                "x-optimade-unit": "inapplicable",
                "type": ["array", "null"],
                "items": {
                    "x-optimade-type": "dictionary",
                    "x-optimade-unit": "inapplicable",
                    "type": ["object", "null"],
                    "properties": {
                        "dimension_name": {
                            "x-optimade-type": "string",
                            "x-optimade-unit": "inapplicable",
                            "type": ["string"],
                        },
                        "requested_slice": _slice_object_definition(),
                        "length": {
                            "x-optimade-type": "integer",
                            "x-optimade-unit": "inapplicable",
                            "type": ["integer", "null"],
                        },
                        "sliceable": {
                            "x-optimade-type": "boolean",
                            "x-optimade-unit": "inapplicable",
                            "type": ["boolean", "null"],
                        },
                        "available_slice": _slice_object_definition(),
                    },
                },
            },
        },
    }


def property_definition(entry: str, name: str, info: PropertyInfo) -> dict[str, Any]:
    """Build an OPTIMADE Property Definition for one property."""
    fulltype = info.get("fulltype", "string")
    optimade_type = _optimade_type(fulltype)
    unit = info.get("unit", "dimensionless")
    nullable = not info.get("required_response", False)

    if name.startswith(RECOGNIZED_PREFIXES):
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
        "sortable": info.get("sortable", False),
        "x-optimade-implementation": {
            "sortable": info.get("sortable", False),
            "response-default": info.get("default_response", False),
        },
    }
    if optimade_type == "list":
        definition["items"] = _inner_definition(fulltype[len("list of ") :], unit)
    if optimade_type == "timestamp":
        definition["format"] = "date-time"
    if optimade_type == "dictionary":
        dict_properties = info.get("dict_properties", {})
        definition["properties"] = {
            key: _inner_definition(inner_fulltype, "dimensionless") for key, inner_fulltype in dict_properties.items()
        }
    if unit == "angstrom":
        definition["x-optimade-unit-definitions"] = [_ANGSTROM_UNIT_DEFINITION]

    dimensions = info.get("dimensions")
    if dimensions is not None:
        x_dimensions: dict[str, Any] = {"names": dimensions["names"], "sizes": dimensions["sizes"]}
        if "compactable" in dimensions:
            x_dimensions["compactable"] = dimensions["compactable"]
        definition["x-optimade-dimensions"] = x_dimensions

    metadata_definition = info.get("metadata_definition")
    if metadata_definition is not None:
        definition["x-optimade-metadata-definition"] = metadata_definition
    elif dimensions is not None:
        definition["x-optimade-metadata-definition"] = _generated_metadata_definition(name)

    return definition


def entry_property_definitions(entry: str, entry_info: dict[str, EntryInfo]) -> dict[str, dict[str, Any]]:
    """Property Definitions for all properties served for an entry type."""
    return {name: property_definition(entry, name, info) for name, info in entry_info[entry]["properties"].items()}
