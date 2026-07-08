import json
from typing import Any

import pytest
from starlette.testclient import TestClient

from httk.optimade import BackendAdapter, EntrySource, OptimadeConfig, OptimadeError, RawRequest, create_asgi_app
from httk.optimade.backend.partial import PartialDimension, PartialValue
from httk.optimade.engine.validate import validate_optimade_request
from httk.optimade.model import RequestedSlice, ValidatedParameters
from httk.optimade.schema.served import build_served_schema

from fake_backend import FakeStore


def make_request(representation: str) -> RawRequest:
    return RawRequest(baseurl="http://localhost/", representation=representation)


def _schema() -> Any:
    return build_served_schema(
        {"structures": ["id", "type", "nelements", "cartesian_site_positions"]},
        default_response_overrides={"structures": ["nelements", "cartesian_site_positions"]},
    )


# --- dimension_slices parsing -------------------------------------------------


def test_dimension_slices_fully_formed() -> None:
    schema = _schema()
    validated = validate_optimade_request(
        make_request("/structures/demo-1?dimension_slices=dim_sites[3:5:2]"), "1.3.0", schema
    )
    assert validated.query.dimension_slices == {"dim_sites": RequestedSlice(start=3, stop=5, step=2)}


def test_dimension_slices_defaults_all_none() -> None:
    schema = _schema()
    validated = validate_optimade_request(
        make_request("/structures/demo-1?dimension_slices=dim_sites[::]"), "1.3.0", schema
    )
    assert validated.query.dimension_slices == {"dim_sites": RequestedSlice(start=None, stop=None, step=None)}


def test_dimension_slices_start_only() -> None:
    schema = _schema()
    validated = validate_optimade_request(
        make_request("/structures/demo-1?dimension_slices=dim_sites[0::]"), "1.3.0", schema
    )
    assert validated.query.dimension_slices == {"dim_sites": RequestedSlice(start=0, stop=None, step=None)}


def test_dimension_slices_multiple() -> None:
    schema = _schema()
    validated = validate_optimade_request(
        make_request("/structures/demo-1?dimension_slices=dim_sites[1:3:],dim_spatial[::]"), "1.3.0", schema
    )
    assert validated.query.dimension_slices == {
        "dim_sites": RequestedSlice(start=1, stop=3, step=None),
        "dim_spatial": RequestedSlice(start=None, stop=None, step=None),
    }


@pytest.mark.parametrize(
    "value",
    [
        "dim_sites[1:3]",  # only one colon
        "dim_sites[1:3:2",  # missing bracket
        "dim_sites",  # no slice
        "dim_sites[a:b:c]",  # non-integer
        "dim_sites[-1::]",  # negative
        "[1:2:]",  # no name
    ],
)
def test_dimension_slices_malformed_400(value: str) -> None:
    schema = _schema()
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(make_request("/structures/demo-1?dimension_slices=" + value), "1.3.0", schema)
    assert excinfo.value.response_code == 400


def test_dimension_slices_step_zero_400() -> None:
    schema = _schema()
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(
            make_request("/structures/demo-1?dimension_slices=dim_sites[::0]"), "1.3.0", schema
        )
    assert excinfo.value.response_code == 400


def test_dimension_slices_on_listing_400() -> None:
    schema = _schema()
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(
            make_request("/structures?dimension_slices=dim_sites[1:3:]"), "1.3.0", schema
        )
    assert excinfo.value.response_code == 400


def test_dimension_slices_empty_value_ignored() -> None:
    schema = _schema()
    validated = validate_optimade_request(make_request("/structures/demo-1?dimension_slices="), "1.3.0", schema)
    assert validated.query.dimension_slices == {}


def test_dimension_slices_round_trips_via_as_query_dict() -> None:
    params = ValidatedParameters(
        dimension_slices={
            "dim_sites": RequestedSlice(start=1, stop=3, step=None),
            "dim_spatial": RequestedSlice(start=None, stop=None, step=None),
        }
    )
    query = params.as_query_dict()
    assert query["dimension_slices"] == "dim_sites[1:3:],dim_spatial[::]"


# --- partial_data route parsing -----------------------------------------------


def test_partial_data_route_parsed() -> None:
    schema = _schema()
    validated = validate_optimade_request(
        make_request("/partial_data/structures/demo-1/cartesian_site_positions?offset=4"), "1.3.0", schema
    )
    assert validated.endpoint == "partial_data"
    assert validated.partial_data_parts == ("structures", "demo-1", "cartesian_site_positions")
    assert validated.partial_data_offset == 4


def test_partial_data_bad_property_404() -> None:
    schema = _schema()
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(make_request("/partial_data/structures/demo-1/not_a_prop"), "1.3.0", schema)
    assert excinfo.value.response_code == 404


def test_partial_data_bad_entry_404() -> None:
    schema = _schema()
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(make_request("/partial_data/bogus/demo-1/id"), "1.3.0", schema)
    assert excinfo.value.response_code == 404


def test_partial_data_bad_offset_400() -> None:
    schema = _schema()
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(
            make_request("/partial_data/structures/demo-1/cartesian_site_positions?offset=nope"), "1.3.0", schema
        )
    assert excinfo.value.response_code == 400


# --- end to end fixtures ------------------------------------------------------


class Row:
    def __init__(self, sid: str, positions: list[list[float]]) -> None:
        self.sid = sid
        self.positions = positions


def _partial_positions(row: Row) -> PartialValue:
    def fetch(slices: tuple[slice, ...]) -> Any:
        outer = row.positions[slices[0]]
        inner = slices[1] if len(slices) > 1 else slice(None)
        return [site[inner] for site in outer]

    return PartialValue(
        dimensions=(
            PartialDimension(name="dim_sites", length=len(row.positions), sliceable=True),
            PartialDimension(name="dim_spatial", length=3, sliceable=False),
        ),
        fetch=fetch,
    )


def make_client(chunk_size: int = 1000) -> TestClient:
    positions = [[float(i), float(i) + 0.1, float(i) + 0.2] for i in range(5)]
    store = FakeStore(rows_by_target={"structure-table": [Row("demo-1", positions)]})
    schema = _schema()
    fields: dict[str, Any] = {
        "type": lambda x: "structures",
        "id": lambda x: x.sid,
        "nelements": lambda x: 2,
        "cartesian_site_positions": _partial_positions,
    }
    adapter = BackendAdapter(
        store=store,
        sources={"structures": (EntrySource(target="structure-table", fields=fields),)},
        schema=schema,
    )
    config = OptimadeConfig()
    config.partial_data_chunk_size = chunk_size
    app = create_asgi_app(adapter, config, baseurl="http://testserver/")
    return TestClient(app, base_url="http://testserver")


# --- listing: null attribute + partial_data_links + list_axes -----------------


def test_listing_omits_partial_value_with_links() -> None:
    client = make_client()
    response = client.get("/structures")
    assert response.status_code == 200
    resource = response.json()["data"][0]
    assert resource["attributes"]["cartesian_site_positions"] is None
    links = resource["meta"]["partial_data_links"]["cartesian_site_positions"]
    assert links[0]["format"] == "jsonlines"
    assert links[0]["link"] == "http://testserver/partial_data/structures/demo-1/cartesian_site_positions"
    axes = resource["meta"]["property_metadata"]["cartesian_site_positions"]["list_axes"]
    assert axes[0] == {"dimension_name": "dim_sites", "length": 5, "sliceable": True}
    assert axes[1] == {"dimension_name": "dim_spatial", "length": 3, "sliceable": False}


# --- single entry with dimension_slices: inline sliced values -----------------


def test_single_entry_dimension_slices_inline() -> None:
    client = make_client()
    response = client.get(
        "/structures/demo-1", params={"dimension_slices": "dim_sites[1:3:]"}
    )
    assert response.status_code == 200
    resource = response.json()["data"]
    # Inclusive stop: indices 1, 2, 3.
    positions = resource["attributes"]["cartesian_site_positions"]
    assert positions == [[1.0, 1.1, 1.2], [2.0, 2.1, 2.2], [3.0, 3.1, 3.2]]
    assert "partial_data_links" not in resource["meta"]
    axes = resource["meta"]["property_metadata"]["cartesian_site_positions"]["list_axes"]
    assert axes[0]["requested_slice"] == {"start": 1, "stop": 3}


def test_single_entry_slice_non_sliceable_501() -> None:
    client = make_client()
    response = client.get("/structures/demo-1", params={"dimension_slices": "dim_spatial[0:1:]"})
    assert response.status_code == 501


# --- JSON Lines partial data endpoint -----------------------------------------


def _parse_jsonlines(text: str) -> list[Any]:
    return [json.loads(line) for line in text.strip().split("\n")]


def test_partial_data_endpoint_streams_jsonlines() -> None:
    client = make_client()
    response = client.get("/partial_data/structures/demo-1/cartesian_site_positions")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/jsonlines")
    lines = _parse_jsonlines(response.text)
    header = lines[0]
    assert header["optimade-partial-data"] == {"format": "1.2"}
    assert header["layout"] == "dense"
    assert header["returned_ranges"] == [{"start": 0, "stop": 4, "step": 1}]
    assert header["property_name"] == "cartesian_site_positions"
    assert header["entry"] == {"id": "demo-1", "type": "structures"}
    # 5 data lines then the end marker.
    assert lines[1] == [0.0, 0.1, 0.2]
    assert lines[-1] == ["PARTIAL-DATA-END", [""]]
    assert len(lines) == 1 + 5 + 1


def test_partial_data_endpoint_chunking_next_marker() -> None:
    client = make_client(chunk_size=2)
    response = client.get("/partial_data/structures/demo-1/cartesian_site_positions")
    lines = _parse_jsonlines(response.text)
    assert lines[0]["returned_ranges"] == [{"start": 0, "stop": 1, "step": 1}]
    assert len(lines) == 1 + 2 + 1
    assert lines[-1] == [
        "PARTIAL-DATA-NEXT",
        ["http://testserver/partial_data/structures/demo-1/cartesian_site_positions?offset=2"],
    ]


def test_partial_data_endpoint_last_chunk_end_marker() -> None:
    client = make_client(chunk_size=2)
    response = client.get(
        "/partial_data/structures/demo-1/cartesian_site_positions", params={"offset": 4}
    )
    lines = _parse_jsonlines(response.text)
    # Only one item remains (index 4) and then the end marker.
    assert lines[0]["returned_ranges"] == [{"start": 4, "stop": 4, "step": 1}]
    assert len(lines) == 1 + 1 + 1
    assert lines[-1] == ["PARTIAL-DATA-END", [""]]


def test_partial_data_endpoint_bad_id_404() -> None:
    # An empty store yields no row, so the endpoint reports the entry missing.
    store = FakeStore(rows_by_target={"structure-table": []})
    schema = _schema()
    fields: dict[str, Any] = {
        "type": lambda x: "structures",
        "id": lambda x: x.sid,
        "nelements": lambda x: 2,
        "cartesian_site_positions": _partial_positions,
    }
    adapter = BackendAdapter(
        store=store,
        sources={"structures": (EntrySource(target="structure-table", fields=fields),)},
        schema=schema,
    )
    app = create_asgi_app(adapter, baseurl="http://testserver/")
    client = TestClient(app, base_url="http://testserver")
    response = client.get("/partial_data/structures/nonexistent/cartesian_site_positions")
    assert response.status_code == 404


def test_partial_data_endpoint_non_partial_property_404() -> None:
    client = make_client()
    response = client.get("/partial_data/structures/demo-1/nelements")
    assert response.status_code == 404


def test_partial_data_links_percent_encode_ids() -> None:
    # Entry ids may contain any printable ASCII (including '?', '#', spaces);
    # partial-data links must percent-encode them to stay valid URLs, and the
    # encoded link must round-trip through the ASGI route.
    positions = [[float(i)] for i in range(3)]
    store = FakeStore(rows_by_target={"structure-table": [Row("demo 1?x", positions)]})
    fields: dict[str, Any] = {
        "type": lambda x: "structures",
        "id": lambda x: x.sid,
        "nelements": lambda x: 2,
        "cartesian_site_positions": _partial_positions,
    }
    adapter = BackendAdapter(
        store=store,
        sources={"structures": (EntrySource(target="structure-table", fields=fields),)},
        schema=_schema(),
    )
    app = create_asgi_app(adapter, OptimadeConfig(), baseurl="http://testserver/")
    client = TestClient(app, base_url="http://testserver")

    listing = client.get("/structures").json()
    link = listing["data"][0]["meta"]["partial_data_links"]["cartesian_site_positions"][0]["link"]
    assert "demo%201%3Fx" in link
    assert "?" not in link.replace("%3F", "")

    response = client.get(link)
    assert response.status_code == 200
    header = json.loads(response.text.splitlines()[0])
    assert header["entry"]["id"] == "demo 1?x"


def test_partial_data_offset_beyond_length_is_wellformed() -> None:
    # An offset at or past the end used to emit returned_ranges with
    # stop < start; the range must be omitted for an empty chunk.
    client = make_client()
    response = client.get("/partial_data/structures/demo-1/cartesian_site_positions?offset=99")
    assert response.status_code == 200
    lines = response.text.splitlines()
    header = json.loads(lines[0])
    assert "returned_ranges" not in header
    assert json.loads(lines[-1]) == ["PARTIAL-DATA-END", [""]]
    assert len(lines) == 2
