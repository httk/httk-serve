"""Define template render inputs and the engine protocol."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TemplateRenderInput:
    """Carry page content and context into a template engine.

    :param content_html: Rendered page HTML supplied to the template.
    :param template_name: Optional content template name.
    :param base_template_name: Optional base template name.
    :param context: Values exposed to templates.
    """

    content_html: str
    template_name: str | None
    base_template_name: str | None
    context: dict[str, object]


class TemplateEngine(Protocol):
    """Define the page and fragment template-engine protocol."""

    def render(self, render_input: TemplateRenderInput) -> str:
        """Render page content through the configured templates."""
        ...

    def render_fragment(self, *, template_name: str, context: dict[str, object]) -> str | None:
        """Render one optional fragment template."""
        ...
