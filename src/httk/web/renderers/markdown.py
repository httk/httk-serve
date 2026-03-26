from pathlib import Path

import markdown

from ._frontmatter import split_front_matter
from .base import RenderResult


class MarkdownRenderer:
    def render(self, source_path: Path) -> RenderResult:
        source = source_path.read_text(encoding="utf-8")
        metadata, body = split_front_matter(source)

        html = markdown.markdown(
            body,
            output_format="html5",
            extensions=["fenced_code", "codehilite", "tables"],
        )

        return RenderResult(html=html, metadata=dict(metadata))
