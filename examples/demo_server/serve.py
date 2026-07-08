"""A small OPTIMADE demo server over an in-memory dataset.

Run with::

    python examples/demo_server/serve.py

then query, e.g.::

    curl 'http://localhost:8080/structures?filter=elements HAS "Ga" AND nelements=2'
"""

from typing import Any

from inmemory_backend import InMemoryStore

from httk.optimade import BackendAdapter, EntrySource, OptimadeConfig, serve
from httk.optimade.schema.served import build_served_schema


def structure(
    sid: str, formula: str, anonymous: str, species_at_sites: list[str], positions: list[list[float]]
) -> dict[str, Any]:
    return {
        '__id': sid,
        'formula': formula,
        'anonymous_formula': anonymous,
        'formula_symbols': sorted(set(species_at_sites)),
        'number_of_elements': len(set(species_at_sites)),
        'species_at_sites': species_at_sites,
        'positions': positions,
    }


STRUCTURES = [
    structure('demo-1', 'GaTi', 'AB', ['Ga', 'Ti'], [[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]]),
    structure('demo-2', 'Si', 'A', ['Si'], [[0.0, 0.0, 0.0]]),
    structure('demo-3', 'SiO2', 'AB2', ['Si', 'O', 'O'], [[0.0, 0.0, 0.0], [0.3, 0.3, 0.3], [0.6, 0.6, 0.6]]),
    structure('demo-4', 'GaAs', 'AB', ['Ga', 'As'], [[0.0, 0.0, 0.0], [0.25, 0.25, 0.25]]),
    structure('demo-5', 'NaCl', 'AB', ['Na', 'Cl'], [[0.0, 0.0, 0.0], [0.5, 0.0, 0.0]]),
]

CALCULATIONS = [
    {'__id': 'calc-1', 'total_energy': -12.5, 'structure': 'demo-1'},
    {'__id': 'calc-2', 'total_energy': -5.4, 'structure': 'demo-2'},
]

_CUBIC_CELL = [[5.0, 0.0, 0.0], [0.0, 5.0, 0.0], [0.0, 0.0, 5.0]]

STRUCTURE_FIELDS = {
    'type': lambda x: "structures",
    'id': lambda x: x['__id'],
    'structure_features': lambda x: [],
    'lattice_vectors': lambda x: _CUBIC_CELL,
    'elements': lambda x: sorted(set(x['formula_symbols'])),
    'nelements': lambda x: x['number_of_elements'],
    'chemical_formula_descriptive': lambda x: x['formula'],
    'chemical_formula_reduced': lambda x: x['formula'],
    'chemical_formula_anonymous': lambda x: x['anonymous_formula'],
    'dimension_types': lambda x: [1, 1, 1],
    'nperiodic_dimensions': lambda x: 3,
    'nsites': lambda x: len(x['positions']),
    'species_at_sites': lambda x: x['species_at_sites'],
    'cartesian_site_positions': lambda x: x['positions'],
}

CALCULATION_FIELDS = {
    'type': lambda x: "calculations",
    'id': lambda x: x['__id'],
    '_httk_total_energy': lambda x: x['total_energy'],
    '_httk_structure_id': lambda x: x['structure'],
}


# The properties served for each entry type; the same lists the default httk
# schema serves, restated here so a custom (sortable-enabled) schema can be built.
STRUCTURE_PROPERTIES = [
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

CALCULATION_PROPERTIES = [
    'id',
    'type',
    '_httk_total_energy',
    '_httk_structure_id',
]

DEFAULT_RESPONSE_OVERRIDES = {
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

# OPTIMADE property -> backend column name, for the sortable structure properties.
STRUCTURE_SORT_COLUMNS = {
    'id': '__id',
    'nelements': 'number_of_elements',
    'chemical_formula_descriptive': 'formula',
}


def make_adapter() -> BackendAdapter:
    store = InMemoryStore({'structures': STRUCTURES, 'calculations': CALCULATIONS})
    schema = build_served_schema(
        {
            'structures': STRUCTURE_PROPERTIES,
            'calculations': CALCULATION_PROPERTIES,
        },
        default_response_overrides=DEFAULT_RESPONSE_OVERRIDES,
        sortable={'structures': list(STRUCTURE_SORT_COLUMNS)},
    )
    return BackendAdapter(
        store=store,
        sources={
            'structures': (
                EntrySource(target='structures', fields=STRUCTURE_FIELDS, sort_columns=STRUCTURE_SORT_COLUMNS),
            ),
            'calculations': (EntrySource(target='calculations', fields=CALCULATION_FIELDS),),
        },
        schema=schema,
    )


if __name__ == "__main__":
    config = OptimadeConfig(
        links=[
            {
                "id": "index",
                "name": "demo index",
                "description": "Demo OPTIMADE database served by httk-optimade",
                "base_url": "http://localhost:8080",
                "homepage": "https://httk.org",
                "link_type": "root",
            },
        ]
    )

    serve(make_adapter(), config, port=8080, debug=True)
