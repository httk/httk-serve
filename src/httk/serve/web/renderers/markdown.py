from pathlib import Path

import markdown

from httk.serve.web.widgets.extraction import markdown_source

from ._frontmatter import split_front_matter
from .base import RenderResult


class MarkdownRenderer:
    def render(self, source_path: Path) -> RenderResult:
        source = source_path.read_text(encoding="utf-8")
        metadata, body = split_front_matter(source)
        frontmatter_lines = len(source.splitlines()) - len(body.splitlines())
        widget_source, widgets = markdown_source(body, source_path, line_offset=frontmatter_lines)

        html = markdown.markdown(
            widget_source,
            output_format="html",
            extensions=["fenced_code", "codehilite", "tables"],
        )

        return RenderResult(html=html, metadata=dict(metadata), widgets=widgets)
