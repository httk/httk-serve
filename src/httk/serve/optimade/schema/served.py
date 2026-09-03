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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from httk.core import EntryTypeDefinition, PropertyDefinition, apply_definition_prefix
from httk.core.property_definitions import known_definition_prefixes
from httk.core.provenance import RUNS_DEFINITION_ID

_RELATIONSHIPS_ROOT = apply_definition_prefix("relationships", RUNS_DEFINITION_ID)
"""The ``_httk_relationships`` filter-extension root, reserved as a name (derived)."""


def fulltype_of(definition: PropertyDefinition) -> str:
    """Reconstruct a property's simplified ``fulltype`` string.

    Maps the OPTIMADE type back to the compact spelling used by the filter
    layer: ``"string"``/``"integer"``/``"float"``/``"boolean"``/``"timestamp"``,
    ``"dict"`` for dictionaries, and ``"list of ..."`` (nesting through the
    definition's ``items``) for lists.

    :param definition: Property definition to inspect.
    :return: Compact fulltype spelling used by the filter and response layers.
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


def _is_default_response(name: str, definition: PropertyDefinition, default_names: set[str]) -> bool:
    """Whether a served property belongs in responses without response_fields."""
    if name in ("id", "type"):
        return True
    return name in default_names and definition.requirements.get("response-level") not in {"should not", "must not"}


def _is_queryable(name: str, definition: PropertyDefinition) -> bool:
    """Whether a served property may be used in ``filter=``.

    ``id`` and ``type`` are always queryable. A query support of ``"none"``
    disables filtering only for provider-specific (underscore-namespaced)
    properties, where it is the provider's authoritative statement that the field
    must not be filtered. On STANDARD properties an ``"none"`` query support is
    merely the specification's "querying not required" requirement level, which a
    deployment may (and httk does) still implement, so it must not disable them.
    Only the exact value ``"none"`` disables filtering; other levels keep the
    default behavior.
    """
    if name in ("id", "type") or not name.startswith("_"):
        return True
    return definition.requirements.get("query-support") != "none"


def simplified_property(
    definition: PropertyDefinition,
    *,
    sortable: bool = False,
    required_response: bool = False,
    default_response: bool = False,
    queryable: bool = True,
) -> dict[str, Any]:
    """Build a simplified property view for the filter and wrapping layers.

    Carries the ``description``, reconstructed ``fulltype``, the implementation
    flags (``sortable``/``required_response``/``default_response``/``queryable``),
    and — when present — the property's ``unit`` and ``dimensions`` (used by the
    trajectory frame-wrapping).

    :param definition: Property definition to simplify.
    :param sortable: Mark the property as sortable by the backend.
    :param required_response: Mark the property as required in responses.
    :param default_response: Mark the property as returned by default.
    :param queryable: Mark the property as usable in ``filter=`` (false honors
        an ``x-optimade-requirements.query-support`` of ``"none"``).
    :return: Simplified property metadata.
    """
    info: dict[str, Any] = {
        "description": definition.description,
        "fulltype": fulltype_of(definition),
        "sortable": sortable,
        "required_response": required_response,
        "default_response": default_response,
        "queryable": queryable,
    }
    unit = definition.unit
    if unit is not None and unit not in ("dimensionless", "inapplicable"):
        info["unit"] = unit
    dimensions = definition.dimensions
    if dimensions is not None:
        info["dimensions"] = {key: list(value) for key, value in dimensions.items()}
    return info


def entry_type_definition_from_simple(name: str, info: Mapping[str, Any]) -> EntryTypeDefinition:
    """Build an :class:`~httk.core.EntryTypeDefinition` from simplified metadata.

    ``info`` is a ``{"description": <str>, "properties": {<name>: <simplified
    property dict>}}`` mapping (as produced by, e.g.,
    :func:`~httk.serve.optimade.schema.trajectories.trajectories_entry_info`); each
    property is generated with
    :meth:`~httk.core.PropertyDefinition.from_simple`.

    :param name: Entry endpoint and definition name.
    :param info: Simplified entry description and property mapping.
    :return: Full entry-type definition.
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
    """Describe served entry types and their derived lookup tables.

    :param entry_info: Simplified entry-info documents keyed by entry type.
    :param entry_definition_ids: Definition IRIs keyed by entry type.
    :param recognized_prefixes: Property-definition prefixes recognized in requests.
    :param all_entries: Served entry endpoint names in declaration order.
    :param revision_endpoints: Store-backed revision endpoint names.
    :param revision_base: Base entry type keyed by revision endpoint.
    :param alt_endpoints: Store-backed alternative endpoint names.
    :param alt_base: Base entry type keyed by alternative endpoint.
    :param valid_endpoints: Fixed and entry endpoint names accepted by validation.
    :param properties_by_entry: Served property names keyed by entry type.
    :param default_response_fields: Default response fields keyed by entry type.
    :param required_response_fields: Required response fields keyed by entry type.
    :param unknown_response_fields: Defined but unserved fields keyed by entry type.
    :param sortable_response_fields: Sortable response fields keyed by entry type.
    :param property_definitions: Full property definitions keyed by entry type.
    """

    entry_info: dict[str, dict[str, Any]]
    entry_definition_ids: dict[str, str]
    recognized_prefixes: tuple[str, ...]
    all_entries: tuple[str, ...]
    revision_endpoints: tuple[str, ...]
    revision_base: dict[str, str]
    alt_endpoints: tuple[str, ...]
    alt_base: dict[str, str]
    valid_endpoints: tuple[str, ...]
    properties_by_entry: dict[str, tuple[str, ...]]
    default_response_fields: dict[str, tuple[str, ...]]
    required_response_fields: dict[str, tuple[str, ...]]
    unknown_response_fields: dict[str, tuple[str, ...]]
    sortable_response_fields: dict[str, tuple[str, ...]]
    property_definitions: dict[str, dict[str, dict[str, Any]]]


def derived_endpoint_name(base: str, suffix: str) -> str:
    """Return the derived (``~revs``/``~alts``) endpoint name for a base entry.

    An already-``_``-prefixed base (a provider wire name such as ``_httk_runs``)
    keeps its single prefix; a bare standard base gets the ``_httk_`` prefix, so
    both the schema and the route parser spell ``_httk_runs~revs`` and
    ``_httk_structures~revs`` alike.

    :param base: The served base entry-type name.
    :param suffix: The derived-endpoint suffix (``"revs"`` or ``"alts"``).
    :return: The derived endpoint name.
    """
    return f"{base}~{suffix}" if base.startswith("_") else f"_httk_{base}~{suffix}"


def build_served_schema(
    definitions: Mapping[str, EntryTypeDefinition],
    served: Mapping[str, Sequence[str]] | None = None,
    *,
    default_response_overrides: Mapping[str, Sequence[str]] | None = None,
    sortable: Mapping[str, Sequence[str]] | None = None,
    recognized_prefixes: tuple[str, ...] | None = None,
    revisions: Sequence[str] = (),
    alternatives: Sequence[str] = (),
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

    :param definitions: Full definitions keyed by served entry type.
    :param served: Optional served-property subset keyed by entry type.
    :param default_response_overrides: Additional default fields keyed by entry type.
    :param sortable: Sortable fields keyed by entry type.
    :param recognized_prefixes: Prefixes recognized in response-field requests.
    :param revisions: Base entries for which stored revision endpoints are served.
    :param alternatives: Base entries for which stored alternative endpoints are served.
    :return: Derived schema and lookup tables.
    :raises ValueError: If a requested served property is not defined.
    """
    if recognized_prefixes is None:
        recognized_prefixes = known_definition_prefixes()
    if _RELATIONSHIPS_ROOT in definitions:
        raise ValueError(f"Entry type name {_RELATIONSHIPS_ROOT!r} is reserved for the relationships filter extension.")
    revision_bases = tuple(revisions)
    unknown_revisions = [entry for entry in revision_bases if entry not in definitions]
    if unknown_revisions:
        raise ValueError("Revision endpoint requested for undefined entry type(s): " + ", ".join(unknown_revisions))
    if len(set(revision_bases)) != len(revision_bases):
        raise ValueError("Revision endpoint entries must be unique.")

    alt_bases = tuple(alternatives)
    unknown_alternatives = [entry for entry in alt_bases if entry not in definitions]
    if unknown_alternatives:
        raise ValueError(
            "Alternative endpoint requested for undefined entry type(s): " + ", ".join(unknown_alternatives)
        )
    if len(set(alt_bases)) != len(alt_bases):
        raise ValueError("Alternative endpoint entries must be unique.")

    revision_endpoints = tuple(derived_endpoint_name(entry, "revs") for entry in revision_bases)
    alt_endpoints = tuple(derived_endpoint_name(entry, "alts") for entry in alt_bases)
    fixed_endpoints = {"", "info", "links", "partial_data", "versions"}
    collisions = [
        endpoint
        for endpoint in revision_endpoints + alt_endpoints
        if endpoint in definitions or endpoint in fixed_endpoints
    ]
    if collisions:
        raise ValueError("Generated revision endpoint name collision(s): " + ", ".join(collisions))
    revision_base = dict(zip(revision_endpoints, revision_bases, strict=True))
    alt_base = dict(zip(alt_endpoints, alt_bases, strict=True))
    expanded_definitions = dict(definitions)
    expanded_served: dict[str, Sequence[str]] = dict(served or {})
    expanded_defaults: dict[str, Sequence[str]] = dict(default_response_overrides or {})
    expanded_sortable: dict[str, Sequence[str]] = dict(sortable or {})
    for revision_endpoint, entry in revision_base.items():
        expanded_definitions[revision_endpoint] = definitions[entry].extended(
            {
                "_httk_id": PropertyDefinition.from_simple(
                    "_httk_id",
                    description="The lineage (logical) entry id shared by all revisions of this entry.",
                )
            }
        )
        base_served = (
            list(served[entry]) if served is not None and entry in served else list(definitions[entry].properties)
        )
        expanded_served[revision_endpoint] = tuple(base_served + ["_httk_id"])
        base_defaults = (
            list(default_response_overrides.get(entry, ())) if default_response_overrides is not None else []
        )
        expanded_defaults[revision_endpoint] = tuple(base_defaults + ["_httk_id"])
        base_sortable = list(sortable.get(entry, ())) if sortable is not None else []
        expanded_sortable[revision_endpoint] = tuple(base_sortable + ["_httk_id"])
    for alt_endpoint, entry in alt_base.items():
        expanded_definitions[alt_endpoint] = definitions[entry].extended(
            {
                "_httk_id": PropertyDefinition.from_simple(
                    "_httk_id",
                    description="The lineage (logical) entry id of the entry this alternative represents.",
                ),
                "_httk_kind": PropertyDefinition.from_simple(
                    "_httk_kind",
                    description="The kind token naming this alternative representation of the entry.",
                ),
            }
        )
        base_served = (
            list(served[entry]) if served is not None and entry in served else list(definitions[entry].properties)
        )
        expanded_served[alt_endpoint] = tuple(base_served + ["_httk_id", "_httk_kind"])
        base_defaults = (
            list(default_response_overrides.get(entry, ())) if default_response_overrides is not None else []
        )
        expanded_defaults[alt_endpoint] = tuple(base_defaults + ["_httk_id", "_httk_kind"])
        base_sortable = list(sortable.get(entry, ())) if sortable is not None else []
        expanded_sortable[alt_endpoint] = tuple(base_sortable + ["_httk_id", "_httk_kind"])
    entry_info: dict[str, dict[str, Any]] = {}
    entry_definition_ids: dict[str, str] = {}
    property_definitions: dict[str, dict[str, dict[str, Any]]] = {}
    properties_by_entry: dict[str, tuple[str, ...]] = {}
    default_response_fields: dict[str, tuple[str, ...]] = {}
    required_response_fields: dict[str, tuple[str, ...]] = {}
    unknown_response_fields: dict[str, tuple[str, ...]] = {}
    sortable_response_fields: dict[str, tuple[str, ...]] = {}

    for entry, definition in expanded_definitions.items():
        if definition.definition_id is not None:
            entry_definition_ids[entry] = definition.definition_id
        described = definition.properties
        served_names = list(expanded_served[entry]) if entry in expanded_served else list(described)

        if _RELATIONSHIPS_ROOT in served_names:
            raise ValueError(
                f"Property name {_RELATIONSHIPS_ROOT!r} is reserved for the relationships filter extension."
            )
        missing = [name for name in served_names if name not in described]
        if missing:
            raise ValueError(
                "Entry type '"
                + entry
                + "' is asked to serve property(ies) not described by its definition: "
                + ", ".join(missing)
                + "."
            )

        sortable_names = set(expanded_sortable.get(entry, ()))
        default_names = set(expanded_defaults.get(entry, ()))

        simplified: dict[str, Any] = {}
        prop_defs: dict[str, dict[str, Any]] = {}
        defaults: list[str] = []
        requireds: list[str] = []
        sortables: list[str] = []
        for name in served_names:
            prop = described[name]
            is_sortable = name in sortable_names
            is_default = _is_default_response(name, prop, default_names)
            is_required = _is_required_response(name, prop)
            is_queryable = _is_queryable(name, prop)
            simplified[name] = simplified_property(
                prop,
                sortable=is_sortable,
                required_response=is_required,
                default_response=is_default,
                queryable=is_queryable,
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
        entry_definition_ids=entry_definition_ids,
        recognized_prefixes=recognized_prefixes,
        all_entries=all_entries,
        revision_endpoints=revision_endpoints,
        revision_base=revision_base,
        alt_endpoints=alt_endpoints,
        alt_base=alt_base,
        valid_endpoints=tuple(
            ["info", "links"]
            + list(all_entries)
            + list(revision_endpoints)
            + list(alt_endpoints)
            + ["info/" + x for x in all_entries + revision_endpoints + alt_endpoints]
            + [""]
        ),
        properties_by_entry=properties_by_entry,
        default_response_fields=default_response_fields,
        required_response_fields=required_response_fields,
        unknown_response_fields=unknown_response_fields,
        sortable_response_fields=sortable_response_fields,
        property_definitions=property_definitions,
    )
