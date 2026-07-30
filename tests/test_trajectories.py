import json
from typing import Any

from definition_fixtures import served_schema, structures_definition
from fake_backend import FakeStore
from starlette.testclient import TestClient

from httk.serve.optimade import BackendAdapter, EntrySource, OptimadeConfig, create_asgi_app
from httk.serve.optimade.backend import simple_property_handlers, translate_filter
from httk.serve.optimade.backend.partial import PartialDimension, PartialValue
from httk.serve.optimade.filter import parse_optimade_filter
from httk.serve.optimade.schema.trajectories import trajectories_entry_info

# The structures properties reused (frame-wrapped) for the trajectory.
STRUCTURE_PROPERTIES = [
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

TRAJECTORY_PROPERTIES = STRUCTURE_PROPERTIES + ['nframes', 'reference_frames']

DEFAULT_OVERRIDES = {
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

_CELL = [[5.0, 0.0, 0.0], [0.0, 5.0, 0.0], [0.0, 0.0, 5.0]]


def trajectories_schema() -> Any:
    return served_schema(
        {'trajectories': TRAJECTORY_PROPERTIES},
        extra_entry_info={'trajectories': trajectories_entry_info(structures_definition(), STRUCTURE_PROPERTIES)},
        default_response_overrides=DEFAULT_OVERRIDES,
    )


# --- Schema wrapping ----------------------------------------------------------


def test_id_and_type_stay_unwrapped() -> None:
    info = trajectories_entry_info(structures_definition(), STRUCTURE_PROPERTIES)
    properties = info['properties']
    assert properties['id']['fulltype'] == 'string'
    assert properties['type']['fulltype'] == 'string'
    assert 'trajectories' in properties['type']['description']
    assert 'dimensions' not in properties['id']
    assert 'dimensions' not in properties['type']


def test_elements_is_frame_wrapped() -> None:
    properties = trajectories_entry_info(structures_definition(), STRUCTURE_PROPERTIES)['properties']
    elements = properties['elements']
    assert elements['fulltype'] == 'list of list of string'
    dimensions = elements['dimensions']
    assert dimensions['names'][0] == 'dim_frames'
    assert dimensions['names'] == ['dim_frames', 'dim_elements_1']
    assert dimensions['sizes'] == [None, None]
    assert dimensions['compactable'] == ['constant', 'no']
    assert elements['description'].startswith('Frame-dependent (per dim_frames): ')


def test_lattice_vectors_wraps_declared_dimensions() -> None:
    properties = trajectories_entry_info(structures_definition(), STRUCTURE_PROPERTIES)['properties']
    lattice = properties['lattice_vectors']
    assert lattice['fulltype'] == 'list of list of list of float'
    assert lattice['dimensions'] == {
        'names': ['dim_frames', 'dim_lattice', 'dim_spatial'],
        'sizes': [None, 3, 3],
        'compactable': ['constant', 'no', 'no'],
    }


def test_cartesian_site_positions_wraps_open_ended_dimensions() -> None:
    properties = trajectories_entry_info(structures_definition(), STRUCTURE_PROPERTIES)['properties']
    positions = properties['cartesian_site_positions']
    assert positions['dimensions'] == {
        'names': ['dim_frames', 'dim_sites', 'dim_spatial'],
        'sizes': [None, None, 3],
        'compactable': ['constant', 'no', 'no'],
    }


def test_nframes_and_reference_frames_present() -> None:
    properties = trajectories_entry_info(structures_definition(), STRUCTURE_PROPERTIES)['properties']
    nframes = properties['nframes']
    assert nframes['fulltype'] == 'integer'
    assert nframes['required_support'] is True
    assert nframes['required_query'] is True
    assert nframes['default_response'] is True
    reference_frames = properties['reference_frames']
    assert reference_frames['fulltype'] == 'list of integer'
    assert reference_frames['required_support'] is False
    assert reference_frames['default_response'] is True
    assert 'last_modified' in properties


# --- Property definitions emit x-optimade-dimensions with dim_frames ----------


def test_info_trajectories_definition_has_dim_frames() -> None:
    schema = trajectories_schema()
    definition = schema.property_definitions['trajectories']['cartesian_site_positions']
    dimensions = definition['x-optimade-dimensions']
    assert dimensions['names'][0] == 'dim_frames'
    assert dimensions['compactable'][0] == 'constant'


# --- Filtering: nframes -------------------------------------------------------


def trajectories_adapter(store: FakeStore) -> BackendAdapter:
    schema = trajectories_schema()
    field_handlers = {
        'trajectories': simple_property_handlers(
            'trajectories', {'nframes': 'nframes'}, schema.entry_info['trajectories']
        )
    }
    return BackendAdapter(
        store=store,
        sources={'trajectories': (EntrySource(target='trajectories', fields=TRAJECTORY_FIELDS),)},
        field_handlers=field_handlers,
        schema=schema,
    )


def test_nframes_filter_translates_to_number_comparison() -> None:
    store = FakeStore(rows_by_target={'trajectories': []})
    adapter = trajectories_adapter(store)
    pairs = translate_filter(parse_optimade_filter('nframes>=5'), ['trajectories'], adapter)
    _source, searcher = pairs[0]
    assert searcher.expressions[0].tree == ("ge", ("column", "nframes"), 5)  # type: ignore[attr-defined]


# --- End to end fixtures ------------------------------------------------------


class TrajRow:
    def __init__(self, sid: str, nframes: int, reference_frames: list[int], positions: list[list[list[float]]]) -> None:
        self.sid = sid
        self.nframes = nframes
        self.reference_frames = reference_frames
        self.positions = positions


def _trajectory_positions(row: TrajRow) -> PartialValue:
    def fetch(slices: tuple[slice, ...]) -> Any:
        frame_slice, site_slice, spatial_slice = slices
        return [[site[spatial_slice] for site in frame[site_slice]] for frame in row.positions[frame_slice]]

    return PartialValue(
        dimensions=(
            PartialDimension('dim_frames', length=row.nframes, sliceable=True),
            PartialDimension('dim_sites', length=2),
            PartialDimension('dim_spatial', length=3),
        ),
        fetch=fetch,
    )


TRAJECTORY_FIELDS: dict[str, Any] = {
    'type': lambda x: "trajectories",
    'id': lambda x: x.sid,
    'nframes': lambda x: x.nframes,
    'reference_frames': lambda x: x.reference_frames,
    'elements': lambda x: [['Ga', 'Ti']],
    'nelements': lambda x: [2],
    'lattice_vectors': lambda x: [_CELL],
    'chemical_formula_descriptive': lambda x: ['GaTi'],
    'dimension_types': lambda x: [[1, 1, 1]],
    'species_at_sites': lambda x: [['Ga', 'Ti']],
    'cartesian_site_positions': _trajectory_positions,
}


def _positions() -> list[list[list[float]]]:
    return [[[0.0 + 0.01 * f, 0.0, 0.0], [0.5, 0.5, 0.5 + 0.01 * f]] for f in range(5)]


def make_client(chunk_size: int = 1000) -> TestClient:
    row = TrajRow('traj-1', 5, [0, 4], _positions())
    store = FakeStore(rows_by_target={'trajectories': [row]})
    adapter = trajectories_adapter(store)
    config = OptimadeConfig()
    config.partial_data_chunk_size = chunk_size
    app = create_asgi_app(adapter, config, baseurl="http://testserver/")
    return TestClient(app, base_url="http://testserver")


def test_e2e_nframes_filter_returns_trajectory() -> None:
    client = make_client()
    response = client.get("/trajectories", params={"filter": "nframes>=5"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert [d["id"] for d in data] == ["traj-1"]


def test_e2e_compact_constant_lists() -> None:
    client = make_client()
    response = client.get("/trajectories/traj-1")
    assert response.status_code == 200
    attributes = response.json()["data"]["attributes"]
    assert attributes["nelements"] == [2]
    assert attributes["elements"] == [["Ga", "Ti"]]
    assert attributes["lattice_vectors"] == [_CELL]
    assert attributes["nframes"] == 5
    assert attributes["reference_frames"] == [0, 4]


def test_e2e_positions_null_with_partial_data_links() -> None:
    client = make_client()
    response = client.get("/trajectories/traj-1")
    assert response.status_code == 200
    resource = response.json()["data"]
    assert resource["attributes"]["cartesian_site_positions"] is None
    links = resource["meta"]["partial_data_links"]["cartesian_site_positions"]
    assert links[0]["format"] == "jsonlines"
    assert links[0]["link"] == "http://testserver/partial_data/trajectories/traj-1/cartesian_site_positions"
    axes = resource["meta"]["property_metadata"]["cartesian_site_positions"]["list_axes"]
    assert len(axes) == 3
    assert axes[0] == {"dimension_name": "dim_frames", "length": 5, "sliceable": True}
    assert axes[1]["dimension_name"] == "dim_sites"
    assert axes[1]["sliceable"] is False


def test_e2e_dimension_slices_returns_inline_frames() -> None:
    client = make_client()
    response = client.get("/trajectories/traj-1", params={"dimension_slices": "dim_frames[1:3:]"})
    assert response.status_code == 200
    resource = response.json()["data"]
    positions = resource["attributes"]["cartesian_site_positions"]
    # Inclusive stop: frames 1, 2, 3.
    assert len(positions) == 3
    assert positions[0] == [[0.01, 0.0, 0.0], [0.5, 0.5, 0.51]]
    assert "partial_data_links" not in resource["meta"]
    axes = resource["meta"]["property_metadata"]["cartesian_site_positions"]["list_axes"]
    assert axes[0]["requested_slice"] == {"start": 1, "stop": 3}


def test_e2e_partial_data_endpoint_streams_frames() -> None:
    client = make_client()
    response = client.get("/partial_data/trajectories/traj-1/cartesian_site_positions")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/jsonlines")
    lines = [json.loads(line) for line in response.text.strip().split("\n")]
    header = lines[0]
    assert header["optimade-partial-data"] == {"format": "1.2"}
    assert header["property_name"] == "cartesian_site_positions"
    assert header["entry"] == {"id": "traj-1", "type": "trajectories"}
    assert header["returned_ranges"] == [{"start": 0, "stop": 4, "step": 1}]
    # 5 dense frame lines, then the end marker.
    assert lines[1] == [[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]]
    assert lines[-1] == ["PARTIAL-DATA-END", [""]]
    assert len(lines) == 1 + 5 + 1


def test_e2e_partial_data_endpoint_chunk_next_marker() -> None:
    client = make_client(chunk_size=2)
    response = client.get("/partial_data/trajectories/traj-1/cartesian_site_positions")
    lines = [json.loads(line) for line in response.text.strip().split("\n")]
    assert lines[0]["returned_ranges"] == [{"start": 0, "stop": 1, "step": 1}]
    assert len(lines) == 1 + 2 + 1
    assert lines[-1] == [
        "PARTIAL-DATA-NEXT",
        ["http://testserver/partial_data/trajectories/traj-1/cartesian_site_positions?offset=2"],
    ]
