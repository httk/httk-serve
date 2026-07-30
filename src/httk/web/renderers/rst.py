from pathlib import Path
from typing import Any

from docutils import nodes
from docutils.core import publish_doctree, publish_from_doctree
from docutils.parsers.rst import Parser
from docutils.readers.standalone import Reader
from docutils.writers.html5_polyglot import Writer

from httk.web.widgets.extraction import placement_for

from .base import RenderResult, WidgetPlacement

_RST_SETTINGS_OVERRIDES = {
    "raw_enabled": False,
    "file_insertion_enabled": False,
}


class RstRenderer:
    def render(self, source_path: Path) -> RenderResult:
        source = source_path.read_text(encoding="utf-8")

        # Docutils is used as the block parser here, not as a rendered-HTML
        # postprocessor: only paragraph nodes can become widget placements.
        publish_kwargs: dict[str, Any] = {
            "source": source,
            "source_path": str(source_path),
            "reader": Reader(),
            "parser": Parser(),
            "settings": None,
            "settings_spec": None,
            "settings_overrides": _RST_SETTINGS_OVERRIDES,
            "config_section": None,
            "enable_exit_status": False,
        }
        document = publish_doctree(**publish_kwargs)
        widgets = self._extract_widgets(document, source_path)
        writer = Writer()
        publish_from_doctree(
            document,
            writer=writer,
            settings_overrides=_RST_SETTINGS_OVERRIDES,
            enable_exit_status=False,
        )
        html = writer.parts.get("html_body", "")
        metadata = self._extract_metadata(document)
        return RenderResult(html=html, metadata=metadata, widgets=widgets)

    def _extract_widgets(
        self, doctree: nodes.document, source_path: Path, *, line_offset: int = 0
    ) -> tuple[WidgetPlacement, ...]:
        placements: list[WidgetPlacement] = []
        for paragraph in doctree.findall(nodes.paragraph):
            raw_text = paragraph.rawsource.strip()
            escaped = raw_text.startswith("\\{{ widget(") and raw_text.endswith(" }}")
            active = raw_text.startswith("{{ widget(") and raw_text.endswith(" }}")
            if not escaped and not active:
                continue
            line = (paragraph.line or 1) + line_offset
            if escaped:
                paragraph.clear()
                paragraph += nodes.Text(raw_text[1:])
                continue
            placement = placement_for(raw_text, source_path=source_path, line=line, column=1, index=len(placements))
            paragraph.clear()
            paragraph += nodes.Text(placement.placeholder)
            placements.append(placement)
        return tuple(placements)

    def _extract_metadata(self, doctree: nodes.document) -> dict[str, object]:
        metadata: dict[str, object] = {}

        for field in doctree.findall(nodes.field):
            if len(field.children) < 2:
                continue

            name_node = field.children[0]
            body_node = field.children[1]
            name = name_node.astext().strip().lower()

            if name.endswith("-list"):
                key = name[: -len("-list")]
                values: list[str] = []
                for item in body_node.findall(nodes.list_item):
                    text = item.astext().strip()
                    if text:
                        values.append(text)
                if not values:
                    text = body_node.astext().strip()
                    values = [x.strip() for x in text.splitlines() if x.strip()]
                metadata[key] = values
            else:
                metadata[name] = body_node.astext().strip()

        return metadata
