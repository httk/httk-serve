from pathlib import Path

from docutils import io, nodes
from docutils.core import publish_programmatically
from docutils.writers.html5_polyglot import Writer

from .base import RenderResult

_RST_SETTINGS_OVERRIDES = {
    "raw_enabled": False,
    "file_insertion_enabled": False,
}


class RstRenderer:
    def render(self, source_path: Path) -> RenderResult:
        source = source_path.read_text(encoding="utf-8")

        _, pub = publish_programmatically(
            source_class=io.StringInput,
            source=source,
            source_path=str(source_path),
            destination_class=io.StringOutput,
            destination=None,
            destination_path=None,
            reader=None,
            reader_name="standalone",
            parser=None,
            parser_name="restructuredtext",
            writer=Writer(),
            writer_name=None,
            settings=None,
            settings_spec=None,
            settings_overrides=_RST_SETTINGS_OVERRIDES,
            config_section=None,
            enable_exit_status=False,
        )
        html = pub.writer.parts.get("html_body", "")
        metadata = self._extract_metadata(pub.document)
        return RenderResult(html=html, metadata=metadata)

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
