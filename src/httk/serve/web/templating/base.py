from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TemplateRenderInput:
    content_html: str
    template_name: str | None
    base_template_name: str | None
    context: dict[str, object]


class TemplateEngine(Protocol):
    def render(self, render_input: TemplateRenderInput) -> str: ...

    def render_fragment(self, *, template_name: str, context: dict[str, object]) -> str | None: ...
