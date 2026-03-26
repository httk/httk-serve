from mimetypes import guess_type

from httk.web.engine.discovery import normalize_route, resolve_route
from httk.web.model.config import SiteConfig
from httk.web.model.errors import NotFoundError
from httk.web.model.page import PageResult, ResolvedRoute
from httk.web.renderers import RENDERERS_BY_SUFFIX
from httk.web.templating import JinjaTemplateEngine, TemplateRenderInput


class SiteEngine:
    def __init__(self, config: SiteConfig) -> None:
        self.config = config
        self.template_engine = JinjaTemplateEngine(template_dir=config.template_dir)

    def resolve(self, route: str) -> ResolvedRoute:
        return resolve_route(config=self.config, route=route)

    def render(self, route: str) -> PageResult:
        resolved = self.resolve(route)

        if resolved.kind == "missing" or resolved.source_path is None:
            raise NotFoundError(f"Route not found: {resolved.route}")

        if resolved.kind == "static":
            content_type = guess_type(str(resolved.source_path))[0] or "application/octet-stream"
            return PageResult(status_code=200, content_type=content_type, body=resolved.source_path.read_bytes())

        rendered_html, metadata = self._render_content_without_templates(resolved)
        route_key = normalize_route(route)

        template_name = self._as_optional_str(metadata.get("template"), default="default")
        base_template_name = self._as_optional_str(metadata.get("base_template"), default="base_default")

        content_html = self.template_engine.render(
            TemplateRenderInput(
                content_html=rendered_html,
                template_name=template_name,
                base_template_name=base_template_name,
                context=self._build_template_context(route_key=route_key, metadata=metadata),
            )
        )

        return PageResult(
            status_code=200,
            content_type="text/html; charset=utf-8",
            body=content_html.encode("utf-8"),
            metadata=metadata,
        )

    def _render_content_without_templates(self, resolved: ResolvedRoute) -> tuple[str, dict[str, object]]:
        if resolved.kind != "content" or resolved.source_path is None:
            raise NotFoundError(f"Route is not content: {resolved.route}")

        suffix = resolved.source_path.suffix.lower()
        renderer = RENDERERS_BY_SUFFIX.get(suffix)
        if renderer is None:
            raise NotFoundError(f"No renderer for content suffix: {suffix}")

        rendered = renderer.render(resolved.source_path)
        return rendered.html, dict(rendered.metadata)

    def _build_template_context(self, *, route_key: str, metadata: dict[str, object]) -> dict[str, object]:
        context: dict[str, object] = dict(metadata)
        page_cache: dict[str, tuple[str, dict[str, object]]] = {}

        def first_value(*values: object) -> object:
            for value in values:
                if value:
                    return value
            if values:
                return values[-1]
            return None

        def listdir(path: str, filters: str = "", limit: int | None = None) -> list[str]:
            content_root = self.config.content_dir
            target = (content_root / path).resolve()
            try:
                target.relative_to(content_root.resolve())
            except ValueError:
                return []

            if not target.exists() or not target.is_dir():
                return []

            suffixes = [x.strip() for x in filters.split(";") if x.strip()]
            files: list[str] = []
            for child in sorted(target.iterdir()):
                if not child.is_file():
                    continue
                rel = str(child.relative_to(content_root)).replace("\\", "/")
                if suffixes and not any(rel.endswith(suffix) for suffix in suffixes):
                    continue
                files.append(rel)

            if limit is not None:
                return files[:limit]
            return files

        def pages(path: str, field: str) -> object:
            normalized = normalize_route(path)

            cached = page_cache.get(normalized)
            if cached is not None:
                page_html, page_metadata = cached
            else:
                target = self.resolve(path)
                if target.kind != "content" or target.source_path is None:
                    return None
                page_html, page_metadata = self._render_content_without_templates(target)
                page_cache[normalized] = (page_html, page_metadata)

            if field in page_metadata:
                return page_metadata[field]
            if field in {"content", "html"}:
                return page_html
            if field == "relurl":
                return normalized
            return None

        context["first_value"] = first_value
        context["listdir"] = listdir
        context["pages"] = pages
        context["page"] = {
            "relurl": route_key,
            "absurl": self._absolute_url(route_key),
            "relbaseurl": self._relative_base(route_key),
        }
        return context

    def _absolute_url(self, route_key: str) -> str:
        if self.config.baseurl is None:
            return route_key
        base = self.config.baseurl
        if not base.endswith("/"):
            base += "/"
        return f"{base}{route_key}"

    def _relative_base(self, route_key: str) -> str:
        depth = max(0, route_key.count("/"))
        if depth == 0:
            return "."
        return "/".join(".." for _ in range(depth))

    def _as_optional_str(self, value: object, *, default: str | None = None) -> str | None:
        if value is None:
            return default
        if isinstance(value, str):
            stripped = value.strip()
            return stripped if stripped else default
        return default
