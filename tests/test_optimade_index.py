from typing import Any

import pytest
from starlette.testclient import TestClient

from httk.serve import ASGIAppMount, compose_asgi_apps
from httk.serve.optimade import OptimadeConfig, OptimadeIndexConfig, create_asgi_app, create_index_asgi_app
from httk.serve.optimade.endpoints.info import generate_info_endpoint_reply
from httk.serve.optimade.engine.validate import validate_optimade_request
from httk.serve.optimade.model import RawRequest
from httk.serve.optimade.schema.served import build_served_schema


def link(link_id: str, link_type: str) -> dict[str, Any]:
    return {
        "id": link_id,
        "name": link_id,
        "description": link_id + " description",
        "base_url": "https://example.org/" + link_id,
        "homepage": "https://example.org/" + link_id + "/home",
        "link_type": link_type,
    }


def index_config(*, default: str | None = "amdb", cors: tuple[str, ...] = ()) -> OptimadeIndexConfig:
    return OptimadeIndexConfig(
        links=[link("index", "root"), link("amdb", "child")],
        default_link_id=default,
        cors_origins=cors,
    )


def test_index_info_and_links_are_query_free_and_have_exact_discovery() -> None:
    app = create_index_asgi_app(index_config(), baseurl="https://public.example/optimade/")

    with TestClient(app) as client:
        info = client.get("/v1/info")
        links = client.get("/v1.3.0/links")
        versions = client.get("/versions")

    assert info.status_code == 200
    attributes = info.json()["data"]["attributes"]
    assert attributes["is_index"] is True
    assert attributes["entry_types_by_format"] == {"json": []}
    assert attributes["available_endpoints"] == ["info", "links"]
    assert [item["url"] for item in attributes["available_api_versions"]] == [
        "https://public.example/optimade/v1",
        "https://public.example/optimade/v1.3",
        "https://public.example/optimade/v1.3.0",
    ]
    assert info.json()["data"]["relationships"] == {"default": {"data": {"type": "links", "id": "amdb"}}}
    assert len(links.json()["data"]) == links.json()["meta"]["data_returned"] == 3
    assert versions.text == "version\n1\n"


def test_ordinary_factory_rejects_index_config() -> None:
    with pytest.raises(TypeError, match="use create_index_asgi_app"):
        create_asgi_app(object(), index_config())  # type: ignore[arg-type]


@pytest.mark.parametrize("endpoint", ["info", "links"])
@pytest.mark.parametrize("alias", ["v1", "v1.3", "v1.3.0"])
def test_index_serves_all_version_aliases(endpoint: str, alias: str) -> None:
    with TestClient(create_index_asgi_app(index_config())) as client:
        assert client.get(f"/{alias}/{endpoint}").status_code == 200


@pytest.mark.parametrize(
    "path", ["/", "/structures", "/info/structures", "/partial_data/structures/a/x", "/v1/versions"]
)
def test_index_rejects_non_index_endpoints(path: str) -> None:
    with TestClient(create_index_asgi_app(index_config())) as client:
        response = client.get(path)
    assert response.status_code == 404
    assert response.json()["errors"][0]["status"] == 404


def test_index_default_relationship_can_be_null() -> None:
    with TestClient(create_index_asgi_app(index_config(default=None))) as client:
        assert client.get("/info").json()["data"]["relationships"] == {"default": {"data": None}}


def test_index_info_is_mount_aware_and_cors_uses_existing_runtime() -> None:
    index = create_index_asgi_app(index_config(cors=("https://table.example",)))
    app = compose_asgi_apps([ASGIAppMount("/optimade/index", index)])

    with TestClient(app, base_url="http://testserver") as client:
        response = client.get("/optimade/index/v1/info", headers={"Origin": "https://table.example"})

    assert response.status_code == 200
    assert [item["url"] for item in response.json()["data"]["attributes"]["available_api_versions"]] == [
        "http://testserver/optimade/index/v1",
        "http://testserver/optimade/index/v1.3",
        "http://testserver/optimade/index/v1.3.0",
    ]
    assert response.headers["access-control-allow-origin"] == "https://table.example"


def test_normal_config_remains_non_index_without_relationships() -> None:
    request = RawRequest(baseurl="http://testserver/", representation="/info")
    schema = build_served_schema({})
    reply = generate_info_endpoint_reply(validate_optimade_request(request, "1.3.0", schema), OptimadeConfig(), schema)
    assert reply["data"]["attributes"]["is_index"] is False
    assert "relationships" not in reply["data"]


@pytest.mark.parametrize(
    "links",
    [
        [],
        [link("one", "root"), link("two", "root")],
        [link("one", "child")],
        [link("one", "root"), {"id": "two"}],
        [link("one", "root"), link("one", "child")],
    ],
)
def test_index_config_validates_links(links: list[dict[str, Any]]) -> None:
    with pytest.raises(ValueError):
        OptimadeIndexConfig(links=links)


def test_index_config_requires_default_to_be_a_child() -> None:
    with pytest.raises(ValueError):
        OptimadeIndexConfig(links=[link("one", "root")], default_link_id="one")


def test_index_config_preserves_json_api_url_shapes() -> None:
    root = link("one", "root")
    root["base_url"] = {
        "href": "https://example.org/index",
        "meta": {"aggregate": "ok"},
        "extra": "preserved",
    }
    root["homepage"] = {"href": "https://example.org/home"}
    config = OptimadeIndexConfig(links=[root])
    assert config.links[0]["base_url"] == {
        "href": "https://example.org/index",
        "meta": {"aggregate": "ok"},
        "extra": "preserved",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", 1),
        ("description", None),
        ("base_url", ""),
        ("base_url", {"href": ""}),
        ("base_url", {"meta": {}}),
        ("base_url", {"href": "https://example.org", "meta": "bad"}),
        ("homepage", 1),
        ("homepage", {"href": "https://example.org", "meta": []}),
    ],
)
def test_index_config_validates_mandatory_link_value_shapes(field: str, value: object) -> None:
    root = link("one", "root")
    root[field] = value
    with pytest.raises(ValueError):
        OptimadeIndexConfig(links=[root])
