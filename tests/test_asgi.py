import json
from dataclasses import dataclass
from typing import Any

from starlette.testclient import TestClient

from httk.optimade import BackendAdapter, EntrySource, create_asgi_app

from fake_backend import FakeStore


@dataclass
class Row:
    sid: str
    nelements: int


STRUCTURE_FIELDS: dict[str, Any] = {
    "type": lambda x: "structures",
    "id": lambda x: x.sid,
    "nelements": lambda x: x.nelements,
    "elements": lambda x: ["Ga", "Ti"][: x.nelements],
    "chemical_formula_descriptive": lambda x: "GaTi",
    "chemical_formula_reduced": lambda x: "GaTi",
    "chemical_formula_anonymous": lambda x: "AB",
    "dimension_types": lambda x: [1, 1, 1],
    "nperiodic_dimensions": lambda x: 3,
    "lattice_vectors": lambda x: [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
    "structure_features": lambda x: [],
    "nsites": lambda x: 1,
    "species_at_sites": lambda x: ["Ga"],
    "cartesian_site_positions": lambda x: [[0.0, 0.0, 0.0]],
}


def make_client(nstructures: int = 3) -> TestClient:
    rows = [Row(sid=f"s{i}", nelements=(i % 2) + 1) for i in range(nstructures)]
    store = FakeStore(rows_by_target={"structure-table": rows, "calc-table": []})
    adapter = BackendAdapter(
        store=store,
        sources={
            "structures": (EntrySource(target="structure-table", fields=STRUCTURE_FIELDS),),
            "calculations": (EntrySource(target="calc-table", fields={}),),
        },
    )
    app = create_asgi_app(adapter, baseurl="http://testserver/")
    return TestClient(app, base_url="http://testserver")


def test_base_endpoint_html() -> None:
    client = make_client()
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "OPTIMADE" in response.text


def test_versions_endpoint_csv() -> None:
    client = make_client()
    response = client.get("/versions")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.text == "version\n1\n"


def test_info_endpoint() -> None:
    client = make_client()
    response = client.get("/v1/info")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.api+json"
    payload = response.json()
    assert payload["data"]["type"] == "info"
    assert payload["meta"]["api_version"] == "1.0.0"


def test_structures_endpoint() -> None:
    client = make_client()
    response = client.get("/structures")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["data"]) == 3
    assert payload["meta"]["data_available"] == 3
    assert payload["meta"]["data_returned"] == 3
    assert payload["links"]["next"] is None
    entry = payload["data"][0]
    assert entry["type"] == "structures"
    assert "nelements" in entry["attributes"]


def test_structures_endpoint_with_filter() -> None:
    client = make_client()
    response = client.get("/structures", params={"filter": "nelements=2"})
    assert response.status_code == 200
    payload = response.json()
    # The fake store does not evaluate expressions, but the filter must
    # translate without error and produce a well-formed reply.
    assert isinstance(payload["data"], list)


def test_structures_pagination_next_link() -> None:
    client = make_client(nstructures=5)
    response = client.get("/structures", params={"page_limit": "2"})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["data"]) == 2
    assert payload["meta"]["more_data_available"] is True
    next_link = payload["links"]["next"]
    assert next_link.startswith("http://testserver/structures?")
    assert "page_offset=2" in next_link

    response2 = client.get(next_link)
    assert response2.status_code == 200
    payload2 = response2.json()
    assert [d["id"] for d in payload2["data"]] == ["s2", "s3"]


def test_single_structure_endpoint() -> None:
    # The fake store does not evaluate the id filter, so use a single-row store;
    # a real backend would return just the matching entry.
    client = make_client(nstructures=1)
    response = client.get("/structures/s0")
    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["id"] == "s0"
    assert payload["meta"]["data_returned"] == 1
    assert payload["links"]["next"] is None


def test_response_fields_restriction() -> None:
    client = make_client()
    response = client.get("/structures", params={"response_fields": "elements"})
    assert response.status_code == 200
    payload = response.json()
    attributes = payload["data"][0]["attributes"]
    assert "elements" in attributes
    assert "nelements" not in attributes


def test_unknown_endpoint_is_jsonapi_error() -> None:
    client = make_client()
    response = client.get("/nosuch")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/vnd.api+json"
    payload = response.json()
    assert payload["errors"][0]["status"] == 404
    assert "meta" in payload


def test_unsupported_version_553() -> None:
    client = make_client()
    response = client.get("/v9/info")
    assert response.status_code == 553
    payload = response.json()
    assert payload["errors"][0]["status"] == 553


def test_bad_filter_400() -> None:
    client = make_client()
    response = client.get("/structures", params={"filter": "elements HAS"})
    assert response.status_code == 400
    payload = response.json()
    assert payload["errors"][0]["status"] == 400


def test_json_output_is_pretty_printed_and_sorted() -> None:
    client = make_client()
    response = client.get("/v1/info")
    parsed = json.loads(response.text)
    assert response.text == json.dumps(parsed, indent=4, separators=(",", ": "), sort_keys=True)
