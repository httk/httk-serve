from mimetypes import guess_type

from markupsafe import Markup

from httk.web.engine.discovery import normalize_route, resolve_route
from httk.web.functions import PythonFunctionHandler
from httk.web.model.config import SiteConfig
from httk.web.model.errors import FunctionInjectionError, NotFoundError
from httk.web.model.page import PageResult, ResolvedRoute
from httk.web.model.request import HttpRequestContext
from httk.web.renderers import RENDERERS_BY_SUFFIX
from httk.web.templating import (
    HttkCompatTemplateEngine,
    JinjaTemplateEngine,
    TemplateEngine,
    TemplateRenderInput,
)


class SiteEngine:
    def __init__(self, config: SiteConfig) -> None:
        self.config = config
        self.template_engine: TemplateEngine
        if config.compatibility_mode:
            self.template_engine = HttkCompatTemplateEngine(template_dir=config.template_dir)
        else:
            self.template_engine = JinjaTemplateEngine(template_dir=config.template_dir)
        self.function_handler = PythonFunctionHandler(functions_dir=config.functions_dir)

    def resolve(self, route: str) -> ResolvedRoute:
        return resolve_route(config=self.config, route=route)

    def render(
        self,
        route: str,
        query: dict[str, str] | None = None,
        request: HttpRequestContext | None = None,
    ) -> PageResult:
        resolved = self.resolve(route)

        if resolved.kind == "missing" or resolved.source_path is None:
            raise NotFoundError(f"Route not found: {resolved.route}")

        if resolved.kind == "static":
            content_type = guess_type(str(resolved.source_path))[0] or "application/octet-stream"
            return PageResult(status_code=200, content_type=content_type, body=resolved.source_path.read_bytes())

        if request is None:
            request_context = HttpRequestContext(query=dict(query or {}))
        else:
            request_context = request
            if query is not None:
                request_context = HttpRequestContext(
                    method=request.method,
                    query=dict(query),
                    postvars=request.postvars,
                    headers=request.headers,
                )

        query_params = dict(request_context.query)
        postvars = dict(request_context.postvars)
        request_params = dict(query_params)
        request_params.update(postvars)
        rendered_html, metadata = self._render_content_without_templates(resolved)
        route_key = normalize_route(route)
        warnings: list[str] = []

        template_name = self._metadata_string(metadata, "template", default="default")
        base_template_name = self._metadata_string(metadata, "base_template", default="base_default")

        context = self._build_template_context(
            route_key=route_key,
            metadata=metadata,
            query=query_params,
            postvars=postvars,
            request=request_context,
        )
        self._apply_function_injections(
            metadata=metadata,
            context=context,
            params=request_params,
            route_key=route_key,
            warnings=warnings,
        )

        content_html = self.template_engine.render(
            TemplateRenderInput(
                content_html=rendered_html,
                template_name=template_name,
                base_template_name=base_template_name,
                context=context,
            )
        )

        return PageResult(
            status_code=200,
            content_type="text/html; charset=utf-8",
            body=content_html.encode("utf-8"),
            metadata=metadata,
            warnings=warnings,
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

    def _build_template_context(
        self,
        *,
        route_key: str,
        metadata: dict[str, object],
        query: dict[str, str],
        postvars: dict[str, str],
        request: HttpRequestContext,
    ) -> dict[str, object]:
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
        context["query"] = dict(query)
        context["postvars"] = dict(postvars)
        context["request"] = request
        context["page"] = {
            "relurl": route_key,
            "absurl": self._absolute_url(route_key),
            "relbaseurl": self._relative_base(route_key),
        }
        return context

    def _apply_function_injections(
        self,
        *,
        metadata: dict[str, object],
        context: dict[str, object],
        params: dict[str, str],
        route_key: str,
        warnings: list[str],
    ) -> None:
        function_keys = [key for key in metadata if key.endswith("-function")]

        for function_key in function_keys:
            try:
                raw_spec = metadata.get(function_key)
                if not isinstance(raw_spec, str):
                    del metadata[function_key]
                    continue

                output_name = function_key[: -len("-function")]
                function_name, arg_specs, function_template = self._parse_function_spec(raw_spec)
                required = [x.strip() for x in arg_specs.split(",") if x.strip()]

                if not self._function_args_satisfied(required, params):
                    context[output_name] = ""
                    metadata[output_name] = ""
                    warnings.append(
                        f"Function '{function_name}' on route '{route_key}' skipped: input parameter constraints not satisfied."
                    )
                    del metadata[function_key]
                    continue

                result = self.function_handler.execute(function_name=function_name, params=params, global_data=context)
                joint_context = dict(context)
                joint_context["result"] = result

                fragment = self.template_engine.render_fragment(template_name=function_template, context=joint_context)
                if fragment is None:
                    output = str(result)
                    warnings.append(
                        f"Function '{function_name}' on route '{route_key}' rendered without template '{function_template}'."
                    )
                else:
                    output = Markup(fragment)

                context[output_name] = output
                metadata[output_name] = output
                del metadata[function_key]
            except Exception as exc:
                raise FunctionInjectionError(f"Failed processing function metadata '{function_key}': {exc}") from exc

    def _parse_function_spec(self, spec: str) -> tuple[str, str, str]:
        parts = spec.split(":", 2)
        if len(parts) != 3:
            raise ValueError(f"Invalid function spec: {spec}")

        function_name = parts[0].strip()
        function_args = parts[1].strip()
        function_template = parts[2].strip()
        if not function_name:
            raise ValueError(f"Invalid function name in spec: {spec}")
        return function_name, function_args, function_template

    def _function_args_satisfied(self, arg_specs: list[str], query: dict[str, str]) -> bool:
        for arg_spec in arg_specs:
            if arg_spec.startswith("?"):
                continue
            if arg_spec.startswith("!"):
                if arg_spec[1:] in query:
                    return False
                continue
            if arg_spec not in query:
                return False
        return True

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

    def _metadata_string(self, metadata: dict[str, object], key: str, *, default: str | None = None) -> str | None:
        value = metadata.get(key)
        if value is None and key:
            title_case_key = f"{key[0].upper()}{key[1:]}"
            value = metadata.get(title_case_key)

        if value is None:
            return default
        if isinstance(value, str):
            stripped = value.strip()
            return stripped if stripped else default
        return default
