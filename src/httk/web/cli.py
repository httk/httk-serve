"""The ``httk web`` command: serve, validate, and list static widgets."""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import uvicorn

from .api import create_asgi_app, serve
from .engine.discovery import DEFAULT_CONTENT_EXTENSIONS
from .engine.site_engine import SiteEngine
from .model.config import SiteConfig
from .model.errors import WebError
from .widgets.core import BUILTIN_WIDGETS

_RELOAD_SRCDIR = "HTTK_WEB_RELOAD_SRCDIR"
_RELOAD_COMPATIBILITY_MODE = "HTTK_WEB_RELOAD_COMPATIBILITY_MODE"
_RELOAD_CONFIG_NAME = "HTTK_WEB_RELOAD_CONFIG_NAME"


class CLIContextLike(Protocol):
    """The minimal root-command context consumed by :func:`command`."""

    @property
    def program(self) -> str: ...

    @property
    def cwd(self) -> Path: ...


def build_parser(program: str) -> argparse.ArgumentParser:
    """Build the public ``httk web`` argument parser."""

    parser = argparse.ArgumentParser(prog=program, description="Serve and validate httk-web sites")
    subparsers = parser.add_subparsers(dest="subcommand", metavar="COMMAND")
    serve_parser = subparsers.add_parser("serve", help="serve a site")
    _site_arguments(serve_parser)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8080)
    serve_parser.add_argument("--reload", action="store_true", help="restart the uvicorn process on source changes")
    check_parser = subparsers.add_parser("check", help="validate all content pages and widgets")
    _site_arguments(check_parser)
    list_parser = subparsers.add_parser("list", help="list available widgets")
    _site_arguments(list_parser)
    return parser


def _site_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("srcdir", metavar="SRCDIR")
    parser.add_argument("--compatibility-mode", action="store_true")
    parser.add_argument("--config-name", default="config")


def command(argv: Sequence[str], context: CLIContextLike) -> int:
    """Dispatch the command registered by ``httk.registry.cli.web``."""

    parser = build_parser(f"{context.program} web")
    try:
        arguments = parser.parse_args(list(argv))
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    if arguments.subcommand is None:
        parser.print_help()
        return 0
    if arguments.subcommand == "serve":
        return _serve(arguments)
    if arguments.subcommand == "check":
        return _check(arguments)
    return _list(arguments)


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
        "httk.web.cli:reload_app",
        factory=True,
        host=arguments.host,
        port=arguments.port,
        reload=True,
        reload_dirs=[str(Path(arguments.srcdir).resolve())],
    )
    return 0


def reload_app():
    """Uvicorn reload factory configured by the process-level reload dispatcher."""

    srcdir = os.environ.get(_RELOAD_SRCDIR)
    if not srcdir:
        raise RuntimeError("httk web reload factory has no source directory")
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
        print(f"httk web check: {exc}", file=sys.stderr)
        return 2
    pages = _content_routes(engine.config.content_dir)
    errors: list[str] = []
    for route in pages:
        try:
            engine.render(route)
        except (OSError, ValueError, WebError) as exc:
            errors.append(str(exc))
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"httk web check: {len(errors)} error(s) across {len(pages)} page(s)", file=sys.stderr)
        return 1
    print(f"httk web check: {len(pages)} page(s) valid")
    return 0


def _list(arguments: argparse.Namespace) -> int:
    try:
        engine = _engine(arguments)
    except (OSError, ValueError, WebError) as exc:
        print(f"httk web list: {exc}", file=sys.stderr)
        return 2
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


if __name__ == "__main__":
    from httk.core import CLIContext

    raise SystemExit(command(sys.argv[1:], CLIContext("httk", Path.cwd())))
