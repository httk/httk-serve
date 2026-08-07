"""Define renderer inputs and outputs shared by content renderers."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WidgetPlacement:
    """Describe a widget invocation replaced by a stable placeholder.

    :param placeholder: Placeholder text inserted into rendered content.
    :param name: Requested widget name.
    :param props: Literal widget properties.
    :param source_path: Source file containing the invocation.
    :param line: One-based source line.
    :param column: One-based source column.
    :param snippet: Original invocation text.
    """

    placeholder: str
    name: str
    props: Mapping[str, object]
    source_path: Path
    line: int
    column: int
    snippet: str


from typing import Protocol


@dataclass(frozen=True)
class RenderResult:
    """Carry rendered HTML, metadata, and widget placements.

    :param html: Rendered HTML before templates are applied.
    :param metadata: Source metadata.
    :param widgets: Widget placements found in the source.
    """

    html: str
    metadata: dict[str, object]
    widgets: tuple[WidgetPlacement, ...] = ()


class Renderer(Protocol):
    """Define the source-renderer protocol."""

    def render(self, source_path: Path) -> RenderResult:
        """Render a source file into HTML and metadata."""
        ...
