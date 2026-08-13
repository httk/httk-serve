"""Compose caller-owned Starlette applications at explicit URL paths."""

from collections.abc import Iterable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any

from starlette.applications import Starlette
from starlette.routing import BaseRoute, Mount
from starlette.types import Receive, Scope, Send


def _validate_mount_path(path: object) -> str:
    """Validate and return a canonical absolute mount path."""
    if not isinstance(path, str) or not path:
        raise ValueError("mount paths must be non-empty strings")
    if path == "/":
        return path
    if (
        not path.startswith("/")
        or path.endswith("/")
        or "//" in path
        or "\\" in path
        or "?" in path
        or "#" in path
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
    ):
        raise ValueError("mount paths must be canonical absolute paths")
    segments = path.split("/")
    if any(segment in ("", ".", "..") for segment in segments[1:]):
        raise ValueError("mount paths must be canonical absolute paths")
    return path


@dataclass(frozen=True, slots=True)
class ASGIAppMount:
    """Associate a Starlette application with an explicit absolute path.

    :param path: Canonical absolute URL path, with ``/`` reserved for a root
        fallback.
    :param app: Starlette application to mount.

    The composed application owns the mounted applications for the duration of
    its lifespan and enters each distinct child's Starlette lifespan once.
    Constructing a mount or a composition does not close or otherwise mutate
    caller-owned applications when validation fails.
    """

    path: str
    app: Starlette

    def __post_init__(self) -> None:
        _validate_mount_path(self.path)
        if not isinstance(self.app, Starlette):
            raise TypeError("ASGIAppMount.app must be a Starlette application")


class _MountedChild:
    """Own one child's yielded lifespan state and route calls to that child."""

    def __init__(self, app: Starlette) -> None:
        self.app = app
        self.state: dict[str, Any] = {}

    @property
    def routes(self) -> list[BaseRoute]:
        """Return the live child route table for Starlette reverse routing."""
        return self.app.routes

    @asynccontextmanager
    async def lifespan(self):
        try:
            async with self.app.router.lifespan_context(self.app) as state:
                self.state = dict(state or {})
                yield
        finally:
            self.state.clear()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        child_scope = dict(scope)
        if scope["type"] in {"http", "websocket"}:
            child_scope["state"] = {**dict(scope.get("state") or {}), **self.state}
        await self.app(child_scope, receive, send)


def _ordered_mounts(mounts: Iterable[ASGIAppMount], root: ASGIAppMount | None) -> list[ASGIAppMount]:
    """Validate mount ownership and return routes from specific to broad."""
    supplied = list(mounts)
    if any(not isinstance(mount, ASGIAppMount) for mount in supplied):
        raise TypeError("mounts must contain ASGIAppMount instances")
    if root is not None and not isinstance(root, ASGIAppMount):
        raise TypeError("root must be an ASGIAppMount instance or None")

    paths = [mount.path for mount in supplied]
    if "/" in paths:
        raise ValueError("the root mount must be supplied with root=, not among mounts")
    if root is not None and root.path != "/":
        raise ValueError("root mount path must be '/'")
    if len(paths) != len(set(paths)):
        raise ValueError("mount paths must be unique")

    return sorted(supplied, key=lambda mount: (-mount.path.count("/"), mount.path))


def compose_asgi_apps(mounts: Iterable[ASGIAppMount], *, root: ASGIAppMount | None = None) -> Starlette:
    """Compose Starlette applications at caller-selected URL paths.

    More-specific paths are routed before their ancestors regardless of caller
    order. At application startup, each distinct mounted child is entered in
    that deterministic route order and is exited in reverse order; a failed
    startup unwinds children that already started. The returned parent owns
    lifespan coordination but not application configuration or data.

    :param mounts: Service mounts with paths other than ``/``.
    :param root: Optional ``ASGIAppMount`` at ``/`` used as the fallback route.
    :return: Parent Starlette application containing the requested mounts.
    :raises TypeError: If a descriptor or application has the wrong type.
    :raises ValueError: If paths are malformed, noncanonical, or duplicated.
    """
    ordered = _ordered_mounts(mounts, root)
    all_mounts = ordered + ([root] if root is not None else [])
    children_by_id: dict[int, _MountedChild] = {}
    children: list[_MountedChild] = []
    routes: list[Mount] = []
    for mount in all_mounts:
        app_id = id(mount.app)
        child = children_by_id.get(app_id)
        if child is None:
            child = _MountedChild(mount.app)
            children_by_id[app_id] = child
            children.append(child)
        routes.append(Mount(mount.path, app=child))

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        async with AsyncExitStack() as stack:
            for child in children:
                await stack.enter_async_context(child.lifespan())
            yield

    return Starlette(routes=routes, lifespan=lifespan)
