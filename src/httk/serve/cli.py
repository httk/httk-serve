"""The ``httk serve`` umbrella command and its ``web`` and ``optimade`` groups."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

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


def build_parser(program: str) -> argparse.ArgumentParser:
    """Build the public ``httk serve`` argument parser.

    :param program: Program name displayed in command-line help.
    :return: Configured argument parser.
    """

    parser = argparse.ArgumentParser(prog=program, description="Serve httk applications")
    parser.set_defaults(handler=None, help_parser=parser)
    groups = parser.add_subparsers(metavar="GROUP")
    web_cli.add_parser(groups)
    optimade_cli.add_parser(groups)
    return parser


def command(argv: Sequence[str], context: CLIContextLike) -> int:
    """Dispatch the command registered by ``httk.registry.cli.serve``.

    :param argv: Command-line arguments after the root command.
    :param context: Root-command program and working-directory context.
    :return: Process-style command status code.
    """

    parser = build_parser(f"{context.program} serve")
    try:
        arguments = parser.parse_args(list(argv))
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    handler = getattr(arguments, "handler", None)
    if handler is None:
        getattr(arguments, "help_parser", parser).print_help()
        return 0
    return handler(arguments)


if __name__ == "__main__":
    from httk.core.cli import CLIContext

    raise SystemExit(command(sys.argv[1:], CLIContext("httk", Path.cwd())))
