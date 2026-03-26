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

    def render_fragment(self, *, template_name: str, context: dict[str, object]) -> str | None:
        template_key = self._resolve_fragment_template(template_name)
        if template_key is None:
            return None
        template = self._environment.get_template(template_key)
        return template.render(**context)

    def _resolve_template(self, name: str | None) -> str | None:
        if name is None:
            return None

        candidate = name.strip()
        if not candidate:
            return None

        path_candidate = Path(candidate)
        if not self._is_safe_relative_path(path_candidate):
            return None

        direct = self.template_dir / candidate
        if direct.exists() and direct.is_file():
            return candidate

        if path_candidate.suffix:
            return None

        for suffix in TEMPLATE_SUFFIXES:
            with_suffix = self.template_dir / f"{candidate}{suffix}"
            if with_suffix.exists() and with_suffix.is_file():
                return f"{candidate}{suffix}"

        return None

    def _is_safe_relative_path(self, path_candidate: Path) -> bool:
        if path_candidate.is_absolute():
            return False

        base_dir = self.template_dir.resolve(strict=False)
        resolved_candidate = (self.template_dir / path_candidate).resolve(strict=False)

        try:
            resolved_candidate.relative_to(base_dir)
        except ValueError:
            return False
        return True

    def _resolve_fragment_template(self, name: str) -> str | None:
        """
        Resolve function-fragment templates with extra legacy compatibility.

        In old httkweb metadata, function templates often used bare names
        (e.g. `hello_world_result`) and were resolved against the active
        template engine extension. Here we keep that behavior by probing a few
        additional suffix variants.
        """
        template_key = self._resolve_template(name)
        if template_key is not None:
            return template_key

        candidate = name.strip()
        if not candidate:
            return None

        path_candidate = Path(candidate)
        if not self._is_safe_relative_path(path_candidate):
            return None

        for suffix in (".html", ".httkweb.html", ".html.j2", ".jinja", ".j2"):
            with_suffix = f"{candidate}{suffix}"
            template_key = self._resolve_template(with_suffix)
            if template_key is not None:
                return template_key

        return None
