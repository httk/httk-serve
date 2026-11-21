"""The site-only mode of ``httk serve --site DIR``: serve, validate, and list widgets."""

import argparse
import os
import sys
from pathlib import Path

import uvicorn

from httk.serve.http import ServeApp

from .api import create_asgi_app, serve
from .engine.discovery import DEFAULT_CONTENT_EXTENSIONS
from .engine.site_engine import SiteEngine
from .model.config import SiteConfig
from .model.errors import WebError
from .widgets.core import BUILTIN_WIDGETS

_RELOAD_SRCDIR = "HTTK_SERVE_WEB_RELOAD_SRCDIR"
_RELOAD_COMPATIBILITY_MODE = "HTTK_SERVE_WEB_RELOAD_COMPATIBILITY_MODE"
_RELOAD_CONFIG_NAME = "HTTK_SERVE_WEB_RELOAD_CONFIG_NAME"


def _serve(arguments: argparse.Namespace) -> int:
    if not arguments.reload:
        serve(
            arguments.srcdir,
            host=arguments.host,
            port=arguments.port,
            compatibility_mode=arguments.compatibility_mode,
            config_name=arguments.config_name,
        )
        return 0
    # Uvicorn's reload supervisor must import a factory in a fresh process.
    # Its configuration is intentionally process-level, not a module cache reset.
    os.environ[_RELOAD_SRCDIR] = str(Path(arguments.srcdir).resolve())
    os.environ[_RELOAD_COMPATIBILITY_MODE] = "1" if arguments.compatibility_mode else "0"
    os.environ[_RELOAD_CONFIG_NAME] = arguments.config_name
    uvicorn.run(
        "httk.serve.web.cli:reload_app",
        factory=True,
        host=arguments.host,
        port=arguments.port,
        reload=True,
        reload_dirs=[str(Path(arguments.srcdir).resolve())],
    )
    return 0


def reload_app() -> ServeApp:
    """Create the app selected by the process-level reload dispatcher.

    :return: Fresh application configured from reload environment variables.
    """

    srcdir = os.environ.get(_RELOAD_SRCDIR)
    if not srcdir:
        raise RuntimeError("httk serve reload factory has no source directory")
    return create_asgi_app(
        srcdir,
        compatibility_mode=os.environ.get(_RELOAD_COMPATIBILITY_MODE) == "1",
        config_name=os.environ.get(_RELOAD_CONFIG_NAME, "config"),
    )


def _engine(arguments: argparse.Namespace) -> SiteEngine:
    return SiteEngine(
        SiteConfig.from_srcdir(
            arguments.srcdir,
            compatibility_mode=arguments.compatibility_mode,
            config_name=arguments.config_name,
        )
    )


def _check(arguments: argparse.Namespace) -> int:
    try:
        engine = _engine(arguments)
    except (OSError, ValueError, WebError) as exc:
        print(f"httk serve: {exc}", file=sys.stderr)
        return 2
    with engine:
        pages = _content_routes(engine.config.content_dir)
        errors = _reserved_route_collisions(engine.config, pages)
        for route in pages:
            try:
                engine.render(route)
            except (OSError, ValueError, WebError) as exc:
                errors.append(str(exc))
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            print(f"httk serve: {len(errors)} error(s) across {len(pages)} page(s)", file=sys.stderr)
            return 1
        print(f"httk serve: {len(pages)} page(s) valid")
        return 0


def _list(arguments: argparse.Namespace) -> int:
    try:
        engine = _engine(arguments)
    except (OSError, ValueError, WebError) as exc:
        print(f"httk serve: {exc}", file=sys.stderr)
        return 2
    with engine:
        items = BUILTIN_WIDGETS.available() + engine.widget_loader.available()
        for name, source in sorted(items):
            print(f"{name}\t{source}")
        return 0


def _content_routes(content_dir: Path) -> list[str]:
    if not content_dir.exists():
        return []
    routes: list[str] = []
    for path in sorted(content_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in DEFAULT_CONTENT_EXTENSIONS:
            continue
        routes.append(str(path.relative_to(content_dir).with_suffix("")).replace("\\", "/"))
    return routes


def _reserved_route_collisions(config: SiteConfig, pages: list[str]) -> list[str]:
    """Keep application-reserved runtime paths unavailable to site files."""

    collisions: list[str] = []
    if any(route == "_httk" or route.startswith("_httk/") for route in pages):
        collisions.append("Reserved httk-serve route collision in content: _httk")
    static_reserved = config.static_dir / "_httk"
    if static_reserved.exists():
        collisions.append("Reserved httk-serve route collision in static: _httk")
    return collisions
