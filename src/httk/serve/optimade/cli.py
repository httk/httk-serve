"""The ``httk serve optimade`` command: serve an entry-store file over OPTIMADE.

``httk serve optimade serve STORE`` is a production ASGI runner meant to be the
single ``ExecStart`` command of a service unit behind a front-line web server
such as Apache or nginx. It opens an SQLite or DuckDB store file built by
*httk-store*, discovers its OPTIMADE entry families, and runs the
:func:`~httk.serve.optimade.create_asgi_app` application under uvicorn on a TCP
port or a Unix domain socket. ``httk serve optimade check STORE`` performs the
same opening, discovery, and endpoint self-test without serving.
"""

import argparse
import asyncio
import datetime
import importlib
import json
import logging
import os
import sys
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import fields
from pathlib import Path
from typing import TYPE_CHECKING, Any

import uvicorn
from httk.core.report import configure_reporting
from uvicorn.config import STARTUP_FAILURE

from httk.serve.http import ServeApp

from .model.config import OptimadeConfig
from .runtime.asgi import _normalized_root_path

if TYPE_CHECKING:
    from httk.store.backend.sql.store import SqlStore

_WORKER_ENVIRONMENT = "HTTK_SERVE_OPTIMADE_WORKER"
_SQLITE_HEADER = b"SQLite format 3\x00"
_LOGGER = logging.getLogger(__name__)

# Operator-facing failures to open or discover a store: a missing driver extra,
# an unreadable or foreign file, a layout refusal, or invalid configuration.
_OPEN_ERRORS = (ImportError, OSError, RuntimeError, TypeError, ValueError)


def add_parser(groups: Any) -> None:
    """Add the ``optimade`` command group to the ``httk serve`` parser.

    :param groups: Sub-parser collection of the ``httk serve`` parser.
    """

    optimade = groups.add_parser(
        "optimade",
        help="serve entry-store files over OPTIMADE",
        description="Serve and check httk-store files as OPTIMADE APIs",
    )
    optimade.set_defaults(help_parser=optimade)
    subparsers = optimade.add_subparsers(metavar="COMMAND")
    serve_parser = subparsers.add_parser(
        "serve",
        help="serve a store file over OPTIMADE",
        description=(
            "Serve an SQLite or DuckDB store file as an OPTIMADE API with uvicorn, "
            "e.g. as the ExecStart command of a systemd unit behind a reverse proxy."
        ),
    )
    serve_parser.set_defaults(handler=_serve, help_parser=serve_parser)
    _store_arguments(serve_parser)
    serve_parser.add_argument(
        "--baseurl",
        help="public base URL of the API, e.g. https://example.org/optimade (default: derived from each request)",
    )
    serve_parser.add_argument("--host", default="127.0.0.1", help="interface to bind (default: 127.0.0.1)")
    bind = serve_parser.add_mutually_exclusive_group()
    bind.add_argument("--port", type=_port, default=8080, help="TCP port to bind (default: 8080)")
    bind.add_argument("--uds", type=Path, help="bind this Unix domain socket instead of a TCP port")
    serve_parser.add_argument(
        "--root-path", type=_root_path, default="", help="ASGI root path when proxied under a sub-path"
    )
    serve_parser.add_argument("--workers", type=_workers, default=1, help="number of worker processes (default: 1)")
    serve_parser.add_argument("--no-access-log", action="store_true", help="disable the per-request access log")
    check_parser = subparsers.add_parser(
        "check",
        help="open a store file and self-test the endpoints it would serve",
        description=(
            "Open a store file and its configuration, request every OPTIMADE endpoint "
            "in-process, and list the entry endpoints it would serve."
        ),
    )
    check_parser.set_defaults(handler=_check, help_parser=check_parser)
    _store_arguments(check_parser)


def _store_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("store", metavar="STORE", type=Path, help="SQLite or DuckDB store file")
    parser.add_argument("--config", type=Path, help="TOML file with OPTIMADE service metadata")
    parser.add_argument(
        "--records",
        metavar="MODULE:CLASS",
        action="append",
        default=[],
        help=(
            "record class the store was written with that no installed distribution registers, "
            "imported with the working directory on the import path; repeatable"
        ),
    )
    parser.add_argument(
        "--data-path",
        metavar="PATH",
        type=_data_path,
        help=(
            "serve file-entry URLs that start with data/ under PATH instead, "
            "e.g. https://example.org/files (stored entries are unchanged)"
        ),
    )


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("must be between 1 and 65535")
    return port


def _workers(value: str) -> int:
    workers = int(value)
    if workers < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return workers


def _root_path(value: str) -> str:
    try:
        _normalized_root_path(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


def _data_path(value: str) -> str:
    if not value.rstrip("/"):
        raise argparse.ArgumentTypeError("must name a location, such as https://example.org/files")
    return value


def open_store(path: str | os.PathLike[str], records: Sequence[type] = ()) -> "SqlStore":
    """Open an existing SQLite or DuckDB store file for serving.

    The format is recognized from the file header, not its name. A DuckDB file
    is opened read-only so several worker processes can share it; an SQLite
    file is served correctly when the serving user has read-only access to it.
    A missing file is never created.

    :param path: Path of the store file.
    :param records: Application record classes the store was written with,
        forwarded as the store's ``records`` option.
    :return: The opened entry store, owned by the caller.
    :raises ValueError: If the file is missing or is neither SQLite nor DuckDB.
    :raises OSError: If the file cannot be read.
    :raises ImportError: If the database driver for the file is not installed.
    :raises RuntimeError: If the file's store layout is refused on reopen.
    """

    path = Path(path)
    if not path.is_file():
        raise ValueError(f"store file does not exist: {path}")
    with path.open("rb") as stream:
        header = stream.read(16)
    from httk.store import DuckdbStore, SqliteStore

    options: dict[str, Any] = {"records": list(records)} if records else {}
    if header == _SQLITE_HEADER:
        return SqliteStore(path, **options)
    if header[8:12] == b"DUCK":
        return DuckdbStore(path, read_only=True, **options)
    raise ValueError(f"not an SQLite or DuckDB store file: {path}")


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _provider(value: object) -> bool:
    return isinstance(value, dict) and all(isinstance(value.get(key), str) for key in ("name", "description", "prefix"))


# The accepted TOML shape of every OptimadeConfig field, with its description.
_CONFIG_SHAPES: dict[str, tuple[Callable[[object], bool], str]] = {
    "provider": (_provider, "a table with string name, description and prefix"),
    "links": (lambda value: isinstance(value, list) and all(isinstance(item, dict) for item in value), "tables"),
    "implementation": (lambda value: isinstance(value, dict), "a table"),
    "database": (lambda value: isinstance(value, dict), "a table"),
    "schema_url": (lambda value: isinstance(value, str), "a string"),
    "request_delay": (_number, "a number"),
    "license": (lambda value: isinstance(value, str | dict), "a string or a table"),
    "available_licenses": (_string_list, "a list of strings"),
    "available_licenses_for_entries": (_string_list, "a list of strings"),
    "page_limit_max": (_integer, "an integer"),
    "partial_data_chunk_size": (_integer, "an integer"),
    "cors_origins": (_string_list, "a list of strings"),
}


def _has_datetime(value: object) -> bool:
    if isinstance(value, datetime.date | datetime.time):
        return True
    if isinstance(value, dict):
        return any(_has_datetime(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_datetime(item) for item in value)
    return False


def load_config(path: str | os.PathLike[str] | None) -> OptimadeConfig:
    """Load OPTIMADE service metadata from a TOML file.

    Top-level keys are the fields of
    :class:`~httk.serve.optimade.model.config.OptimadeConfig`, e.g. a
    ``[provider]`` table with ``name``, ``description`` and ``prefix``,
    ``license``, ``page_limit_max`` and ``cors_origins``. Dates are written as
    strings, not TOML date-time values.

    :param path: TOML file path, or ``None`` for the default configuration.
    :return: The service configuration.
    :raises ValueError: If the file is not valid TOML, names unknown keys, or
        gives a value of the wrong shape.
    :raises OSError: If the file cannot be read.
    """

    if path is None:
        return OptimadeConfig()
    path = Path(path)
    try:
        with path.open("rb") as stream:
            values = tomllib.load(stream)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    unknown = sorted(set(values) - {field.name for field in fields(OptimadeConfig)})
    if unknown:
        raise ValueError(f"{path}: unknown OPTIMADE configuration keys: {', '.join(unknown)}")
    for key, value in values.items():
        accepts, shape = _CONFIG_SHAPES[key]
        if not accepts(value):
            raise ValueError(f"{path}: {key} must be {shape}")
        if _has_datetime(value):
            raise ValueError(f"{path}: {key} contains a TOML date or time; write it as a string")
    if "cors_origins" in values:
        values["cors_origins"] = tuple(values["cors_origins"])
    return OptimadeConfig(**values)


async def _get(app: ServeApp, target: str) -> tuple[int, bytes]:
    path, _separator, query = target.partition("?")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": query.encode(),
        "headers": [(b"host", b"localhost")],
        "client": None,
        "server": ("localhost", 80),
    }
    status = 0
    body = bytearray()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            body.extend(message.get("body", b""))

    await app(scope, receive, send)  # type: ignore[arg-type]
    return status, bytes(body)


def _self_test(app: ServeApp) -> list[str]:
    """Request every served endpoint in-process and describe each failure."""

    entries = app.state.schema.all_entries
    targets = ["/v1/info", "/v1/links", *(f"/v1/{entry}?page_limit=1" for entry in entries)]

    async def run() -> list[tuple[int, bytes]]:
        return [await _get(app, target) for target in targets]

    failures = []
    for target, (status, body) in zip(targets, asyncio.run(run()), strict=True):
        if status != 200:
            try:
                detail = json.loads(body)["errors"][0]["detail"]
            except (ValueError, KeyError, IndexError, TypeError):
                detail = body.decode(errors="replace")[:200]
            failures.append(f"{target}: HTTP {status}: {detail}")
    return failures


def _record_classes(specs: Sequence[str], app_dir: str | os.PathLike[str]) -> list[type]:
    """Import application record classes named as ``MODULE:CLASS``.

    :param specs: Record class references, e.g. ``result_record:Result``.
    :param app_dir: Directory placed first on the import path when any class
        is named, as uvicorn does for application modules.
    :return: The imported classes, in order.
    :raises ValueError: If a reference is malformed, cannot be imported, or
        does not name a class.
    """

    classes = []
    if specs and os.fspath(app_dir) not in sys.path:
        sys.path.insert(0, os.fspath(app_dir))
    for spec in specs:
        module_name, separator, name = spec.partition(":")
        if not separator or not module_name or not name:
            raise ValueError(f"record class must be given as MODULE:CLASS, not {spec!r}")
        try:
            value = getattr(importlib.import_module(module_name), name)
        except (ImportError, AttributeError) as exc:
            raise ValueError(f"cannot import record class {spec}: {exc}") from exc
        if not isinstance(value, type):
            raise ValueError(f"{spec} is not a class")
        classes.append(value)
    return classes


def _store_app(
    store_path: Path,
    config_path: Path | None,
    baseurl: str | None,
    records: Sequence[str],
    app_dir: Path,
    data_path: str | None = None,
) -> tuple["SqlStore", ServeApp]:
    from .api import create_asgi_app

    config = load_config(config_path)
    store = open_store(store_path, _record_classes(records, app_dir))
    try:
        app = create_asgi_app(store, config, baseurl=baseurl, data_path=data_path)
    except BaseException:
        store.close()
        raise
    return store, app


def _console_reporting() -> None:
    if not logging.getLogger("httk").handlers and not logging.getLogger().handlers:
        configure_reporting()


def worker_app() -> ServeApp:
    """Create the application of one uvicorn worker process.

    Uvicorn imports this factory in each worker; ``httk serve optimade serve
    --workers`` passes the store and configuration through the environment.
    The store stays open for the lifetime of the worker process. A worker that
    cannot open the store exits with uvicorn's startup-failure status, which
    stops the supervising process instead of respawning the worker.

    :return: Application serving the configured store.
    :raises RuntimeError: If the process was not started by the serve command.
    """

    settings = os.environ.get(_WORKER_ENVIRONMENT)
    if not settings:
        raise RuntimeError("httk serve optimade worker factory has no store configuration")
    _console_reporting()
    values = json.loads(settings)
    config = Path(values["config"]) if values["config"] is not None else None
    try:
        return _store_app(
            Path(values["store"]),
            config,
            values["baseurl"],
            values["records"],
            Path(values["app_dir"]),
            values.get("data_path"),
        )[1]
    except _OPEN_ERRORS as exc:
        _LOGGER.error("httk serve optimade worker cannot open %s: %s", values["store"], exc)
        sys.exit(STARTUP_FAILURE)


def _serve(arguments: argparse.Namespace) -> int:
    _console_reporting()
    store_path = arguments.store.resolve()
    config_path = arguments.config.resolve() if arguments.config is not None else None
    try:
        store, app = _store_app(
            store_path, config_path, arguments.baseurl, arguments.records, Path.cwd(), arguments.data_path
        )
    except _OPEN_ERRORS as exc:
        print(f"httk serve optimade serve: {exc}", file=sys.stderr)
        return 2
    failures = _self_test(app)
    if failures:
        store.close()
        for failure in failures:
            print(f"httk serve optimade serve: {failure}", file=sys.stderr)
        return 2
    location = f"unix:{arguments.uds}" if arguments.uds is not None else f"http://{arguments.host}:{arguments.port}"
    print(f"Serving {store_path} ({', '.join(app.state.schema.all_entries)}) at {location}", flush=True)
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
        try:
            uvicorn.run(app, **options)
        finally:
            store.close()
        return 0
    # Worker processes import an application factory and open their own store;
    # the parent's open only verified the store before any worker started.
    store.close()
    os.environ[_WORKER_ENVIRONMENT] = json.dumps(
        {
            "store": str(store_path),
            "config": str(config_path) if config_path is not None else None,
            "baseurl": arguments.baseurl,
            "records": arguments.records,
            "app_dir": str(Path.cwd()),
            "data_path": arguments.data_path,
        }
    )
    uvicorn.run("httk.serve.optimade.cli:worker_app", factory=True, workers=arguments.workers, **options)
    return 0


def _check(arguments: argparse.Namespace) -> int:
    try:
        store, app = _store_app(
            arguments.store, arguments.config, None, arguments.records, Path.cwd(), arguments.data_path
        )
    except _OPEN_ERRORS as exc:
        print(f"httk serve optimade check: {exc}", file=sys.stderr)
        return 2
    try:
        failures = _self_test(app)
    finally:
        store.close()
    for failure in failures:
        print(f"httk serve optimade check: {failure}", file=sys.stderr)
    if failures:
        return 1
    for entry in app.state.schema.all_entries:
        print(entry)
    return 0
