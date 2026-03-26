from pathlib import Path

from docutils.core import publish_parts
from docutils.writers.html5_polyglot import Writer

from ._frontmatter import split_front_matter
from .base import RenderResult


class HttkwebCompatRenderer:
    """
    Minimal compatibility renderer for legacy .httkweb files.

    Old .httkweb pages typically have YAML-like front matter bounded by dashes,
    followed by text that is close to reStructuredText.
    """

    def render(self, source_path: Path) -> RenderResult:
        source = source_path.read_text(encoding="utf-8")
        metadata, body = split_front_matter(source)
        html = publish_parts(
            body,
            writer=Writer(),
            settings_overrides={
                "raw_enabled": False,
                "file_insertion_enabled": False,
            },
        ).get("html_body", "")
        return RenderResult(html=html, metadata=dict(metadata))
