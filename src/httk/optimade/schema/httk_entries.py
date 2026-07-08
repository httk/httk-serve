"""The subset of the OPTIMADE entry/property definitions served by httk.

This narrows :mod:`httk.optimade.schema.entries` (the full specification data)
down to the entry types and properties the httk backend implements, and
derives the default/required/unknown response-field tables used during request
validation and response generation.
"""

from . import entries
from .entries import EntryInfo, PropertyInfo

httk_recognized_prefixes: tuple[str, ...] = ('_httk_', '_omdb_')

httk_all_entries: list[str] = ['structures', 'calculations']

_structure_properties = [
    'id',
    'type',
    'elements',
    'nelements',
    'chemical_formula_descriptive',
    'dimension_types',
    'nperiodic_dimensions',
    'lattice_vectors',
    'structure_features',
    'nsites',
    'species_at_sites',
    'cartesian_site_positions',
    'chemical_formula_anonymous',
    'chemical_formula_reduced',
]

_calculation_properties = [
    'id',
    'type',
    '_httk_total_energy',
    '_httk_structure_id',
]

# Properties served in responses by default, beyond what the specification
# data already marks as default_response.
_default_response_overrides = {
    'structures': [
        'structure_features',
        'lattice_vectors',
        'elements',
        'nelements',
        'chemical_formula_descriptive',
        'dimension_types',
        'nperiodic_dimensions',
        'nsites',
        'species_at_sites',
        'cartesian_site_positions',
        'chemical_formula_anonymous',
        'chemical_formula_reduced',
    ],
    'calculations': [
        '_httk_total_energy',
        '_httk_structure_id',
    ],
}


def _build_entry_info() -> dict[str, EntryInfo]:
    """Build the httk entry info as an independent copy of the spec data."""
    selected = {
        'structures': _structure_properties,
        'calculations': _calculation_properties,
    }
    entry_info: dict[str, EntryInfo] = {}
    for entry, property_names in selected.items():
        spec_properties = entries.entry_info[entry]['properties']
        properties: dict[str, PropertyInfo] = {}
        for name in property_names:
            prop = spec_properties[name].copy()
            # Nothing served by the httk backend is sortable.
            prop['sortable'] = False
            if name in _default_response_overrides[entry]:
                prop['default_response'] = True
            properties[name] = prop
        entry_info[entry] = {
            'description': entries.entry_info[entry]['description'],
            'properties': properties,
        }
    return entry_info


httk_entry_info: dict[str, EntryInfo] = _build_entry_info()

httk_valid_endpoints: list[str] = ['info', 'links'] + httk_all_entries + ["info/" + x for x in httk_all_entries] + ['']

httk_properties_by_entry: dict[str, list[str]] = {
    x: list(httk_entry_info[x]['properties'].keys()) for x in httk_entry_info
}

httk_valid_response_fields = httk_properties_by_entry

default_response_fields: dict[str, list[str]] = {
    entry: [p for p, info in httk_entry_info[entry]['properties'].items() if info.get('default_response', False)]
    for entry in httk_all_entries
}

required_response_fields: dict[str, list[str]] = {
    entry: [p for p, info in httk_entry_info[entry]['properties'].items() if info.get('required_response', False)]
    for entry in httk_all_entries
}

# Properties defined by the specification for an entry type, but not
# implemented by the httk backend.
httk_unknown_response_fields: dict[str, list[str]] = {
    entry: [p for p in entries.properties_by_entry[entry] if p not in httk_entry_info[entry]['properties']]
    for entry in httk_all_entries
}
