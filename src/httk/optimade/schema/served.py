"""The registry of entry types and properties served by an OPTIMADE deployment.

A :class:`ServedSchema` narrows the full specification data in
:mod:`httk.optimade.schema.entries` down to the entry types and properties a
backend implements, and derives the endpoint and response-field tables used
during request validation and response generation.
"""

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from . import entries as entry_spec
from .entries import EntryInfo, PropertyInfo
from .property_definitions import RECOGNIZED_PREFIXES, entry_property_definitions


@dataclass(frozen=True)
class ServedSchema:
    """The entry types and properties served, with derived lookup tables."""

    entry_info: dict[str, EntryInfo]
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
    entries: Mapping[str, Sequence[str]],
    *,
    extra_entry_info: Mapping[str, EntryInfo] | None = None,
    default_response_overrides: Mapping[str, Sequence[str]] | None = None,
    sortable: Mapping[str, Sequence[str]] | None = None,
    recognized_prefixes: tuple[str, ...] = RECOGNIZED_PREFIXES,
) -> ServedSchema:
    """Build a :class:`ServedSchema` serving the given properties per entry type.

    ``entries`` maps entry type names to the property names served for them.
    Property information is copied from the specification data, or from
    ``extra_entry_info`` for entry types the specification does not define.
    Properties are marked non-sortable except those named in ``sortable``, and
    ``default_response_overrides`` marks additional properties as served in
    responses by default.
    """
    entry_info: dict[str, EntryInfo] = {}
    for entry, property_names in entries.items():
        if entry in entry_spec.entry_info:
            source_info = entry_spec.entry_info[entry]
        elif extra_entry_info is not None and entry in extra_entry_info:
            source_info = extra_entry_info[entry]
        else:
            raise KeyError("No entry information available for entry type: " + entry)
        source_properties = source_info['properties']
        properties: dict[str, PropertyInfo] = {}
        for name in property_names:
            prop = source_properties[name].copy()
            prop['sortable'] = sortable is not None and name in sortable.get(entry, ())
            if default_response_overrides is not None and name in default_response_overrides.get(entry, ()):
                prop['default_response'] = True
            properties[name] = prop
        entry_info[entry] = {
            'description': source_info['description'],
            'properties': properties,
        }

    all_entries = tuple(entry_info)

    return ServedSchema(
        entry_info=entry_info,
        recognized_prefixes=recognized_prefixes,
        all_entries=all_entries,
        valid_endpoints=tuple(['info', 'links'] + list(all_entries) + ["info/" + x for x in all_entries] + ['']),
        properties_by_entry={entry: tuple(entry_info[entry]['properties']) for entry in all_entries},
        default_response_fields={
            entry: tuple(
                p for p, info in entry_info[entry]['properties'].items() if info.get('default_response', False)
            )
            for entry in all_entries
        },
        required_response_fields={
            entry: tuple(
                p for p, info in entry_info[entry]['properties'].items() if info.get('required_response', False)
            )
            for entry in all_entries
        },
        unknown_response_fields={
            entry: tuple(
                p for p in entry_spec.properties_by_entry.get(entry, ()) if p not in entry_info[entry]['properties']
            )
            for entry in all_entries
        },
        sortable_response_fields={
            entry: tuple(p for p, info in entry_info[entry]['properties'].items() if info.get('sortable', False))
            for entry in all_entries
        },
        property_definitions={entry: entry_property_definitions(entry, entry_info) for entry in all_entries},
    )
