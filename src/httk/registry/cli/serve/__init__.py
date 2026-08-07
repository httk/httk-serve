"""Register the :command:`httk serve web` umbrella command."""

from httk.core.register import register_cli_command

register_cli_command(
    "serve",
    "httk.serve.web.cli:command",
    "serve and validate httk-serve sites",
)
