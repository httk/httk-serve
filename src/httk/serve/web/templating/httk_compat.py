from pathlib import Path

from markupsafe import Markup

from ._legacy_formatter import HttkTemplateFormatter, UnquotedText
from .base import TemplateRenderInput
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
        self._legacy_formatter = HttkTemplateFormatter()

    def render(self, render_input: TemplateRenderInput) -> str:
        template_key = self._resolve_template(render_input.template_name)
        base_key = self._resolve_template(render_input.base_template_name)

        context = dict(render_input.context)
        content = render_input.content_html
        if template_key is not None:
            content = self._render_with_resolved_template(template_key, content=content, context=context)

        if base_key is not None:
            content = self._render_with_resolved_template(base_key, content=content, context=context)

        return content

    def render_fragment(self, *, template_name: str, context: dict[str, object]) -> str | None:
        template_key = self._resolve_fragment_template(template_name)
        if template_key is None:
            return None

        if template_key.endswith(".httkweb.html"):
            template_text = (self.template_dir / template_key).read_text(encoding="utf-8")
            return self._legacy_formatter.format(template_text, **dict(context))

        template = self._environment.get_template(template_key)
        return template.render(**context)

    def _render_with_resolved_template(self, template_key: str, *, content: str, context: dict[str, object]) -> str:
        if not template_key.endswith(".httkweb.html"):
            template = self._environment.get_template(template_key)
            working = dict(context)
            working["content"] = Markup(content)
            return template.render(**working)

        template_text = (self.template_dir / template_key).read_text(encoding="utf-8")
        working = dict(context)
        working["content"] = UnquotedText(content)
        return self._legacy_formatter.format(template_text, **working)
