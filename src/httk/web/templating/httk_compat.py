from pathlib import Path

from .jinja2_engine import JinjaTemplateEngine

LEGACY_TEMPLATE_SUFFIXES: tuple[str, ...] = (
    ".httkweb.html",
    ".html",
    ".html.j2",
    ".jinja",
    ".j2",
)


class HttkCompatTemplateEngine(JinjaTemplateEngine):
    """
    Legacy-oriented template resolution for old httkweb projects.

    The compatibility engine keeps Jinja rendering but prioritizes legacy
    template suffixes so old `.httkweb.html` files resolve first.
    """

    def __init__(self, template_dir: Path) -> None:
        super().__init__(template_dir, template_suffixes=LEGACY_TEMPLATE_SUFFIXES)
