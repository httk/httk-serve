"""Register the :command:`httk serve` umbrella command."""

from httk.core.register import register_cli_command

register_cli_command(
    "serve",
    "httk.serve.cli:command",
    "serve httk-serve sites and OPTIMADE store files",
)
