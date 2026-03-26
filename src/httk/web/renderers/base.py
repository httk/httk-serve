from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class RenderResult:
    html: str
    metadata: dict[str, object]


class Renderer(Protocol):
    def render(self, source_path: Path) -> RenderResult: ...
