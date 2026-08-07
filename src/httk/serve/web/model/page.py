"""Define immutable route, render, and publication result models."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from httk.serve.web.widgets.core import WidgetAsset


@dataclass(frozen=True)
class ResolvedRoute:
    """Describe how a requested route was resolved.

    :param kind: Resolution kind: static, content, or missing.
    :param route: Normalized route text.
    :param source_path: Matching source path when one exists.
    """

    kind: Literal["static", "content", "missing"]
    route: str
    source_path: Path | None = None


@dataclass(frozen=True)
class PageResult:
    """Carry one rendered page response and its publication metadata.

    :param status_code: HTTP status code for the rendered page.
    :param content_type: Response content type.
    :param body: Rendered response bytes.
    :param metadata: Page metadata exposed to templates.
    :param warnings: Non-fatal rendering warnings.
    :param assets: Trusted widget assets used by the page.
    """

    status_code: int
    content_type: str
    body: bytes
    metadata: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    assets: tuple[WidgetAsset, ...] = ()


@dataclass(frozen=True)
class PublishReport:
    """Report files written and warnings collected during publication.

    :param written_files: Output files written by publication.
    :param warnings: Non-fatal publication warnings.
    """

    written_files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
