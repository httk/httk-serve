"""A small OPTIMADE demo server over an in-memory dataset.

Run with::

    python examples/demo_server/serve.py

then query, e.g.::

    curl 'http://localhost:8080/structures?filter=elements HAS "Ga" AND nelements=2'
"""

from collections import Counter
from functools import reduce
from math import gcd
from typing import Any

from httk.core import EntryTypeDefinition, PropertyDefinition, standard_entry_type
from httk.data.optimade_query import relationship_id_handler

from httk.optimade import (
    BackendAdapter,
    EntrySource,
    InMemoryStore,
    OptimadeConfig,
    serve,
)
from httk.optimade.backend import simple_property_handlers
from httk.optimade.backend.partial import PartialDimension, PartialValue
from httk.optimade.schema.served import (
    ServedSchema,
    build_served_schema,
    entry_type_definition_from_simple,
)
from httk.optimade.schema.trajectories import trajectories_entry_info


def _reduced_formula(species_at_sites: list[str]) -> str:
    """The OPTIMADE ``chemical_formula_reduced``: elements in alphabetical order
    with integer proportions reduced by their greatest common divisor and the
    proportion ``1`` omitted."""
    counts = Counter(species_at_sites)
    divisor = reduce(gcd, counts.values())
    parts = []
    for element in sorted(counts):
        proportion = counts[element] // divisor
        parts.append(element + (str(proportion) if proportion > 1 else ''))
    return ''.join(parts)


def _anonymous_formula(species_at_sites: list[str]) -> str:
    """The OPTIMADE ``chemical_formula_anonymous``: proportions reduced and
    sorted in descending order, labelled A, B, C, ... with ``1`` omitted."""
    counts = Counter(species_at_sites)
    divisor = reduce(gcd, counts.values())
    proportions = sorted((c // divisor for c in counts.values()), reverse=True)
    labels = (chr(ord('A') + i) for i in range(len(proportions)))
    return ''.join(label + (str(p) if p > 1 else '') for label, p in zip(labels, proportions))


def structure(
    sid: str,
    formula: str,
    species_at_sites: list[str],
    positions: list[list[float]],
    last_modified: str,
    references: list[str] | None = None,
) -> dict[str, Any]:
    return {
        '__id': sid,
        'formula': formula,
        # Spec-compliant reduced/anonymous formulas, stored under real backend
        # record keys so filters compare against exactly what the attribute
        # extractors serve (see STRUCTURE_FIELDS, which read these keys).
        'formula_reduced': _reduced_formula(species_at_sites),
        'formula_anonymous': _anonymous_formula(species_at_sites),
        'formula_symbols': sorted(set(species_at_sites)),
        'number_of_elements': len(set(species_at_sites)),
        'nsites': len(positions),
        'species_at_sites': species_at_sites,
        'positions': positions,
        'last_modified': last_modified,
        'references': references if references is not None else [],
    }


STRUCTURES = [
    structure(
        'demo-1', 'GaTi', ['Ga', 'Ti'], [[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]], '2021-03-14T08:00:00Z', references=['ref-1']
    ),
    structure('demo-2', 'Si', ['Si'], [[0.0, 0.0, 0.0]], '2021-05-02T12:30:00Z'),
    structure(
        'demo-3',
        'SiO2',
        ['Si', 'O', 'O'],
        [[0.0, 0.0, 0.0], [0.3, 0.3, 0.3], [0.6, 0.6, 0.6]],
        '2019-11-20T16:45:00Z',
        references=['ref-2'],
    ),
    structure('demo-4', 'GaAs', ['Ga', 'As'], [[0.0, 0.0, 0.0], [0.25, 0.25, 0.25]], '2022-01-10T09:15:00Z'),
    structure('demo-5', 'NaCl', ['Na', 'Cl'], [[0.0, 0.0, 0.0], [0.5, 0.0, 0.0]], '2020-07-07T07:07:00Z'),
]

CALCULATIONS = [
    {
        '__id': 'calc-1',
        'total_energy': -12.5,
        'structure': 'demo-1',
        'files': [('file-1', 'input'), ('file-2', 'output')],
        'file_ids': ['file-1', 'file-2'],
        'last_modified': '2021-03-15T10:20:00Z',
    },
    {
        '__id': 'calc-2',
        'total_energy': -5.4,
        'structure': 'demo-2',
        'files': [],
        'file_ids': [],
        'last_modified': '2021-05-03T11:00:00Z',
    },
]

FILES = [
    {
        '__id': 'file-1',
        'url': 'https://example.org/files/calc-1/INCAR',
        'name': 'INCAR',
        'size': 512,
        'media_type': 'text/plain',
        'description': 'Input settings file',
        'checksums': {'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
        'last_modified': '2021-03-15T10:20:00Z',
    },
    {
        '__id': 'file-2',
        'url': 'https://example.org/files/calc-1/OUTCAR',
        'name': 'OUTCAR',
        'size': 204800,
        'media_type': 'text/plain',
        'description': 'Output log file',
        'checksums': {'sha256': '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08'},
        'last_modified': '2021-03-15T10:25:00Z',
    },
]

REFERENCES = [
    {
        '__id': 'ref-1',
        'title': 'A study of gallium titanium compounds',
        'journal': 'Journal of Demo Materials',
        'year': '2021',
        'doi': '10.1234/demo.2021.1',
        'authors': [{'name': 'Ada Lovelace'}, {'name': 'Alan Turing'}],
        'last_modified': '2021-02-01T00:00:00Z',
    },
    {
        '__id': 'ref-2',
        'title': 'Silicon dioxide polymorphs revisited',
        'journal': 'Demo Letters',
        'year': '2019',
        'doi': '10.1234/demo.2019.7',
        'authors': [{'name': 'Grace Hopper'}],
        'last_modified': '2019-09-09T09:00:00Z',
    },
]

_CUBIC_CELL = [[5.0, 0.0, 0.0], [0.0, 5.0, 0.0], [0.0, 0.0, 5.0]]

STRUCTURE_FIELDS = {
    'type': lambda x: "structures",
    'id': lambda x: x['__id'],
    'last_modified': lambda x: x['last_modified'],
    'structure_features': lambda x: [],
    'lattice_vectors': lambda x: _CUBIC_CELL,
    'elements': lambda x: sorted(set(x['formula_symbols'])),
    'nelements': lambda x: x['number_of_elements'],
    'chemical_formula_descriptive': lambda x: x['formula'],
    'chemical_formula_reduced': lambda x: x['formula_reduced'],
    'chemical_formula_anonymous': lambda x: x['formula_anonymous'],
    'dimension_types': lambda x: [1, 1, 1],
    'nperiodic_dimensions': lambda x: 3,
    'nsites': lambda x: x['nsites'],
    'species_at_sites': lambda x: x['species_at_sites'],
    'cartesian_site_positions': lambda x: x['positions'],
}

CALCULATION_FIELDS = {
    'type': lambda x: "calculations",
    'id': lambda x: x['__id'],
    'last_modified': lambda x: x['last_modified'],
    '_httk_total_energy': lambda x: x['total_energy'],
    '_httk_structure_id': lambda x: x['structure'],
}


def calculation_relationships(row: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    files = row.get('files') or []
    if not files:
        return {}
    return {'files': [{'id': fid, 'role': role} for fid, role in files]}


FILE_FIELDS = {
    'type': lambda x: "files",
    'id': lambda x: x['__id'],
    'last_modified': lambda x: x['last_modified'],
    'url': lambda x: x['url'],
    'name': lambda x: x['name'],
    'size': lambda x: x['size'],
    'media_type': lambda x: x['media_type'],
    'description': lambda x: x['description'],
    'checksums': lambda x: x['checksums'],
}

# OPTIMADE property -> backend record key, for the queryable file properties.
FILE_KEYS = {
    'last_modified': 'last_modified',
    'url': 'url',
    'name': 'name',
    'media_type': 'media_type',
}


def structure_relationships(row: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    ref_ids = row.get('references') or []
    if not ref_ids:
        return {}
    return {
        'references': [{'id': rid, 'description': 'Reference for this structure'} for rid in ref_ids],
    }


REFERENCE_FIELDS = {
    'type': lambda x: "references",
    'id': lambda x: x['__id'],
    'last_modified': lambda x: x['last_modified'],
    'title': lambda x: x['title'],
    'journal': lambda x: x['journal'],
    'year': lambda x: x['year'],
    'doi': lambda x: x['doi'],
    'authors': lambda x: x['authors'],
}

# OPTIMADE property -> backend record key, for the queryable reference properties.
REFERENCE_KEYS = {
    'last_modified': 'last_modified',
    'doi': 'doi',
    'year': 'year',
    'title': 'title',
    'journal': 'journal',
}


# One 5-frame trajectory of the GaTi structure. The per-frame Cartesian site
# positions (5 frames x 2 sites x 3 coordinates) vary slightly frame to frame;
# everything else is constant across the trajectory and served in compact form.
_TRAJECTORY_POSITIONS = [[[0.0 + 0.01 * f, 0.0, 0.0], [0.5, 0.5, 0.5 + 0.01 * f]] for f in range(5)]

TRAJECTORIES = [
    {
        '__id': 'traj-1',
        'nframes': 5,
        'reference_frames': [0, 4],
        'positions_frames': _TRAJECTORY_POSITIONS,
        'last_modified': '2021-03-14T08:30:00Z',
    },
]


def trajectory_positions(row: dict[str, Any]) -> PartialValue:
    frames = row['positions_frames']

    def fetch(slices: tuple[slice, ...]) -> Any:
        frame_slice, site_slice, spatial_slice = slices
        return [[site[spatial_slice] for site in frame[site_slice]] for frame in frames[frame_slice]]

    return PartialValue(
        dimensions=(
            PartialDimension('dim_frames', length=row['nframes'], sliceable=True),
            PartialDimension('dim_sites', length=2),
            PartialDimension('dim_spatial', length=3),
        ),
        fetch=fetch,
    )


TRAJECTORY_FIELDS = {
    'type': lambda x: "trajectories",
    'id': lambda x: x['__id'],
    'last_modified': lambda x: x['last_modified'],
    'nframes': lambda x: x['nframes'],
    'reference_frames': lambda x: x['reference_frames'],
    # Constant across frames: served as single-item lists (compact 'constant').
    'elements': lambda x: [['Ga', 'Ti']],
    'nelements': lambda x: [2],
    'lattice_vectors': lambda x: [_CUBIC_CELL],
    'chemical_formula_descriptive': lambda x: ['GaTi'],
    'dimension_types': lambda x: [[1, 1, 1]],
    'species_at_sites': lambda x: [['Ga', 'Ti']],
    # Large per-frame data: transferred via the partial data protocol.
    'cartesian_site_positions': trajectory_positions,
}

# OPTIMADE property -> backend record key, for the queryable trajectory properties.
TRAJECTORY_KEYS = {
    'last_modified': 'last_modified',
    'nframes': 'nframes',
}


# The properties served for each entry type; the same lists the default httk
# schema serves, restated here so a custom (sortable-enabled) schema can be built.
STRUCTURE_PROPERTIES = [
    'id',
    'type',
    'last_modified',
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
    'last_modified',
    '_httk_total_energy',
    '_httk_structure_id',
]

REFERENCE_PROPERTIES = [
    'id',
    'type',
    'last_modified',
    'title',
    'journal',
    'year',
    'doi',
    'authors',
    'url',
    'bib_type',
]

FILE_PROPERTIES = [
    'id',
    'type',
    'last_modified',
    'url',
    'name',
    'size',
    'media_type',
    'version',
    'description',
    'checksums',
]

# The structures properties reused (frame-wrapped) for the trajectory.
TRAJECTORY_STRUCTURE_PROPERTIES = [
    'id',
    'type',
    'last_modified',
    'elements',
    'nelements',
    'lattice_vectors',
    'chemical_formula_descriptive',
    'dimension_types',
    'species_at_sites',
    'cartesian_site_positions',
]

TRAJECTORY_PROPERTIES = TRAJECTORY_STRUCTURE_PROPERTIES + ['nframes', 'reference_frames']

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
    'references': [
        'title',
        'journal',
        'year',
        'doi',
        'authors',
    ],
    'files': [
        'url',
        'name',
        'size',
        'media_type',
        'description',
    ],
    'trajectories': [
        'nframes',
        'reference_frames',
        'elements',
        'nelements',
        'lattice_vectors',
        'chemical_formula_descriptive',
        'dimension_types',
        'species_at_sites',
        'cartesian_site_positions',
    ],
}

# OPTIMADE property -> backend record key, for the sortable structure properties.
STRUCTURE_SORT_KEYS = {
    'id': '__id',
    'nelements': 'number_of_elements',
    'chemical_formula_descriptive': 'formula',
}

# OPTIMADE property -> backend record key, for the queryable structure properties.
# Each key holds exactly the value the corresponding attribute extractor
# serves (e.g. the spec-compliant computed reduced/anonymous formulas, the real
# site count, the modification timestamp), so filters agree with responses.
STRUCTURE_KEYS = {
    'elements': 'formula_symbols',
    'nelements': 'number_of_elements',
    'chemical_formula_descriptive': 'formula',
    'chemical_formula_reduced': 'formula_reduced',
    'chemical_formula_anonymous': 'formula_anonymous',
    'nsites': 'nsites',
    'last_modified': 'last_modified',
}

# OPTIMADE property -> backend record key, for the queryable calculation properties.
CALCULATION_KEYS = {
    '_httk_total_energy': 'total_energy',
    '_httk_structure_id': 'structure',
    'last_modified': 'last_modified',
}


# (name, fulltype, unit, dimensions) for the structures properties this demo
# serves. The real ``structures`` standard is vendored by httk-atomistic; this
# example synthesizes just the served subset with PropertyDefinition.from_simple.
_STRUCTURE_PROP_SPECS: list[tuple[str, str, str | None, dict[str, Any] | None]] = [
    ('id', 'string', None, None),
    ('type', 'string', None, None),
    ('last_modified', 'string', None, None),
    ('elements', 'list of string', None, None),
    ('nelements', 'integer', None, None),
    ('chemical_formula_descriptive', 'string', None, None),
    ('chemical_formula_reduced', 'string', None, None),
    ('chemical_formula_anonymous', 'string', None, None),
    ('dimension_types', 'list of integer', None, None),
    ('nperiodic_dimensions', 'integer', None, None),
    (
        'lattice_vectors',
        'list of list of float',
        'angstrom',
        {'names': ['dim_lattice', 'dim_spatial'], 'sizes': [3, 3]},
    ),
    ('structure_features', 'list of string', None, None),
    ('nsites', 'integer', None, None),
    ('species_at_sites', 'list of string', None, None),
    (
        'cartesian_site_positions',
        'list of list of float',
        'angstrom',
        {'names': ['dim_sites', 'dim_spatial'], 'sizes': [None, 3]},
    ),
]


def _structures_definition() -> EntryTypeDefinition:
    properties = {
        name: PropertyDefinition.from_simple(
            name,
            description='The ' + name.replace('_', ' ') + ' of the structure.',
            fulltype=fulltype,
            unit=unit,
            dimensions=dimensions,
            required_response=name in ('id', 'type'),
        )
        for name, fulltype, unit, dimensions in _STRUCTURE_PROP_SPECS
    }
    return EntryTypeDefinition('structures', 'A structures entry.', properties)


def _calculations_definition() -> EntryTypeDefinition:
    return standard_entry_type('calculations').extended(
        {
            '_httk_total_energy': PropertyDefinition.from_simple(
                '_httk_total_energy', description='Total energy', fulltype='float'
            ),
            '_httk_structure_id': PropertyDefinition.from_simple(
                '_httk_structure_id', description='Index of the structure in structures entry type', fulltype='integer'
            ),
        }
    )


def _fulltypes(schema: ServedSchema, entry_type: str) -> dict[str, str]:
    """The property-name -> fulltype mapping simple_property_handlers consumes."""
    return {name: prop['fulltype'] for name, prop in schema.entry_info[entry_type]['properties'].items()}


def make_adapter() -> BackendAdapter:
    store = InMemoryStore(
        {
            'structures': STRUCTURES,
            'calculations': CALCULATIONS,
            'references': REFERENCES,
            'files': FILES,
            'trajectories': TRAJECTORIES,
        }
    )
    structures_definition = _structures_definition()
    definitions = {
        'structures': structures_definition,
        'calculations': _calculations_definition(),
        'references': standard_entry_type('references'),
        'files': standard_entry_type('files'),
        'trajectories': entry_type_definition_from_simple(
            'trajectories', trajectories_entry_info(structures_definition, TRAJECTORY_STRUCTURE_PROPERTIES)
        ),
    }
    schema = build_served_schema(
        definitions,
        {
            'structures': STRUCTURE_PROPERTIES,
            'calculations': CALCULATION_PROPERTIES,
            'references': REFERENCE_PROPERTIES,
            'files': FILE_PROPERTIES,
            'trajectories': TRAJECTORY_PROPERTIES,
        },
        default_response_overrides=DEFAULT_RESPONSE_OVERRIDES,
        sortable={'structures': list(STRUCTURE_SORT_KEYS)},
    )
    # Every entry type's filter handlers are derived generically from a
    # property-key map with simple_property_handlers (the same path
    # adapter_from_providers uses); relationship filters are added on top.
    field_handlers = {
        'structures': simple_property_handlers('structures', STRUCTURE_KEYS, _fulltypes(schema, 'structures')),
        'calculations': simple_property_handlers('calculations', CALCULATION_KEYS, _fulltypes(schema, 'calculations')),
        'references': simple_property_handlers('references', REFERENCE_KEYS, _fulltypes(schema, 'references')),
        'files': simple_property_handlers('files', FILE_KEYS, _fulltypes(schema, 'files')),
        'trajectories': simple_property_handlers('trajectories', TRAJECTORY_KEYS, _fulltypes(schema, 'trajectories')),
    }
    # A relationship filter handler: `references.id HAS "ref-1"` matches over the
    # structure row's 'references' key (a list of related reference ids).
    # Depth-1 relationship-property filters (e.g. `references.doi CONTAINS
    # "10.1234"`) then work automatically through the translation layer's
    # related-property resolver.
    structures_handlers = dict(field_handlers['structures'])
    structures_handlers['references.id'] = relationship_id_handler('references')
    field_handlers['structures'] = structures_handlers
    # `files.id HAS "file-1"` matches over the calculation row's 'file_ids' key.
    calculations_handlers = dict(field_handlers['calculations'])
    calculations_handlers['files.id'] = relationship_id_handler('file_ids')
    field_handlers['calculations'] = calculations_handlers
    return BackendAdapter(
        store=store,
        sources={
            'structures': (
                EntrySource(
                    target='structures',
                    fields=STRUCTURE_FIELDS,
                    sort_keys=STRUCTURE_SORT_KEYS,
                    relationships=structure_relationships,
                ),
            ),
            'calculations': (
                EntrySource(
                    target='calculations',
                    fields=CALCULATION_FIELDS,
                    relationships=calculation_relationships,
                ),
            ),
            'references': (EntrySource(target='references', fields=REFERENCE_FIELDS),),
            'files': (EntrySource(target='files', fields=FILE_FIELDS),),
            'trajectories': (EntrySource(target='trajectories', fields=TRAJECTORY_FIELDS),),
        },
        field_handlers=field_handlers,
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
        ],
        schema_url="https://schemas.optimade.org/openapi/v1.3/optimade.json",
    )

    serve(make_adapter(), config, port=8080, debug=True)
