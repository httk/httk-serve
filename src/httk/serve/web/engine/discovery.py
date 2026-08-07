"""Resolve site routes to static files and renderable content sources."""

from pathlib import Path

from httk.serve.web.model.config import SiteConfig
from httk.serve.web.model.page import ResolvedRoute

DEFAULT_CONTENT_EXTENSIONS: tuple[str, ...] = (".md", ".rst", ".html", ".httkweb")


def normalize_route(route: str) -> str:
    """Normalize a site route for lookup.

    :param route: Route text supplied by a caller or request.
    :return: Route without a leading slash, using ``index`` for the root.
    """
    normalized = route.lstrip("/").strip()
    if normalized in {"", "."}:
        return "index"
    return normalized


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def resolve_route(config: SiteConfig, route: str) -> ResolvedRoute:
    """Resolve a route against the configured static and content directories.

    :param config: Site directory configuration.
    :param route: Route to resolve.
    :return: The matching static, content, or missing route description.
    """
    normalized = normalize_route(route)

    static_candidate = (config.static_dir / normalized).resolve()
    if static_candidate.exists() and static_candidate.is_file() and _is_within(static_candidate, config.static_dir):
        return ResolvedRoute(kind="static", route=normalized, source_path=static_candidate)

    content = _resolve_content_route(content_dir=config.content_dir, route=normalized)
    if content is not None:
        return ResolvedRoute(kind="content", route=normalized, source_path=content)

    return ResolvedRoute(kind="missing", route=normalized)


def _resolve_content_route(content_dir: Path, route: str) -> Path | None:
    route_path = Path(route)

    if route_path.suffix:
        candidate = (content_dir / route_path).resolve()
        if candidate.exists() and candidate.is_file() and _is_within(candidate, content_dir):
            return candidate

    for ext in DEFAULT_CONTENT_EXTENSIONS:
        candidate = (content_dir / route_path).with_suffix(ext).resolve()
        if candidate.exists() and candidate.is_file() and _is_within(candidate, content_dir):
            return candidate

    return None
