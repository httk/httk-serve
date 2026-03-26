from dataclasses import dataclass


@dataclass(frozen=True)
class TemplateRenderInput:
    content_html: str
    template_name: str | None
    base_template_name: str | None
    context: dict[str, object]
