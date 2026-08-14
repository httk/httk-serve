"""Coverage for the lightweight live JSON and JSON-LD GET applications."""

import pytest
from starlette.testclient import TestClient

from httk.serve.http import json_get_app, jsonld_get_app


def test_jsonld_get_head_accept_cache_cors_and_profile() -> None:
    document = {
        "@context": {"name": "http://schema.org/name"},
        "@id": "https://example.test/catalogue",
        "name": "Example catalogue",
    }
    app = jsonld_get_app(
        document,
        path="/catalogue/",
        media_type='application/ld+json; profile="https://example.test/profiles/catalogue"',
        profile="https://example.test/protocols/public-get",
    )

    with TestClient(app) as client:
        response = client.get("/catalogue/", headers={"Accept": "application/ld+json"})
        assert response.status_code == 200
        assert response.json() == document
        assert response.headers["content-type"] == (
            'application/ld+json; profile="https://example.test/profiles/catalogue"'
        )
        assert response.headers["cache-control"] == "public, max-age=60"
        assert response.headers["access-control-allow-origin"] == "*"
        assert response.headers["vary"] == "Accept"
        assert response.headers["link"] == '<https://example.test/protocols/public-get>; rel="profile"'

        head = client.head("/catalogue/")
        assert head.status_code == 200
        assert head.content == b""
        assert head.headers["content-length"] == response.headers["content-length"]
        assert head.headers["etag"] == response.headers["etag"]

        cached = client.get(
            "/catalogue/",
            headers={"If-None-Match": f'W/{response.headers["etag"]}'},
        )
        assert cached.status_code == 304
        assert client.get("/catalogue/", headers={"Accept": "text/turtle"}).status_code == 406
        assert client.get("/catalogue/", headers={"Accept": "application/ld+json;q=0"}).status_code == 406
        assert client.get("/catalogue/", headers={"Accept": "application/ld+json;q=0.5"}).status_code == 200
        for fallback in ("application/*", "*/*"):
            assert (
                client.get(
                    "/catalogue/",
                    headers={"Accept": f"application/ld+json;q=0, {fallback};q=1"},
                ).status_code
                == 406
            )
        assert (
            client.get(
                "/catalogue/",
                headers={"Accept": "application/ld+json;q=not-a-quality"},
            ).status_code
            == 406
        )


def test_document_factories_are_live_and_may_be_async() -> None:
    state = {"generation": 1}

    async def document() -> dict[str, object]:
        return {"@context": {}, "generation": state["generation"]}

    app = jsonld_get_app(document)
    with TestClient(app) as client:
        first = client.get("/")
        state["generation"] = 2
        second = client.get("/")

    assert first.json()["generation"] == 1
    assert second.json()["generation"] == 2
    assert first.headers["etag"] != second.headers["etag"]


def test_accept_parsing_supports_quoted_separators_and_rejects_duplicates() -> None:
    media_type = 'application/ld+json; profile="https://example.test/p;a,t"'
    app = jsonld_get_app({"@context": {}}, media_type=media_type)
    with TestClient(app) as client:
        accepted = client.get("/", headers={"Accept": media_type})
        duplicate = client.get(
            "/",
            headers={"Accept": 'application/ld+json;profile="bad";profile="https://example.test/p;a,t"'},
        )
    assert accepted.status_code == 200
    assert duplicate.status_code == 406


@pytest.mark.parametrize("path", ["catalogue", "//catalogue", "/a//b", "/a/../b", "/a%2fb", "/a?b"])
def test_jsonld_app_rejects_noncanonical_paths(path: str) -> None:
    with pytest.raises(ValueError, match="canonical"):
        jsonld_get_app({"@context": {}}, path=path)


def test_jsonld_app_rejects_non_jsonld_media_type_and_bad_source() -> None:
    with pytest.raises(ValueError, match=r"application/ld\+json"):
        jsonld_get_app({"@context": {}}, media_type="application/json")
    with pytest.raises(TypeError, match="mapping"):
        jsonld_get_app([{"@context": {}}])  # type: ignore[arg-type]


@pytest.mark.parametrize("media_type", ["application/json", "application/activity+json"])
def test_json_get_app_serves_general_json_media_types(media_type: str) -> None:
    document = {"services": [{"endpoint": "https://example.test/catalogue"}]}
    app = json_get_app(document, media_type=media_type)
    with TestClient(app) as client:
        response = client.get("/", headers={"Accept": media_type})
        cached = client.get("/", headers={"If-None-Match": f'W/{response.headers["etag"]}, "other"'})
    assert response.status_code == 200
    assert response.json() == document
    assert response.headers["content-type"] == media_type
    assert cached.status_code == 304


@pytest.mark.parametrize("media_type", ["text/json", "application/xml", "application/not-json"])
def test_json_get_app_rejects_non_json_media_types(media_type: str) -> None:
    with pytest.raises(ValueError, match="JSON media type|application/json"):
        json_get_app({}, media_type=media_type)
