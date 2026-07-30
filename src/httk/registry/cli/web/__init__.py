"""Register the lazy :command:`httk web` umbrella command."""

from httk.core import register_cli_command

register_cli_command(
    "web",
    "httk.web.cli:command",
    "serve and validate httk-web sites",
)
