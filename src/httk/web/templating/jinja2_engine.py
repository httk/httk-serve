from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

from .base import TemplateRenderInput

TEMPLATE_SUFFIXES: tuple[str, ...] = (
    ".html.j2",
    ".jinja",
    ".j2",
    ".html",
    ".httkweb.html",
)


class JinjaTemplateEngine:
    def __init__(self, template_dir: Path) -> None:
        self.template_dir = template_dir
        self._environment = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render(self, render_input: TemplateRenderInput) -> str:
        template_key = self._resolve_template(render_input.template_name)
        base_key = self._resolve_template(render_input.base_template_name)

        context = dict(render_input.context)

        content = render_input.content_html
        if template_key is not None:
            template = self._environment.get_template(template_key)
            context["content"] = Markup(content)
            content = template.render(**context)

        if base_key is not None:
            base_template = self._environment.get_template(base_key)
            context["content"] = Markup(content)
            content = base_template.render(**context)

        return content

    def _resolve_template(self, name: str | None) -> str | None:
        if name is None:
            return None

        candidate = name.strip()
        if not candidate:
            return None

        direct = self.template_dir / candidate
        if direct.exists() and direct.is_file():
            return candidate

        path_candidate = Path(candidate)
        if path_candidate.suffix:
            return None

        for suffix in TEMPLATE_SUFFIXES:
            with_suffix = self.template_dir / f"{candidate}{suffix}"
            if with_suffix.exists() and with_suffix.is_file():
                return f"{candidate}{suffix}"

        return None
