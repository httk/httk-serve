from pathlib import Path

from httk.web.widgets.extraction import html_source

from .base import RenderResult


class HtmlRenderer:
    def render(self, source_path: Path) -> RenderResult:
        source = source_path.read_text(encoding="utf-8")
        html, widgets = html_source(source, source_path)
        metadata: dict[str, object] = {"name": source_path.stem}
        return RenderResult(html=html, metadata=metadata, widgets=widgets)
