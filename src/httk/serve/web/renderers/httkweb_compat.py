"""Render legacy ``.httkweb`` source files."""

from pathlib import Path

from docutils.core import publish_doctree, publish_from_doctree
from docutils.writers.html5_polyglot import Writer

from ._frontmatter import split_front_matter
from .base import RenderResult
from .rst import RstRenderer


class HttkwebCompatRenderer:
    """
    Minimal compatibility renderer for legacy .httkweb files.

    Old .httkweb pages typically have YAML-like front matter bounded by dashes,
    followed by text that is close to reStructuredText.
    """

    def render(self, source_path: Path) -> RenderResult:
        """Render one legacy ``.httkweb`` source file.

        :param source_path: Legacy source file to render.
        :return: Rendered HTML, metadata, and widget placements.
        """
        source = source_path.read_text(encoding="utf-8")
        metadata, body = split_front_matter(source)
        document = publish_doctree(
            body,
            source_path=str(source_path),
            settings_overrides={
                "raw_enabled": False,
                "file_insertion_enabled": False,
            },
        )
        line_offset = len(source.splitlines()) - len(body.splitlines())
        widgets = RstRenderer()._extract_widgets(document, source_path, line_offset=line_offset)
        writer = Writer()
        publish_from_doctree(
            document,
            writer=writer,
            settings_overrides={"raw_enabled": False, "file_insertion_enabled": False},
        )
        html = writer.parts.get("html_body", "")
        return RenderResult(html=html, metadata=dict(metadata), widgets=widgets)
