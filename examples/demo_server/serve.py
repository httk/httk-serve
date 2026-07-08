"""A small OPTIMADE demo server over an in-memory dataset.

Run with::

    python examples/demo_server/serve.py

then query, e.g.::

    curl 'http://localhost:8080/structures?filter=elements HAS "Ga" AND nelements=2'
"""

from typing import Any

from inmemory_backend import InMemoryStore

from httk.optimade import BackendAdapter, EntrySource, OptimadeConfig, serve
from httk.optimade.backend import default_field_handlers, simple_property_handlers
from httk.optimade.backend.handlers import set_handler
from httk.optimade.backend.partial import PartialDimension, PartialValue
from httk.optimade.schema.served import build_served_schema
from httk.optimade.schema.trajectories import trajectories_entry_info


def structure(
    sid: str,
    formula: str,
    anonymous: str,
    species_at_sites: list[str],
    positions: list[list[float]],
    references: list[str] | None = None,
) -> dict[str, Any]:
    return {
        '__id': sid,
        'formula': formula,
        'anonymous_formula': anonymous,
        'formula_symbols': sorted(set(species_at_sites)),
        'number_of_elements': len(set(species_at_sites)),
        'species_at_sites': species_at_sites,
        'positions': positions,
        'references': references if references is not None else [],
    }


STRUCTURES = [
    structure('demo-1', 'GaTi', 'AB', ['Ga', 'Ti'], [[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]], references=['ref-1']),
    structure('demo-2', 'Si', 'A', ['Si'], [[0.0, 0.0, 0.0]]),
    structure(
        'demo-3',
        'SiO2',
        'AB2',
        ['Si', 'O', 'O'],
        [[0.0, 0.0, 0.0], [0.3, 0.3, 0.3], [0.6, 0.6, 0.6]],
        references=['ref-2'],
    ),
    structure('demo-4', 'GaAs', 'AB', ['Ga', 'As'], [[0.0, 0.0, 0.0], [0.25, 0.25, 0.25]]),
    structure('demo-5', 'NaCl', 'AB', ['Na', 'Cl'], [[0.0, 0.0, 0.0], [0.5, 0.0, 0.0]]),
]

CALCULATIONS = [
    {
        '__id': 'calc-1',
        'total_energy': -12.5,
        'structure': 'demo-1',
        'files': [('file-1', 'input'), ('file-2', 'output')],
        'file_ids': ['file-1', 'file-2'],
    },
    {'__id': 'calc-2', 'total_energy': -5.4, 'structure': 'demo-2', 'files': [], 'file_ids': []},
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
    },
    {
        '__id': 'file-2',
        'url': 'https://example.org/files/calc-1/OUTCAR',
        'name': 'OUTCAR',
        'size': 204800,
        'media_type': 'text/plain',
        'description': 'Output log file',
        'checksums': {'sha256': '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08'},
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
    },
    {
        '__id': 'ref-2',
        'title': 'Silicon dioxide polymorphs revisited',
        'journal': 'Demo Letters',
        'year': '2019',
        'doi': '10.1234/demo.2019.7',
        'authors': [{'name': 'Grace Hopper'}],
    },
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


def calculation_relationships(row: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    files = row.get('files') or []
    if not files:
        return {}
    return {'files': [{'id': fid, 'role': role} for fid, role in files]}


FILE_FIELDS = {
    'type': lambda x: "files",
    'id': lambda x: x['__id'],
    'url': lambda x: x['url'],
    'name': lambda x: x['name'],
    'size': lambda x: x['size'],
    'media_type': lambda x: x['media_type'],
    'description': lambda x: x['description'],
    'checksums': lambda x: x['checksums'],
}

# OPTIMADE property -> backend column, for the queryable file properties.
FILE_COLUMNS = {
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
    'title': lambda x: x['title'],
    'journal': lambda x: x['journal'],
    'year': lambda x: x['year'],
    'doi': lambda x: x['doi'],
    'authors': lambda x: x['authors'],
}

# OPTIMADE property -> backend column, for the queryable reference properties.
REFERENCE_COLUMNS = {
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

# OPTIMADE property -> backend column, for the queryable trajectory properties.
TRAJECTORY_COLUMNS = {
    'nframes': 'nframes',
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

REFERENCE_PROPERTIES = [
    'id',
    'type',
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

# OPTIMADE property -> backend column name, for the sortable structure properties.
STRUCTURE_SORT_COLUMNS = {
    'id': '__id',
    'nelements': 'number_of_elements',
    'chemical_formula_descriptive': 'formula',
}


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
    schema = build_served_schema(
        {
            'structures': STRUCTURE_PROPERTIES,
            'calculations': CALCULATION_PROPERTIES,
            'references': REFERENCE_PROPERTIES,
            'files': FILE_PROPERTIES,
            'trajectories': TRAJECTORY_PROPERTIES,
        },
        extra_entry_info={'trajectories': trajectories_entry_info(TRAJECTORY_STRUCTURE_PROPERTIES)},
        default_response_overrides=DEFAULT_RESPONSE_OVERRIDES,
        sortable={'structures': list(STRUCTURE_SORT_COLUMNS)},
    )
    field_handlers = default_field_handlers()
    field_handlers['references'] = simple_property_handlers(
        'references', REFERENCE_COLUMNS, schema.entry_info['references']
    )
    field_handlers['files'] = simple_property_handlers('files', FILE_COLUMNS, schema.entry_info['files'])
    field_handlers['trajectories'] = simple_property_handlers(
        'trajectories', TRAJECTORY_COLUMNS, schema.entry_info['trajectories']
    )
    # A relationship filter handler: `references.id HAS "ref-1"` matches over the
    # structure row's 'references' column (a list of related reference ids).
    structures_handlers = dict(field_handlers['structures'])
    structures_handlers['references.id'] = {
        'HAS': lambda entry, ops, values, sv, has_type, inv: set_handler('references', ops, values, inv, has_type, sv),
    }
    field_handlers['structures'] = structures_handlers
    # `files.id HAS "file-1"` matches over the calculation row's 'file_ids' column.
    calculations_handlers = dict(field_handlers['calculations'])
    calculations_handlers['files.id'] = {
        'HAS': lambda entry, ops, values, sv, has_type, inv: set_handler('file_ids', ops, values, inv, has_type, sv),
    }
    field_handlers['calculations'] = calculations_handlers
    return BackendAdapter(
        store=store,
        sources={
            'structures': (
                EntrySource(
                    target='structures',
                    fields=STRUCTURE_FIELDS,
                    sort_columns=STRUCTURE_SORT_COLUMNS,
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
        ]
    )

    serve(make_adapter(), config, port=8080, debug=True)
