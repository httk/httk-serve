"""The registry of entry types and properties served by an OPTIMADE deployment.

A :class:`ServedSchema` narrows a set of first-class
:class:`~httk.core.EntryTypeDefinition` objects (supplied from outside, e.g. by
an :class:`~httk.core.EntryProvider`) down to the entry types and properties a
backend implements, and derives the endpoint and response-field tables used
during request validation and response generation.

The full OPTIMADE property definitions live in httk-core; this module keeps, for
each served property, a small *simplified* view (its ``fulltype`` and the
implementation flags) that the filter-translation and response layers consume,
alongside the full property definitions served on the entry listing info
endpoint.
"""

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from httk.core import EntryTypeDefinition, PropertyDefinition
from httk.core.property_definitions import known_definition_prefixes


def fulltype_of(definition: PropertyDefinition) -> str:
    """Reconstruct the simplified ``fulltype`` string of a property definition.

    Maps the OPTIMADE type back to the compact spelling used by the filter
    layer: ``"string"``/``"integer"``/``"float"``/``"boolean"``/``"timestamp"``,
    ``"dict"`` for dictionaries, and ``"list of ..."`` (nesting through the
    definition's ``items``) for lists.
    """
    return _fulltype_from_doc(definition.as_optimade())


def _fulltype_from_doc(doc: Mapping[str, Any]) -> str:
    optimade_type = doc["x-optimade-type"]
    if optimade_type == "list":
        return "list of " + _fulltype_from_doc(doc["items"])
    if optimade_type == "dictionary":
        return "dict"
    return optimade_type


def _is_required_response(name: str, definition: PropertyDefinition) -> bool:
    """Whether a property must always carry a non-null value in responses.

    ``id`` and ``type`` are always required. Otherwise a property is required
    only when its OPTIMADE requirements mark the response level ``"must"`` *and*
    the value is non-nullable (so, e.g., ``files.url`` is required, but a
    nullable ``last_modified`` marked ``"must"`` is not).
    """
    if name in ("id", "type"):
        return True
    return definition.requirements.get("response-level") == "must" and not definition.nullable


def simplified_property(
    definition: PropertyDefinition,
    *,
    sortable: bool = False,
    required_response: bool = False,
    default_response: bool = False,
) -> dict[str, Any]:
    """A small dialect view of a property definition for the filter/wrap layers.

    Carries the ``description``, reconstructed ``fulltype``, the implementation
    flags (``sortable``/``required_response``/``default_response``), and — when
    present — the property's ``unit`` and ``dimensions`` (used by the trajectory
    frame-wrapping).
    """
    info: dict[str, Any] = {
        "description": definition.description,
        "fulltype": fulltype_of(definition),
        "sortable": sortable,
        "required_response": required_response,
        "default_response": default_response,
    }
    unit = definition.unit
    if unit is not None and unit not in ("dimensionless", "inapplicable"):
        info["unit"] = unit
    dimensions = definition.dimensions
    if dimensions is not None:
        info["dimensions"] = {key: list(value) for key, value in dimensions.items()}
    return info


def entry_type_definition_from_simple(name: str, info: Mapping[str, Any]) -> EntryTypeDefinition:
    """Build an :class:`~httk.core.EntryTypeDefinition` from the simplified dialect.

    ``info`` is a ``{"description": <str>, "properties": {<name>: <simplified
    property dict>}}`` mapping (as produced by, e.g.,
    :func:`~httk.optimade.schema.trajectories.trajectories_entry_info`); each
    property is generated with
    :meth:`~httk.core.PropertyDefinition.from_simple`.
    """
    properties = {
        prop_name: PropertyDefinition.from_simple(
            prop_name,
            description=prop_info.get("description", ""),
            fulltype=prop_info.get("fulltype", "string"),
            unit=prop_info.get("unit"),
            dimensions=prop_info.get("dimensions"),
            dict_properties=prop_info.get("dict_properties"),
            metadata_definition=prop_info.get("metadata_definition"),
            required_response=prop_info.get("required_response", False),
        )
        for prop_name, prop_info in info["properties"].items()
    }
    return EntryTypeDefinition(name, info["description"], properties)


@dataclass(frozen=True)
class ServedSchema:
    """The entry types and properties served, with derived lookup tables."""

    entry_info: dict[str, dict[str, Any]]
    recognized_prefixes: tuple[str, ...]
    all_entries: tuple[str, ...]
    valid_endpoints: tuple[str, ...]
    properties_by_entry: dict[str, tuple[str, ...]]
    default_response_fields: dict[str, tuple[str, ...]]
    required_response_fields: dict[str, tuple[str, ...]]
    unknown_response_fields: dict[str, tuple[str, ...]]
    sortable_response_fields: dict[str, tuple[str, ...]]
    property_definitions: dict[str, dict[str, dict[str, Any]]]


def build_served_schema(
    definitions: Mapping[str, EntryTypeDefinition],
    served: Mapping[str, Sequence[str]] | None = None,
    *,
    default_response_overrides: Mapping[str, Sequence[str]] | None = None,
    sortable: Mapping[str, Sequence[str]] | None = None,
    recognized_prefixes: tuple[str, ...] | None = None,
) -> ServedSchema:
    """Build a :class:`ServedSchema` from entry-type definitions.

    ``definitions`` maps each served entry type name to its full
    :class:`~httk.core.EntryTypeDefinition`. ``served`` maps each entry type to
    the subset of property names actually served (defaulting to every property
    the definition describes); every served name MUST be described by the
    definition (a :class:`ValueError` names any offender). ``id`` and ``type``
    are always default- and required-response; ``default_response_overrides``
    marks additional served properties as default-response, and ``sortable``
    marks served properties as sortable. ``recognized_prefixes`` defaults to the
    prefixes currently registered via :func:`~httk.core.register_definition_prefix`
    (resolved at call time so newly registered prefixes are honored).
    """
    if recognized_prefixes is None:
        recognized_prefixes = known_definition_prefixes()
    entry_info: dict[str, dict[str, Any]] = {}
    property_definitions: dict[str, dict[str, dict[str, Any]]] = {}
    properties_by_entry: dict[str, tuple[str, ...]] = {}
    default_response_fields: dict[str, tuple[str, ...]] = {}
    required_response_fields: dict[str, tuple[str, ...]] = {}
    unknown_response_fields: dict[str, tuple[str, ...]] = {}
    sortable_response_fields: dict[str, tuple[str, ...]] = {}

    for entry, definition in definitions.items():
        described = definition.properties
        served_names = list(served[entry]) if served is not None and entry in served else list(described)

        missing = [name for name in served_names if name not in described]
        if missing:
            raise ValueError(
                "Entry type '"
                + entry
                + "' is asked to serve property(ies) not described by its definition: "
                + ", ".join(missing)
                + "."
            )

        sortable_names = set(sortable.get(entry, ())) if sortable is not None else set()
        default_names = (
            set(default_response_overrides.get(entry, ())) if default_response_overrides is not None else set()
        )

        simplified: dict[str, Any] = {}
        prop_defs: dict[str, dict[str, Any]] = {}
        defaults: list[str] = []
        requireds: list[str] = []
        sortables: list[str] = []
        for name in served_names:
            prop = described[name]
            is_sortable = name in sortable_names
            is_default = name in ("id", "type") or name in default_names
            is_required = _is_required_response(name, prop)
            simplified[name] = simplified_property(
                prop, sortable=is_sortable, required_response=is_required, default_response=is_default
            )
            prop_defs[name] = prop.with_implementation(sortable=is_sortable, response_default=is_default).as_optimade()
            if is_default:
                defaults.append(name)
            if is_required:
                requireds.append(name)
            if is_sortable:
                sortables.append(name)

        entry_info[entry] = {"description": definition.description, "properties": simplified}
        property_definitions[entry] = prop_defs
        properties_by_entry[entry] = tuple(served_names)
        default_response_fields[entry] = tuple(defaults)
        required_response_fields[entry] = tuple(requireds)
        sortable_response_fields[entry] = tuple(sortables)
        unknown_response_fields[entry] = tuple(name for name in described if name not in served_names)

    all_entries = tuple(definitions)

    return ServedSchema(
        entry_info=entry_info,
        recognized_prefixes=recognized_prefixes,
        all_entries=all_entries,
        valid_endpoints=tuple(["info", "links"] + list(all_entries) + ["info/" + x for x in all_entries] + [""]),
        properties_by_entry=properties_by_entry,
        default_response_fields=default_response_fields,
        required_response_fields=required_response_fields,
        unknown_response_fields=unknown_response_fields,
        sortable_response_fields=sortable_response_fields,
        property_definitions=property_definitions,
    )
