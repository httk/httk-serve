"""Compose OPTIMADE, a web site and the file-bytes route over one entry store."""

import os
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from httk.store import EntryStore
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.routing import Route

from httk.serve.composition import ASGIAppMount, compose_asgi_apps
from httk.serve.http import ServeApp
from httk.serve.optimade import OptimadeConfig
from httk.serve.optimade import create_asgi_app as create_optimade_app
from httk.serve.optimade.runtime.asgi import _normalized_root_path
from httk.serve.web import create_asgi_app as create_web_app

from .files import create_files_app


def _default_site() -> Path:
    """Return the packaged default site directory."""
    site = files("httk.serve.combined") / "site"
    if not isinstance(site, Path) or not site.is_dir():
        raise RuntimeError(f"the packaged default site is not an installed directory: {site}")
    return site


def _public_base_url(value: str) -> str:
    """Validate an absolute HTTP(S) base URL and strip its trailing slash."""
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _port = parsed.port
    except ValueError as exc:
        raise ValueError(f"public_base_url must be an absolute HTTP(S) URL: {value!r}") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or "?" in value
        or "#" in value
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise ValueError(
            f"public_base_url must be an absolute HTTP(S) URL without credentials, query or fragment: {value!r}"
        )
    return value.rstrip("/")


async def _redirect_to_optimade_base(request: Request) -> RedirectResponse:
    # scope["path"] carries the ASGI root_path, so the target stays under it.
    query = request.url.query
    return RedirectResponse(request.url.path + "/" + (f"?{query}" if query else ""), status_code=308)


def create_combined_app(
    store: EntryStore,
    config: OptimadeConfig | None = None,
    *,
    site: str | os.PathLike[str] | None = None,
    data_path: str | os.PathLike[str] | Mapping[str, str | os.PathLike[str]] | None = None,
    data_url: str | None = None,
    public_base_url: str | None = None,
    root_path: str = "",
    debug: bool = False,
) -> ServeApp:
    """Create one application serving a store over OPTIMADE together with a web site.

    The OPTIMADE API is mounted at ``/optimade`` (``/optimade`` itself
    redirects to ``/optimade/``), the web site at ``/``, and, with
    ``data_path``, the bytes of the registered stored files at ``/data``.
    Served ``files`` URLs that are stored as ``data/<path>`` then point at
    ``/data/<path>``: under ``public_base_url`` when it is given, otherwise
    made absolute against each request's origin. With ``data_url`` they
    point at an external file server instead and no ``/data`` route exists;
    with neither, stored URLs are served as stored. The store stays owned by
    the caller; the web site's engine is closed by the application's lifespan.

    :param store: Entry store to serve, owned by the caller.
    :param config: OPTIMADE service configuration.
    :param site: Web site source directory (default: the packaged site whose
        pages browse the store through the OPTIMADE API).
    :param data_path: Directory holding the workspaces named by stored
        ``data/<name>/...`` file URLs, or a mapping from workspace names to
        their directories, enabling the ``/data`` route; see
        :func:`~httk.serve.combined.files.create_files_app`.
    :param data_url: External location served in place of the leading
        ``data`` segment of stored file URLs; see
        :func:`~httk.serve.optimade.create_asgi_app`.
    :param public_base_url: Absolute public HTTP(S) URL of the composed
        application, authoritative for OPTIMADE links and file URLs; by
        default both derive from each request.
    :param root_path: ASGI root path the application is served under (the
        value given to uvicorn), used for root-relative file URLs.
    :param debug: Enable Starlette and backend diagnostics.
    :return: Composed application; ``app.state.optimade_app`` and
        ``app.state.web_app`` hold the mounted children and
        ``app.state.data_url`` the served file location.
    :raises ValueError: If ``data_path`` and ``data_url`` are both given, a
        ``data_path`` directory does not exist or a workspace name is not a
        single path component, ``public_base_url`` or ``root_path`` is
        malformed, or a child application refuses its configuration.
    :raises RuntimeError: If the packaged default site is not installed as a directory.
    """

    if data_path is not None and data_url is not None:
        raise ValueError("data_path and data_url are mutually exclusive")
    public = _public_base_url(public_base_url) if public_base_url is not None else None
    normalized_root = _normalized_root_path(root_path)
    mounts = []
    if data_path is not None:
        mounts.append(ASGIAppMount("/data", create_files_app(store, data_path)))
        data_url = f"{public or ('/' + normalized_root if normalized_root else '')}/data"
    site_dir = Path(site) if site is not None else _default_site()

    optimade_app = create_optimade_app(
        store,
        config,
        baseurl=f"{public}/optimade" if public is not None else None,
        data_url=data_url,
        debug=debug,
    )
    mounts.append(ASGIAppMount("/optimade", optimade_app))
    web_app = create_web_app(site_dir, debug=debug)
    try:
        app = compose_asgi_apps(mounts, root=ASGIAppMount("/", web_app))
        app.router.routes.insert(0, Route("/optimade", _redirect_to_optimade_base, methods=["GET"]))
    except BaseException as exc:
        try:
            web_app.state.engine.close()
        except BaseException as cleanup_error:
            exc.add_note(f"Additional site resource cleanup failure: {cleanup_error!r}")
        raise
    app.state.optimade_app = optimade_app
    app.state.web_app = web_app
    app.state.data_url = data_url
    return app
