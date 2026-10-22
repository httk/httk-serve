"""The ``httk serve combined`` command: one server for OPTIMADE, a web site and file bytes.

``httk serve combined serve STORE`` serves an SQLite or DuckDB store file
built by *httk-store* over OPTIMADE at ``/optimade/`` together with the
packaged browsing site (or ``--site DIR``) at ``/`` and, with
``--data-path``, the registered stored files at ``/data/``, under uvicorn.
``httk serve combined check STORE`` performs the same opening and an
in-process self-test without serving.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import uvicorn
from httk.store import EntryStore
from uvicorn.config import STARTUP_FAILURE

from httk.serve.http import ServeApp
from httk.serve.optimade.cli import (
    _OPEN_ERRORS,
    _bind_arguments,
    _console_reporting,
    _file_warnings,
    _get,
    _record_classes,
    _root_path,
    _store_arguments,
    load_config,
    open_store,
)
from httk.serve.optimade.cli import _self_test as _optimade_self_test
from httk.serve.optimade.runtime.asgi import _normalized_root_path

from .api import create_combined_app

if TYPE_CHECKING:
    from httk.store.backend.sql.store import SqlStore

_WORKER_ENVIRONMENT = "HTTK_SERVE_COMBINED_WORKER"
# Pages of the packaged default site; a custom --site only needs its home page.
_SITE_PAGES = ("/", "/browse", "/entry")
_LOGGER = logging.getLogger(__name__)


def add_parser(groups: Any) -> None:
    """Add the ``combined`` command group to the ``httk serve`` parser.

    :param groups: Sub-parser collection of the ``httk serve`` parser.
    """

    combined = groups.add_parser(
        "combined",
        help="serve a store over OPTIMADE together with a browsing web site",
        description="Serve and check httk-store files over OPTIMADE with a web site and file downloads",
    )
    combined.set_defaults(help_parser=combined)
    subparsers = combined.add_subparsers(metavar="COMMAND")
    serve_parser = subparsers.add_parser(
        "serve",
        help="serve a store file over OPTIMADE with a web site",
        description=(
            "Serve an SQLite or DuckDB store file over OPTIMADE at /optimade/ with a web site at / "
            "and, with --data-path, the registered files at /data/, with uvicorn."
        ),
    )
    serve_parser.set_defaults(handler=_serve, help_parser=serve_parser)
    _arguments(serve_parser)
    _bind_arguments(serve_parser)
    check_parser = subparsers.add_parser(
        "check",
        help="open a store file and self-test the combined server",
        description="Open a store file, request the OPTIMADE endpoints and site pages in-process, and report.",
    )
    check_parser.set_defaults(handler=_check, help_parser=check_parser)
    _arguments(check_parser)
    check_parser.add_argument(
        "--root-path", type=_root_path, default="", help="ASGI root path when proxied under a sub-path"
    )


def _arguments(parser: argparse.ArgumentParser) -> None:
    _store_arguments(parser)
    parser.add_argument(
        "--public-base-url",
        metavar="URL",
        help="public http(s) URL of the server, e.g. https://example.org/db (default: derived from each request)",
    )
    parser.add_argument("--site", metavar="DIR", type=Path, help="web site source directory (default: built-in site)")


def _settings(arguments: argparse.Namespace) -> dict[str, Any]:
    def path(value: Path | None) -> str | None:
        return str(value.resolve()) if value is not None else None

    return {
        "store": path(arguments.store),
        "config": path(arguments.config),
        "records": arguments.records,
        "app_dir": str(Path.cwd()),
        "site": path(arguments.site),
        "data_path": arguments.data_path,
        "data_url": arguments.data_url,
        "public_base_url": arguments.public_base_url,
        "root_path": arguments.root_path,
    }


def _combined_app(settings: dict[str, Any]) -> tuple["SqlStore", ServeApp]:
    config = load_config(settings["config"])
    store = open_store(settings["store"], _record_classes(settings["records"], settings["app_dir"]))
    try:
        app = create_combined_app(
            store,
            config,
            site=settings["site"],
            data_path=settings["data_path"],
            data_url=settings["data_url"],
            public_base_url=settings["public_base_url"],
            root_path=settings["root_path"],
        )
    except BaseException:
        store.close()
        raise
    return store, app


def _close(store: "SqlStore", app: ServeApp) -> None:
    try:
        app.state.web_app.state.engine.close()
    finally:
        store.close()


def worker_app() -> ServeApp:
    """Create the application of one uvicorn worker process.

    Uvicorn imports this factory in each worker; ``httk serve combined serve
    --workers`` passes the settings through the environment, and each worker
    opens its own store for its lifetime. A worker that cannot open the store
    exits with uvicorn's startup-failure status, which stops the supervising
    process instead of respawning the worker.

    :return: Application serving the configured store.
    :raises RuntimeError: If the process was not started by the serve command.
    """

    settings = os.environ.get(_WORKER_ENVIRONMENT)
    if not settings:
        raise RuntimeError("httk serve combined worker factory has no store configuration")
    _console_reporting()
    values = json.loads(settings)
    try:
        return _combined_app(values)[1]
    except _OPEN_ERRORS as exc:
        _LOGGER.error("httk serve combined worker cannot open %s: %s", values["store"], exc)
        sys.exit(STARTUP_FAILURE)


def _self_test(app: ServeApp, store: EntryStore, settings: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Request the OPTIMADE endpoints and site pages in-process; return failures and warnings."""

    failures = _optimade_self_test(app, prefix="/optimade", entries=app.state.optimade_app.state.schema.all_entries)
    for target in _SITE_PAGES if settings["site"] is None else ("/",):
        status, _body = asyncio.run(_get(app, target))
        if status != 200:
            failures.append(f"{target}: HTTP {status}")
    warnings = _file_warnings(
        app,
        store,
        api=app.state.optimade_app,
        prefix="/optimade",
        data_path=settings["data_path"],
        data_url=app.state.data_url,
    )
    return failures, warnings


def _serve(arguments: argparse.Namespace) -> int:
    _console_reporting()
    settings = _settings(arguments)
    try:
        store, app = _combined_app(settings)
    except _OPEN_ERRORS as exc:
        print(f"httk serve combined serve: {exc}", file=sys.stderr)
        return 2
    try:
        failures, warnings = _self_test(app, store, settings)
    except BaseException:
        _close(store, app)
        raise
    if failures:
        _close(store, app)
        for failure in failures:
            print(f"httk serve combined serve: {failure}", file=sys.stderr)
        return 2
    for warning in warnings:
        print(f"httk serve combined serve: warning: {warning}", file=sys.stderr)
    if arguments.public_base_url is not None:
        site = arguments.public_base_url.rstrip("/")
    else:
        location = f"unix:{arguments.uds}" if arguments.uds is not None else f"http://{arguments.host}:{arguments.port}"
        root = _normalized_root_path(arguments.root_path)
        site = location + ("/" + root if root else "")
    entries = ", ".join(app.state.optimade_app.state.schema.all_entries)
    print(f"Serving {settings['store']} ({entries})\nSite: {site}/\nOPTIMADE: {site}/optimade/", flush=True)
    options: dict[str, Any] = {
        "root_path": arguments.root_path,
        "access_log": not arguments.no_access_log,
        "server_header": False,
    }
    if arguments.uds is not None:
        options["uds"] = str(arguments.uds)
    else:
        options.update(host=arguments.host, port=arguments.port)
    if arguments.workers == 1:
        # The application's lifespan closes the site engine; the store is ours.
        try:
            uvicorn.run(app, **options)
        finally:
            store.close()
        return 0
    # Worker processes import an application factory and open their own store;
    # the parent's open only verified the store before any worker started.
    _close(store, app)
    os.environ[_WORKER_ENVIRONMENT] = json.dumps(settings)
    uvicorn.run("httk.serve.combined.cli:worker_app", factory=True, workers=arguments.workers, **options)
    return 0


def _check(arguments: argparse.Namespace) -> int:
    settings = _settings(arguments)
    try:
        store, app = _combined_app(settings)
    except _OPEN_ERRORS as exc:
        print(f"httk serve combined check: {exc}", file=sys.stderr)
        return 2
    try:
        failures, warnings = _self_test(app, store, settings)
    finally:
        _close(store, app)
    for failure in failures:
        print(f"httk serve combined check: {failure}", file=sys.stderr)
    for warning in warnings:
        print(f"httk serve combined check: warning: {warning}", file=sys.stderr)
    if failures:
        return 1
    for entry in app.state.optimade_app.state.schema.all_entries:
        print(f"/optimade/v1/{entry}")
    return 0
