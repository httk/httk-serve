"""The ``httk serve`` command: serve a store over OPTIMADE, a web site, or both.

The arguments select one mode: ``STORE`` serves the store over OPTIMADE at
``/optimade/`` with a web site (the built-in one unless ``--site DIR``),
``STORE --no-site`` serves OPTIMADE only, and ``--site DIR`` without a store
serves the site only. ``--check`` validates the selected mode in-process
instead of serving, and ``--site DIR --list-widgets`` lists the site's widgets.
"""

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from typing import Protocol

from .combined import cli as combined_cli
from .optimade import cli as optimade_cli
from .web import cli as web_cli


class CLIContextLike(Protocol):
    """The minimal root-command context consumed by :func:`command`."""

    @property
    def program(self) -> str:
        """Return the root program name."""
        ...

    @property
    def cwd(self) -> Path:
        """Return the root command's working directory."""
        ...


# Mode-restricted options by namespace attribute; each mode below admits a subset.
_FLAGS = {
    "site": "--site",
    "no_site": "--no-site",
    "list_widgets": "--list-widgets",
    "config": "--optimade-config",
    "records": "--record-class",
    "data_path": "--data-path",
    "data_url": "--data-url",
    "public_url": "--public-url",
    "compatibility_mode": "--compatibility-mode",
    "config_name": "--site-config",
    "host": "--host",
    "port": "--port",
    "uds": "--uds",
    "root_path": "--root-path",
    "workers": "--workers",
    "no_access_log": "--no-access-log",
    "reload": "--reload",
}
# Restricted options default to None so an explicit value is detectable; these fill in afterwards.
_DEFAULTS = {"host": "127.0.0.1", "port": 8080, "root_path": "", "workers": 1, "config_name": "config"}
_STORE = {"config", "records", "data_path", "data_url", "public_url"}
_BIND = {"host", "port", "uds", "root_path", "workers", "no_access_log"}
_SITE = {"site", "compatibility_mode", "config_name"}
# Each mode's allowed options and its handler, looked up by name in its module at dispatch.
_MODES: dict[tuple[str, str], tuple[frozenset[str], ModuleType, str]] = {
    ("combined", "serve"): (frozenset(_STORE | _BIND | {"site"}), combined_cli, "_serve"),
    ("combined", "check"): (frozenset(_STORE | {"site", "root_path"}), combined_cli, "_check"),
    ("OPTIMADE-only", "serve"): (frozenset(_STORE | _BIND | {"no_site"}), optimade_cli, "_serve"),
    ("OPTIMADE-only", "check"): (frozenset(_STORE - {"public_url"} | {"no_site"}), optimade_cli, "_check"),
    ("site-only", "serve"): (frozenset(_SITE | {"host", "port", "reload"}), web_cli, "_serve"),
    ("site-only", "check"): (frozenset(_SITE), web_cli, "_check"),
    ("site-only", "list"): (frozenset(_SITE | {"list_widgets"}), web_cli, "_list"),
}


def build_parser(program: str) -> argparse.ArgumentParser:
    """Build the public ``httk serve`` argument parser.

    :param program: Program name displayed in command-line help.
    :return: Configured argument parser.
    """

    parser = argparse.ArgumentParser(
        prog=program,
        description=(
            "Serve an SQLite or DuckDB store file built by httk-store over OPTIMADE at /optimade/ with a web site "
            "at / (STORE), over OPTIMADE only (STORE --no-site), or a web site only (--site DIR)."
        ),
        allow_abbrev=False,
    )
    parser.add_argument("store", metavar="STORE", type=Path, nargs="?", help="SQLite or DuckDB store file")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true", help="validate the selected mode in-process and exit")
    actions.add_argument("--list-widgets", action="store_true", help="list the widgets of --site DIR and exit")

    store = parser.add_argument_group("store and OPTIMADE options")
    store.add_argument("--no-site", action="store_true", help="serve the store over OPTIMADE only, without a site")
    store.add_argument(
        "--optimade-config", dest="config", metavar="FILE", type=Path, help="TOML file with OPTIMADE service metadata"
    )
    store.add_argument(
        "--record-class",
        dest="records",
        metavar="MODULE:CLASS",
        action="append",
        default=[],
        help=(
            "record class the store was written with that no installed distribution registers, "
            "imported with the working directory on the import path; repeatable"
        ),
    )
    files = store.add_mutually_exclusive_group()
    files.add_argument(
        "--data-path",
        metavar="[NAME=]PATH",
        type=optimade_cli._data_path,
        action=optimade_cli._DataPathAction,
        help=(
            "serve the registered files stored as data/<name>/<path> from PATH/<name>/<path> at /data/; "
            "or, repeated as NAME=PATH, the files of workspace NAME from PATH/<path> "
            "(a value splits at its first = only when no / or \\ precedes it)"
        ),
    )
    files.add_argument(
        "--data-url",
        metavar="URL",
        type=optimade_cli._data_url,
        help=(
            "serve file-entry URLs that start with data/ under the external URL instead, "
            "e.g. https://example.org/files (stored entries are unchanged)"
        ),
    )
    store.add_argument(
        "--public-url",
        metavar="URL",
        help=(
            "public URL of the server (with a site) or of the OPTIMADE API (with --no-site), "
            "e.g. https://example.org/db (default: derived from each request)"
        ),
    )

    site = parser.add_argument_group("site options")
    site.add_argument(
        "--site", metavar="DIR", type=Path, help="web site source directory (default with a STORE: built-in site)"
    )
    site.add_argument(
        "--site-config", dest="config_name", metavar="NAME", help="site configuration name (default: config)"
    )
    site.add_argument("--compatibility-mode", action="store_true", help="use legacy site conventions")

    server = parser.add_argument_group("server options")
    server.add_argument("--host", help="interface to bind (default: 127.0.0.1)")
    bind = server.add_mutually_exclusive_group()
    bind.add_argument("--port", type=optimade_cli._port, help="TCP port to bind (default: 8080)")
    bind.add_argument("--uds", type=Path, help="bind this Unix domain socket instead of a TCP port")
    server.add_argument(
        "--root-path", type=optimade_cli._root_path, help="ASGI root path when proxied under a sub-path"
    )
    server.add_argument("--workers", type=optimade_cli._workers, help="number of worker processes (default: 1)")
    server.add_argument("--no-access-log", action="store_true", help="disable the per-request access log")
    server.add_argument("--reload", action="store_true", help="restart the server on site source changes")
    return parser


def _handler(parser: argparse.ArgumentParser, arguments: argparse.Namespace) -> Callable[[argparse.Namespace], int]:
    """Select the mode handler, reject options foreign to the mode, and fill in the mode's attributes."""

    if arguments.store is None and arguments.site is None:
        parser.error("give a STORE file to serve or --site DIR")
    kind = "site-only" if arguments.store is None else "OPTIMADE-only" if arguments.no_site else "combined"
    action = "list" if kind == "site-only" and arguments.list_widgets else "check" if arguments.check else "serve"
    allowed, module, name = _MODES[kind, action]
    for dest, flag in _FLAGS.items():
        if dest not in allowed and getattr(arguments, dest) not in (None, False, []):
            mode = f"{kind} --check" if action == "check" else f"{kind} --list-widgets" if action == "list" else kind
            parser.error(f"argument {flag}: not used in {mode} mode")
    if arguments.site is not None and not arguments.site.is_dir():
        parser.error(f"argument --site: not a directory: {arguments.site}")
    for dest, value in _DEFAULTS.items():
        if getattr(arguments, dest) is None:
            setattr(arguments, dest, value)
    # The handlers keep the attribute names of their former subcommands.
    arguments.baseurl = arguments.public_base_url = arguments.public_url
    arguments.srcdir = arguments.site
    return getattr(module, name)


def command(argv: Sequence[str], context: CLIContextLike) -> int:
    """Dispatch the command registered by ``httk.registry.cli.serve``.

    :param argv: Command-line arguments after the root command.
    :param context: Root-command program and working-directory context.
    :return: Process-style command status code.
    """

    parser = build_parser(f"{context.program} serve")
    try:
        arguments = parser.parse_args(list(argv))
        handler = _handler(parser, arguments)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    return handler(arguments)


if __name__ == "__main__":
    from httk.core.cli import CLIContext

    raise SystemExit(command(sys.argv[1:], CLIContext("httk", Path.cwd())))
