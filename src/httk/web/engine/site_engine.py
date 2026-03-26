from mimetypes import guess_type

from httk.web.engine.discovery import resolve_route
from httk.web.model.config import SiteConfig
from httk.web.model.errors import NotFoundError
from httk.web.model.page import PageResult, ResolvedRoute
from httk.web.renderers import RENDERERS_BY_SUFFIX


class SiteEngine:
    def __init__(self, config: SiteConfig) -> None:
        self.config = config

    def resolve(self, route: str) -> ResolvedRoute:
        return resolve_route(config=self.config, route=route)

    def render(self, route: str) -> PageResult:
        resolved = self.resolve(route)

        if resolved.kind == "missing" or resolved.source_path is None:
            raise NotFoundError(f"Route not found: {resolved.route}")

        if resolved.kind == "static":
            content_type = guess_type(str(resolved.source_path))[0] or "application/octet-stream"
            return PageResult(status_code=200, content_type=content_type, body=resolved.source_path.read_bytes())

        suffix = resolved.source_path.suffix.lower()
        renderer = RENDERERS_BY_SUFFIX.get(suffix)
        if renderer is None:
            raise NotFoundError(f"No renderer for content suffix: {suffix}")

        rendered = renderer.render(resolved.source_path)
        return PageResult(
            status_code=200,
            content_type="text/html; charset=utf-8",
            body=rendered.html.encode("utf-8"),
            metadata=rendered.metadata,
        )
