import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import pytest
from fake_backend import FakeStore
from materials_fixtures import materials_field_handlers, materials_schema
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from httk.optimade import BackendAdapter, EntrySource, OptimadeConfig, create_asgi_app


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

SUPPORTED_URL_ALIASES = ("v1", "v1.3", "v1.3.0")


def make_app(
    nstructures: int = 3,
    *,
    config: OptimadeConfig | None = None,
    baseurl: str | None = "http://testserver/",
) -> Starlette:
    rows = [Row(sid=f"s{i}", nelements=(i % 2) + 1) for i in range(nstructures)]
    store = FakeStore(rows_by_target={"structure-table": rows, "calc-table": []})
    adapter = BackendAdapter(
        store=store,
        sources={
            "structures": (EntrySource(target="structure-table", fields=STRUCTURE_FIELDS),),
            "calculations": (EntrySource(target="calc-table", fields={}),),
        },
        schema=materials_schema(),
        field_handlers=materials_field_handlers(),
    )
    return create_asgi_app(adapter, config, baseurl=baseurl)


def make_client(nstructures: int = 3) -> TestClient:
    return TestClient(make_app(nstructures), base_url="http://testserver")


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
    assert payload["meta"]["api_version"] == "1.3.0"


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


@pytest.mark.parametrize(
    ("endpoint", "expected_path"),
    [
        ("/structures", "/optimade/structures"),
        ("/v1/structures", "/optimade/v1/structures"),
    ],
)
def test_mounted_pagination_links_preserve_mount_and_url_version(endpoint: str, expected_path: str) -> None:
    child = make_app(5, baseurl=None)
    parent = Starlette(routes=[Mount("/optimade", child)])
    with TestClient(parent, base_url="http://testserver") as client:
        response = client.get("/optimade" + endpoint, params={"page_limit": "2"})
        assert response.status_code == 200
        next_link = response.json()["links"]["next"]
        assert urlsplit(next_link).path == expected_path

        next_response = client.get(next_link)
        assert next_response.status_code == 200
        assert [item["id"] for item in next_response.json()["data"]] == ["s2", "s3"]


@pytest.mark.parametrize(
    ("baseurl", "expected"),
    [
        ("https://public.example/optimade/", "https://public.example/optimade/v1/structures?"),
        ("https://public.example/v1/", "https://public.example/v1/v1/structures?"),
        ("https://public.example/outer/v1/", "https://public.example/outer/v1/v1/structures?"),
    ],
)
def test_mounted_versioned_request_keeps_explicit_baseurl_authoritative(baseurl: str, expected: str) -> None:
    child = make_app(5, baseurl=baseurl)
    parent = Starlette(routes=[Mount("/optimade", child)])
    with TestClient(parent, base_url="http://testserver") as client:
        response = client.get("/optimade/v1/structures", params={"page_limit": "2"})

    assert response.status_code == 200
    assert response.json()["links"]["next"].startswith(expected)


def test_mounted_versioned_info_advertises_the_mounted_version_base() -> None:
    child = make_app(baseurl=None)
    parent = Starlette(routes=[Mount("/optimade", child)])
    with TestClient(parent, base_url="http://testserver") as client:
        response = client.get("/optimade/v1/info")

    assert response.status_code == 200
    versions = response.json()["data"]["attributes"]["available_api_versions"]
    urls = [version["url"] for version in versions]
    assert urls[0] == "http://testserver/optimade/v1"
    assert all(url.startswith("http://testserver/optimade/") for url in urls)
    assert all("/v1/v" not in url for url in urls)


@pytest.mark.parametrize("mount", ["/v1", "/outer/v1"])
@pytest.mark.parametrize("request_alias", SUPPORTED_URL_ALIASES)
def test_version_named_mount_preserves_mount_and_request_alias(mount: str, request_alias: str) -> None:
    child = make_app(5, baseurl=None)
    parent = Starlette(routes=[Mount(mount, child)])
    with TestClient(parent, base_url="http://testserver") as client:
        response = client.get(f"{mount}/{request_alias}/structures", params={"page_limit": "2"})
        assert response.status_code == 200
        next_link = response.json()["links"]["next"]
        assert urlsplit(next_link).path == f"{mount}/{request_alias}/structures"

        next_response = client.get(next_link)
        assert next_response.status_code == 200
        assert [item["id"] for item in next_response.json()["data"]] == ["s2", "s3"]

        info_response = client.get(f"{mount}/{request_alias}/info")
        assert info_response.status_code == 200
        advertised = info_response.json()["data"]["attributes"]["available_api_versions"]
        assert [version["url"] for version in advertised] == [
            f"http://testserver{mount}/{alias}" for alias in SUPPORTED_URL_ALIASES
        ]


def test_mounted_root_path_preserves_interior_repeated_slashes() -> None:
    mount = "/outer//api"
    child = make_app(5, baseurl=None)
    parent = Starlette(routes=[Mount(mount, child)])
    with TestClient(parent, base_url="http://testserver") as client:
        response = client.get(mount + "/v1/structures", params={"page_limit": "2"})
        assert response.status_code == 200
        next_link = response.json()["links"]["next"]
        assert urlsplit(next_link).path == mount + "/v1/structures"

        next_response = client.get(next_link)
        assert next_response.status_code == 200
        assert [item["id"] for item in next_response.json()["data"]] == ["s2", "s3"]


def test_cors_is_disabled_by_default() -> None:
    origin = "https://table.example"
    plain = make_client()
    plain_response = plain.get("/structures", headers={"Origin": origin})
    assert "access-control-allow-origin" not in plain_response.headers


@pytest.mark.parametrize("method", ["get", "head"])
@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        ("https://table.example", True),
        ("https://other.example", False),
    ],
)
def test_cors_simple_responses_vary_for_every_origin(method: str, origin: str, allowed: bool) -> None:
    config = OptimadeConfig(cors_origins=("HTTPS://TABLE.EXAMPLE:443/",))
    client = TestClient(make_app(config=config), base_url="http://testserver")
    response = getattr(client, method)("/structures", headers={"Origin": origin})

    assert response.status_code == 200
    assert response.headers["vary"] == "Origin"
    if allowed:
        assert response.headers["access-control-allow-origin"] == origin
    else:
        assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers


def test_cors_preflight_allows_only_configured_origin_and_safe_get_header() -> None:
    origin = "https://table.example"
    client = TestClient(make_app(config=OptimadeConfig(cors_origins=(origin,))), base_url="http://testserver")
    headers = {
        "Origin": origin,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Accept",
    }
    accepted = client.options("/structures", headers=headers)
    assert accepted.status_code == 200
    assert accepted.headers["access-control-allow-origin"] == origin
    assert accepted.headers["vary"] == "Origin"
    assert "GET" in accepted.headers["access-control-allow-methods"]
    assert "access-control-allow-credentials" not in accepted.headers

    rejected = client.options("/structures", headers={**headers, "Origin": "https://other.example"})
    assert rejected.status_code == 400
    assert rejected.headers["vary"] == "Origin"
    assert "access-control-allow-origin" not in rejected.headers
    assert "access-control-allow-credentials" not in rejected.headers


def test_cors_origin_normalizes_internationalized_hosts_like_a_browser() -> None:
    browser_origin = "https://xn--bcher-kva.example"
    client = TestClient(
        make_app(config=OptimadeConfig(cors_origins=("HTTPS://BÜCHER.EXAMPLE:443/",))),
        base_url="http://testserver",
    )

    response = client.get("/structures", headers={"Origin": browser_origin})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == browser_origin
    assert response.headers["vary"] == "Origin"


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "https://*.example",
        "https://table.example/path",
        "https://user@table.example",
        "https://table.example?query=value",
        "https://table.example#fragment",
        "https://table.example\\bad",
    ],
)
def test_invalid_cors_origin_fails_when_application_is_created(origin: str) -> None:
    with pytest.raises(ValueError):
        make_app(config=OptimadeConfig(cors_origins=(origin,)))


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
