from pathlib import Path

from .base import RenderResult


class HtmlRenderer:
    def render(self, source_path: Path) -> RenderResult:
        html = source_path.read_text(encoding="utf-8")
        metadata: dict[str, object] = {"name": source_path.stem}
        return RenderResult(html=html, metadata=metadata)
