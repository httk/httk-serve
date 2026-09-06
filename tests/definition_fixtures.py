"""Synthetic and vendored OPTIMADE entry-type definitions for the test suite.

httk-serve is a generic OPTIMADE implementation: entry-type definitions are
supplied from outside (in practice by an :class:`httk.core.EntryProvider`). The
tests need realistic definitions to exercise the engine end to end, so this
module builds them:

- ``structures`` and ``widgets`` are synthesized with
  :meth:`httk.core.PropertyDefinition.from_simple` (httk-atomistic, which vendors
  the real ``structures`` standard, is not a dependency of httk-serve).
- ``references``, ``files``, and ``calculations`` use the real vendored standards
  from httk-core (``calculations`` extended with two ``_httk_`` custom properties).

``served_schema`` resolves the right definition for each served entry type and
builds a :class:`~httk.serve.optimade.schema.served.ServedSchema`.
"""

from typing import Any, Mapping, Sequence

from httk.core import EntryTypeDefinition, PropertyDefinition, standard_entry_type

from httk.serve.optimade.schema.served import (
    ServedSchema,
    build_served_schema,
    entry_type_definition_from_simple,
)

# (name, fulltype, unit, dimensions) for the full v1.2/v1.3 structures property
# set. Descriptions are short stand-ins; the real standard lives in
# httk-atomistic's vendored structures.json.
_STRUCTURE_PROPS: list[tuple[str, str, str | None, dict[str, Any] | None]] = [
    ("id", "string", None, None),
    ("type", "string", None, None),
    ("immutable_id", "string", None, None),
    ("last_modified", "string", None, None),
    ("elements", "list of string", None, None),
    ("nelements", "integer", None, None),
    ("elements_ratios", "list of float", None, None),
    ("chemical_formula_descriptive", "string", None, None),
    ("chemical_formula_reduced", "string", None, None),
    ("chemical_formula_hill", "string", None, None),
    ("chemical_formula_anonymous", "string", None, None),
    ("dimension_types", "list of integer", None, None),
    ("nperiodic_dimensions", "integer", None, None),
    (
        "lattice_vectors",
        "list of list of float",
        "angstrom",
        {"names": ["dim_lattice", "dim_spatial"], "sizes": [3, 3]},
    ),
    (
        "cartesian_site_positions",
        "list of list of float",
        "angstrom",
        {"names": ["dim_sites", "dim_spatial"], "sizes": [None, 3]},
    ),
    ("nsites", "integer", None, None),
    ("species_at_sites", "list of string", None, None),
    ("species", "list of dict", None, None),
    ("assemblies", "list of dict", None, None),
    ("structure_features", "list of string", None, None),
    ("space_group_symmetry_operations_xyz", "list of string", None, None),
    ("space_group_symbol_hall", "string", None, None),
    ("space_group_symbol_hermann_mauguin", "string", None, None),
    ("space_group_symbol_hermann_mauguin_extended", "string", None, None),
    ("space_group_it_number", "integer", None, None),
    (
        "fractional_site_positions",
        "list of list of float",
        None,
        {"names": ["dim_sites", "dim_spatial"], "sizes": [None, 3]},
    ),
    ("site_coordinate_span", "string", None, None),
    ("site_coordinate_span_description", "string", None, None),
    ("optimization_type", "string", None, None),
    ("wyckoff_positions", "list of string", None, None),
]


def structures_definition() -> EntryTypeDefinition:
    """A synthetic ``structures`` definition covering the full property set."""
    properties = {}
    for name, fulltype, unit, dimensions in _STRUCTURE_PROPS:
        required = name in ("id", "type")
        properties[name] = PropertyDefinition.from_simple(
            name,
            description="The " + name.replace("_", " ") + " of the structure.",
            fulltype=fulltype,
            unit=unit,
            dimensions=dimensions,
            required_response=required,
        )
    return EntryTypeDefinition("structures", "A structures entry.", properties)


def calculations_definition() -> EntryTypeDefinition:
    """The vendored ``calculations`` standard extended with httk custom properties."""
    return standard_entry_type("calculations").extended(
        {
            "_httk_total_energy": PropertyDefinition.from_simple(
                "_httk_total_energy", description="Total energy", fulltype="float"
            ),
            "_httk_structure_id": PropertyDefinition.from_simple(
                "_httk_structure_id", description="Index of the structure in structures entry type", fulltype="integer"
            ),
        }
    )


def references_definition() -> EntryTypeDefinition:
    return standard_entry_type("references")


def files_definition() -> EntryTypeDefinition:
    return standard_entry_type("files")


def widgets_definition() -> EntryTypeDefinition:
    """A synthetic custom entry type with one unprefixed and one custom property."""
    return EntryTypeDefinition(
        "widgets",
        "A widgets entry.",
        {
            "id": PropertyDefinition.from_simple("id", description="The widget id.", required_response=True),
            "type": PropertyDefinition.from_simple("type", description="The entry type.", required_response=True),
            "cogwheels": PropertyDefinition.from_simple(
                "cogwheels", description="The number of cogwheels in the widget.", fulltype="integer"
            ),
        },
    )


_KNOWN = {
    "structures": structures_definition,
    "calculations": calculations_definition,
    "references": references_definition,
    "files": files_definition,
    "widgets": widgets_definition,
}


def served_schema(
    served: Mapping[str, Sequence[str]],
    *,
    extra_entry_info: Mapping[str, Any] | None = None,
    default_response_overrides: Mapping[str, Sequence[str]] | None = None,
    sortable: Mapping[str, Sequence[str]] | None = None,
    default_includes: Mapping[str, Sequence[str]] | None = None,
) -> ServedSchema:
    """Build a served schema for the named entry types and served properties.

    Each entry type's definition comes from ``extra_entry_info`` (the simplified
    dialect, converted) when present, else from the built-in fixture set.
    """
    definitions: dict[str, EntryTypeDefinition] = {}
    for entry in served:
        if extra_entry_info is not None and entry in extra_entry_info:
            definitions[entry] = entry_type_definition_from_simple(entry, extra_entry_info[entry])
        else:
            definitions[entry] = _KNOWN[entry]()
    return build_served_schema(
        definitions,
        served,
        default_response_overrides=default_response_overrides,
        sortable=sortable,
        default_includes=default_includes,
    )
