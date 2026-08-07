"""Render HTML source files while extracting literal widget invocations."""

from pathlib import Path

from httk.serve.web.widgets.extraction import html_source

from .base import RenderResult


class HtmlRenderer:
    """Render HTML source files."""

    def render(self, source_path: Path) -> RenderResult:
        """Render one HTML source file.

        :param source_path: HTML source file to render.
        :return: Rendered HTML, metadata, and widget placements.
        """
        source = source_path.read_text(encoding="utf-8")
        html, widgets = html_source(source, source_path)
        metadata: dict[str, object] = {"name": source_path.stem}
        return RenderResult(html=html, metadata=metadata, widgets=widgets)
