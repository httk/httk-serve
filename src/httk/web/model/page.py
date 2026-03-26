from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class ResolvedRoute:
    kind: Literal["static", "content", "missing"]
    route: str
    source_path: Path | None = None


@dataclass(frozen=True)
class PageResult:
    status_code: int
    content_type: str
    body: bytes
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PublishReport:
    written_files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
