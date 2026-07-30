from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WidgetPlacement:
    """A widget invocation replaced by a stable renderer placeholder."""

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
    html: str
    metadata: dict[str, object]
    widgets: tuple[WidgetPlacement, ...] = ()


class Renderer(Protocol):
    def render(self, source_path: Path) -> RenderResult: ...
