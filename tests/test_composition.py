from contextlib import asynccontextmanager
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from httk.serve import ASGIAppMount, compose_asgi_apps


def service(name: str, events: list[str], *, fail: bool = False) -> Starlette:
    async def endpoint(_request: object) -> PlainTextResponse:
        return PlainTextResponse(name)

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        events.append(name + ":start")
        if fail:
            raise RuntimeError(name)
        try:
            yield
        finally:
            events.append(name + ":stop")

    return Starlette(routes=[Route("/{path:path}", endpoint)], lifespan=lifespan)


def stateful_service(name: str, state: dict[str, Any], events: list[str]) -> Starlette:
    async def endpoint(request: Request) -> JSONResponse:
        return JSONResponse({"name": name, "state": {key: getattr(request.state, key) for key in state}})

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        events.append(name + ":start")
        try:
            yield state
        finally:
            events.append(name + ":stop")

    return Starlette(routes=[Route("/{path:path}", endpoint)], lifespan=lifespan)


def test_composition_routes_specific_mounts_before_ancestors_and_falls_back_to_root() -> None:
    events: list[str] = []
    parent = compose_asgi_apps(
        [
            ASGIAppMount("/api", service("api", events)),
            ASGIAppMount("/api/v1", service("nested", events)),
        ],
        root=ASGIAppMount("/", service("root", events)),
    )

    with TestClient(parent) as client:
        assert client.get("/api/v1/item").text == "nested"
        assert client.get("/api/item").text == "api"
        assert client.get("/other").text == "root"

    assert events == [
        "nested:start",
        "api:start",
        "root:start",
        "root:stop",
        "api:stop",
        "nested:stop",
    ]


def test_composition_aliases_share_one_child_lifespan() -> None:
    events: list[str] = []
    child = service("aliased", events)
    parent = compose_asgi_apps([ASGIAppMount("/first", child), ASGIAppMount("/second", child)])

    with TestClient(parent) as client:
        assert client.get("/first/route").text == "aliased"
        assert client.get("/second/route").text == "aliased"

    assert events == ["aliased:start", "aliased:stop"]


def test_composition_preserves_isolated_child_lifespan_state_for_aliases() -> None:
    events: list[str] = []
    first = stateful_service("first", {"shared": "first", "owner": "one"}, events)
    second = stateful_service("second", {"shared": "second", "owner": "two"}, events)
    alias = stateful_service("alias", {"shared": "alias", "owner": "alias"}, events)
    parent = compose_asgi_apps(
        [
            ASGIAppMount("/second", second),
            ASGIAppMount("/alias/a", alias),
            ASGIAppMount("/first", first),
            ASGIAppMount("/alias/b", alias),
        ]
    )

    with TestClient(parent) as client:
        assert client.get("/first/item").json()["state"] == {"shared": "first", "owner": "one"}
        assert client.get("/second/item").json()["state"] == {"shared": "second", "owner": "two"}
        assert client.get("/alias/a/item").json()["state"] == {"shared": "alias", "owner": "alias"}
        assert client.get("/alias/b/item").json()["state"] == {"shared": "alias", "owner": "alias"}

    assert events == [
        "alias:start",
        "first:start",
        "second:start",
        "second:stop",
        "first:stop",
        "alias:stop",
    ]


def test_composition_equal_depth_order_uses_path_tiebreaker() -> None:
    events: list[str] = []
    parent = compose_asgi_apps(
        [ASGIAppMount("/z-service", service("z", events)), ASGIAppMount("/a-service", service("a", events))]
    )

    with TestClient(parent) as client:
        assert client.get("/a-service/item").text == "a"
        assert client.get("/z-service/item").text == "z"

    assert events == ["a:start", "z:start", "z:stop", "a:stop"]


def test_composition_reverse_routing_exposes_named_child_routes() -> None:
    async def endpoint(request: Request) -> PlainTextResponse:
        return PlainTextResponse(str(request.url_for("named")))

    async def target(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("target")

    child = Starlette(
        routes=[
            Route("/show", endpoint),
            Route("/target", target, name="named"),
        ]
    )
    parent = compose_asgi_apps([ASGIAppMount("/service", child)])

    assert str(parent.url_path_for("named")) == "/service/target"
    with TestClient(parent, base_url="http://testserver") as client:
        assert client.get("/service/show").text == "http://testserver/service/target"


def test_composition_alias_reverse_routing_uses_first_sorted_mount() -> None:
    async def endpoint(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    child = Starlette(routes=[Route("/target", endpoint, name="named")])
    parent = compose_asgi_apps([ASGIAppMount("/z-alias", child), ASGIAppMount("/a-alias", child)])

    assert str(parent.url_path_for("named")) == "/a-alias/target"


def test_composition_clears_failed_child_state_before_restart() -> None:
    events: list[str] = []
    control = {"fail": True, "generation": 0}

    async def endpoint(request: Request) -> JSONResponse:
        return JSONResponse({"generation": request.state.generation})

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        control["generation"] += 1
        generation = control["generation"]
        events.append(f"start:{generation}")
        if control["fail"]:
            raise RuntimeError("startup")
        try:
            yield {"generation": generation}
        finally:
            events.append(f"stop:{generation}")

    child = Starlette(routes=[Route("/{path:path}", endpoint)], lifespan=lifespan)
    parent = compose_asgi_apps([ASGIAppMount("/service", child)])

    with pytest.raises(RuntimeError, match="startup"), TestClient(parent):
        raise AssertionError("startup should fail")

    control["fail"] = False
    with TestClient(parent) as client:
        assert client.get("/service/item").json() == {"generation": 2}

    assert events == ["start:1", "start:2", "stop:2"]


@pytest.mark.parametrize("path", ["", "api", "/api/", "/api//v1", "/api/./v1", "/api/../v1", "/api?x=1"])
def test_composition_rejects_noncanonical_paths(path: str) -> None:
    with pytest.raises(ValueError):
        ASGIAppMount(path, service("bad", []))


def test_composition_rejects_duplicate_paths_without_entering_apps() -> None:
    events: list[str] = []
    first = service("first", events)
    second = service("second", events)

    with pytest.raises(ValueError):
        compose_asgi_apps([ASGIAppMount("/same", first), ASGIAppMount("/same", second)])

    assert events == []


def test_composition_unwinds_started_children_when_startup_fails() -> None:
    events: list[str] = []
    parent = compose_asgi_apps(
        [
            ASGIAppMount("/first", service("first", events)),
            ASGIAppMount("/second", service("second", events, fail=True)),
        ]
    )

    with pytest.raises(RuntimeError, match="second"), TestClient(parent):
        raise AssertionError("startup should fail")

    assert events == ["first:start", "second:start", "first:stop"]
