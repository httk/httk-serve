"""Render site content, templates, widgets, and request-backed pages."""

import inspect
import posixpath
import re
from hashlib import sha256
from mimetypes import guess_type
from types import TracebackType
from typing import ClassVar, Literal, Self
from urllib.parse import SplitResult, urlsplit, urlunsplit

from markupsafe import Markup

from httk.serve.web.engine.discovery import normalize_route, resolve_route
from httk.serve.web.functions import PythonFunctionHandler
from httk.serve.web.model.config import SiteConfig
from httk.serve.web.model.errors import (
    FunctionInjectionError,
    NotFoundError,
    WidgetDiscoveryError,
    WidgetRenderingError,
    WidgetValidationError,
)
from httk.serve.web.model.page import PageResult, ResolvedRoute
from httk.serve.web.model.request import HttpRequestContext
from httk.serve.web.renderers import RENDERERS_BY_SUFFIX
from httk.serve.web.renderers.base import RenderResult, WidgetPlacement
from httk.serve.web.resources import SITE_RESOURCES_KEY, SiteResources
from httk.serve.web.templating import (
    HttkCompatTemplateEngine,
    JinjaTemplateEngine,
    TemplateEngine,
    TemplateRenderInput,
)
from httk.serve.web.widgets import SiteWidgetLoader, WidgetAsset, WidgetContext, WidgetRenderResult
from httk.serve.web.widgets.assets import WidgetAssetConflictError, WidgetAssetRegistry
from httk.serve.web.widgets.core import BUILTIN_WIDGETS, Widget, _immutable_mapping
from httk.serve.web.widgets.table import TableRuntime


class SiteEngine:
    """Render a configured site and own its engine-local resources.

    :param config: Site directory and rendering configuration.
    :param table_token_secret: Secret used to authenticate table continuation tokens.
    """

    def __init__(self, config: SiteConfig, *, table_token_secret: str | bytes | None = None) -> None:
        self.config = config
        self.resources = SiteResources()
        self.template_engine: TemplateEngine
        if config.compatibility_mode:
            self.template_engine = HttkCompatTemplateEngine(template_dir=config.template_dir)
        else:
            self.template_engine = JinjaTemplateEngine(template_dir=config.template_dir)
        self.function_handler = PythonFunctionHandler(functions_dir=config.functions_dir)
        self.widget_loader = SiteWidgetLoader(config.widgets_dir)
        self.widget_assets = WidgetAssetRegistry()
        self.global_data: dict[str, object] = self._load_global_config_metadata()
        self.global_data[SITE_RESOURCES_KEY] = self.resources
        self._publish_route_mode_cache: dict[str, str] = {}
        try:
            self._run_init_function()
            self.table_runtime = TableRuntime(engine=self, token_secret=table_token_secret)
        except BaseException as exc:
            self._close_after_failed_initialization(exc)
            raise

    def close(self) -> None:
        """Release resources registered by site startup code exactly once."""

        self.resources.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        try:
            self.close()
        except BaseException as cleanup_error:
            if exc_value is None:
                raise
            exc_value.add_note(f"Additional site resource cleanup failure: {cleanup_error!r}")
        return False

    def resolve(self, route: str) -> ResolvedRoute:
        """Resolve a route without rendering its contents.

        :param route: Site route to resolve.
        :return: Description of the matching static, content, or missing route.
        """
        return resolve_route(config=self.config, route=route)

    def render(
        self,
        route: str,
        query: dict[str, str] | None = None,
        request: HttpRequestContext | None = None,
    ) -> PageResult:
        """Render one site route for serving or static publication.

        :param route: Site route to render.
        :param query: Optional query parameters for non-request rendering.
        :param request: Optional request context; its presence selects live rendering.
        :return: Rendered response body and publication metadata.
        :raises httk.serve.web.model.errors.NotFoundError: If the route has no renderable source.
        :raises httk.serve.web.model.errors.WidgetError: If widget extraction or rendering fails.
        """
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
        route_key = normalize_route(route)
        warnings: list[str] = []

        render_mode = "serve" if request is not None else "publish"
        rendered = self._render_content_without_templates(resolved)
        rendered_html, metadata = rendered.html, dict(rendered.metadata)
        self._publish_route_mode_cache[route_key] = self._publish_route_mode_from_metadata(metadata)
        template_name = self._metadata_string(
            metadata,
            f"template_{render_mode}",
            default=self._metadata_string(metadata, "template", default="default"),
        )
        base_template_name = self._metadata_string(
            metadata,
            f"base_template_{render_mode}",
            default=self._metadata_string(metadata, "base_template", default="base_default"),
        )

        context = self._build_template_context(
            route_key=route_key,
            metadata=metadata,
            query=query_params,
            postvars=postvars,
            request=request_context,
            render_mode=render_mode,
        )
        rendered_html, assets, asset_origins = self._expand_widgets(
            rendered_html,
            placements=rendered.widgets,
            route_key=route_key,
            render_mode=render_mode,
            query=query_params,
            postvars=postvars,
            page=context["page"],
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
        if render_mode == "publish":
            content_html = self._rewrite_publish_links(content_html, route_key=route_key)

        try:
            self.widget_assets.register_many(assets)
        except WidgetAssetConflictError as exc:
            placement, widget_id = asset_origins[exc.path]
            raise WidgetRenderingError(
                str(exc),
                source_path=placement.source_path,
                line=placement.line,
                column=placement.column,
                snippet=placement.snippet,
                widget_name=placement.name,
                widget_id=widget_id,
                correction="declare one consistent asset for each internal asset path",
            ) from exc

        return PageResult(
            status_code=200,
            content_type="text/html; charset=utf-8",
            body=content_html.encode("utf-8"),
            metadata=metadata,
            warnings=warnings,
            assets=assets,
        )

    def _render_content_without_templates(self, resolved: ResolvedRoute) -> RenderResult:
        if resolved.kind != "content" or resolved.source_path is None:
            raise NotFoundError(f"Route is not content: {resolved.route}")

        suffix = resolved.source_path.suffix.lower()
        renderer = RENDERERS_BY_SUFFIX.get(suffix)
        if renderer is None:
            raise NotFoundError(f"No renderer for content suffix: {suffix}")

        rendered = renderer.render(resolved.source_path)
        return rendered

    def _expand_widgets(
        self,
        html: str,
        *,
        placements: tuple[WidgetPlacement, ...],
        route_key: str,
        render_mode: str,
        query: dict[str, str],
        postvars: dict[str, str],
        page: object,
    ) -> tuple[str, tuple[WidgetAsset, ...], dict[str, tuple[WidgetPlacement, str]]]:
        if not placements:
            return html, (), {}
        if not isinstance(page, dict):
            raise WidgetRenderingError("page context is unavailable")
        self._validate_widget_placeholders(html, placements)
        ids: set[str] = set()
        rendered_by_placeholder: dict[str, str] = {}
        assets: dict[str, WidgetAsset] = {}
        asset_origins: dict[str, tuple[WidgetPlacement, str]] = {}
        for placement_index, placement in enumerate(placements):
            widget_id = placement.props.get("id")
            if widget_id is None:
                widget_id = self._default_widget_id(route_key, placement, placement_index)
            assert isinstance(widget_id, str)
            if widget_id in ids:
                raise WidgetValidationError(
                    "duplicate widget id on this route",
                    source_path=placement.source_path,
                    line=placement.line,
                    column=placement.column,
                    snippet=placement.snippet,
                    widget_name=placement.name,
                    widget_id=widget_id,
                    correction="choose a unique id= value for each widget",
                )
            ids.add(widget_id)
            widget = self._resolve_widget(placement)
            context = WidgetContext(
                route=route_key,
                render_mode=render_mode,
                widget_id=widget_id,
                query=_immutable_mapping(query),
                postvars=_immutable_mapping(postvars),
                page=_immutable_mapping(page),
                source_path=placement.source_path,
                url_for=lambda target: self._route_link_url(
                    source_route_key=route_key,
                    target_route_key=normalize_route(target),
                    render_mode=render_mode,
                    relative_start=True,
                ),
                absolute_url_for=lambda target: self._absolute_url(normalize_route(target), render_mode=render_mode),
                table_runtime=self.table_runtime,
            )
            rendered = self._render_widget(widget, context, placement, widget_id)
            for asset in rendered.assets:
                existing = assets.get(asset.path)
                if existing is not None and existing != asset:
                    raise WidgetRenderingError(
                        f"conflicting widget asset declaration for {asset.path!r}",
                        source_path=placement.source_path,
                        line=placement.line,
                        column=placement.column,
                        snippet=placement.snippet,
                        widget_name=placement.name,
                        widget_id=widget_id,
                        correction="declare one consistent asset for each internal asset path",
                    )
                assets[asset.path] = asset
                asset_origins[asset.path] = (placement, widget_id)
            rendered_by_placeholder[placement.placeholder] = str(rendered.html)
        return (
            self._replace_widget_placeholders_once(html, rendered_by_placeholder),
            tuple(assets.values()),
            asset_origins,
        )

    @staticmethod
    def _default_widget_id(route_key: str, placement: WidgetPlacement, placement_index: int) -> str:
        """Stable across site-directory relocations while preserving local uniqueness."""

        material = f"{route_key}\x1f{placement.name}\x1f{placement.snippet}\x1f{placement_index}"
        return f"widget-{sha256(material.encode('utf-8')).hexdigest()[:16]}"

    def _validate_widget_placeholders(self, html: str, placements: tuple[WidgetPlacement, ...]) -> None:
        """Ensure only the renderer's one original node owns each placeholder."""

        seen: set[str] = set()
        for placement in placements:
            count = html.count(placement.placeholder)
            if placement.placeholder in seen or count != 1:
                raise WidgetValidationError(
                    f"renderer placeholder must occur exactly once (found {count})",
                    source_path=placement.source_path,
                    line=placement.line,
                    column=placement.column,
                    snippet=placement.snippet,
                    widget_name=placement.name,
                    correction="remove literal HTTK_WIDGET_ placeholder text from the source",
                )
            seen.add(placement.placeholder)

    @staticmethod
    def _replace_widget_placeholders_once(html: str, rendered_by_placeholder: dict[str, str]) -> str:
        """Replace placements in original renderer output, never widget output."""

        locations = sorted((html.index(placeholder), placeholder) for placeholder in rendered_by_placeholder)
        parts: list[str] = []
        cursor = 0
        for start, placeholder in locations:
            parts.append(html[cursor:start])
            parts.append(rendered_by_placeholder[placeholder])
            cursor = start + len(placeholder)
        parts.append(html[cursor:])
        return "".join(parts)

    def _resolve_widget(self, placement: WidgetPlacement) -> Widget:
        widget = BUILTIN_WIDGETS.resolve(placement.name)
        if widget is None:
            try:
                widget = self.widget_loader.resolve(placement.name)
            except Exception as exc:
                raise WidgetDiscoveryError(
                    str(exc),
                    source_path=placement.source_path,
                    line=placement.line,
                    column=placement.column,
                    snippet=placement.snippet,
                    widget_name=placement.name,
                ) from exc
        if widget is None:
            available = BUILTIN_WIDGETS.available() + self.widget_loader.available()
            names = [name for name, _ in available]
            nearby = ", ".join(sorted(names, key=lambda item: self._name_distance(item, placement.name))[:4]) or "none"
            locations = "; ".join(f"{name} ({source})" for name, source in available[:8]) or "no widgets found"
            raise WidgetDiscoveryError(
                f"unknown widget; nearby names: {nearby}. Available: {locations}",
                source_path=placement.source_path,
                line=placement.line,
                column=placement.column,
                snippet=placement.snippet,
                widget_name=placement.name,
                correction='create src/widgets/<name>.py with render(context, **props), or use a listed name',
            )
        return widget

    @staticmethod
    def _name_distance(left: str, right: str) -> int:
        return abs(len(left) - len(right)) + sum(a != b for a, b in zip(left, right, strict=False))

    def _render_widget(
        self, widget: Widget, context: WidgetContext, placement: WidgetPlacement, widget_id: str
    ) -> WidgetRenderResult:
        try:
            signature_target = getattr(widget, "render_function", widget.render)
            inspect.signature(signature_target).bind(context, **dict(placement.props))
        except TypeError as exc:
            raise WidgetValidationError(
                f"properties do not match widget render signature: {exc}",
                source_path=placement.source_path,
                line=placement.line,
                column=placement.column,
                snippet=placement.snippet,
                widget_name=placement.name,
                widget_id=widget_id,
                correction="adjust the literal properties or the widget render() signature",
            ) from exc
        try:
            output = widget.render(context, **dict(placement.props))
        except Exception as exc:
            raise WidgetRenderingError(
                str(exc),
                source_path=placement.source_path,
                line=placement.line,
                column=placement.column,
                snippet=placement.snippet,
                widget_name=placement.name,
                widget_id=widget_id,
                correction="inspect the widget render() implementation",
            ) from exc
        if isinstance(output, WidgetRenderResult):
            return output
        if isinstance(output, Markup):
            return WidgetRenderResult(str(output))
        if isinstance(output, str):
            return WidgetRenderResult(str(Markup.escape(output)))
        raise WidgetRenderingError(
            "render() must return str, Markup, or WidgetRenderResult",
            source_path=placement.source_path,
            line=placement.line,
            column=placement.column,
            snippet=placement.snippet,
            widget_name=placement.name,
            widget_id=widget_id,
        )

    def _build_template_context(
        self,
        *,
        route_key: str,
        metadata: dict[str, object],
        query: dict[str, str],
        postvars: dict[str, str],
        request: HttpRequestContext,
        render_mode: str,
    ) -> dict[str, object]:
        context: dict[str, object] = dict(self.global_data)
        context.update(metadata)
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
                rendered_page = self._render_content_without_templates(target)
                page_html, page_metadata = rendered_page.html, dict(rendered_page.metadata)
                page_cache[normalized] = (page_html, page_metadata)

            metadata_value = self._metadata_field_value(page_metadata, field)
            if metadata_value is not None:
                return metadata_value
            if field in {"content", "html"}:
                return page_html
            if field == "relurl":
                return self._route_link_url(
                    source_route_key=route_key,
                    target_route_key=normalized,
                    render_mode=render_mode,
                    relative_start=False,
                )
            return None

        context["first_value"] = first_value
        context["listdir"] = listdir
        context["pages"] = pages
        context["query"] = dict(query)
        context["postvars"] = dict(postvars)
        context["request"] = request
        page_data = {
            key: value
            for key, value in metadata.items()
            if isinstance(key, str) and key and not key.startswith("_") and not key.lower().endswith("-function")
        }
        page_data.update(
            {
                "relurl": self._route_link_url(
                    source_route_key=route_key,
                    target_route_key=route_key,
                    render_mode=render_mode,
                    relative_start=False,
                ),
                "absurl": self._absolute_url(route_key, render_mode=render_mode),
                "relbaseurl": self._relative_base(route_key),
                "functionurl": self._function_url(route_key, render_mode=render_mode),
            }
        )
        context["page"] = page_data
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
        function_keys = [key for key in metadata if isinstance(key, str) and key.lower().endswith("-function")]

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

    def _absolute_url(self, route_key: str, *, render_mode: str) -> str:
        route_path = self._route_url_path(route_key, render_mode=render_mode)
        route_host = self._route_host(route_key, render_mode=render_mode)
        if route_host is None:
            return route_path
        return self._join_host_path(route_host, route_path)

    def _function_url(self, route_key: str, *, render_mode: str) -> str:
        route_path = self._route_url_path(route_key, render_mode=render_mode)
        if render_mode != "publish" or self.config.host_static is None:
            return self._absolute_url(route_key, render_mode=render_mode)
        if self.config.baseurl is None:
            return route_path
        return self._join_host_path(self.config.baseurl, route_path)

    def _route_link_url(
        self,
        *,
        source_route_key: str,
        target_route_key: str,
        render_mode: str,
        relative_start: bool,
    ) -> str:
        target_path = self._route_url_path(target_route_key, render_mode=render_mode)
        if render_mode != "publish" or self.config.host_static is None:
            if relative_start:
                return self._format_rewritten_path(target_path, route_key=source_route_key, target_path=target_path)
            return target_path

        source_host = self._route_host(source_route_key, render_mode=render_mode)
        target_host = self._route_host(target_route_key, render_mode=render_mode)
        if (
            source_host is not None
            and target_host is not None
            and self._host_key(source_host) != self._host_key(target_host)
        ):
            return self._join_host_path(target_host, target_path)

        if relative_start:
            return self._format_rewritten_path(target_path, route_key=source_route_key, target_path=target_path)
        return target_path

    def _route_host(self, route_key: str, *, render_mode: str) -> str | None:
        if render_mode != "publish":
            return self.config.baseurl
        if self.config.baseurl is None:
            return None
        if self.config.host_static is None:
            return self.config.baseurl
        route_mode = self._publish_route_mode(route_key)
        if route_mode == "static":
            return self.config.host_static
        return self.config.baseurl

    def _host_key(self, host: str) -> str:
        return host.rstrip("/")

    def _join_host_path(self, host: str, path: str) -> str:
        base = host if host.endswith("/") else f"{host}/"
        return f"{base}{path}"

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

    def _load_global_config_metadata(self) -> dict[str, object]:
        config_name = self.config.config_name.strip()
        if not config_name:
            return {}

        for suffix, renderer in RENDERERS_BY_SUFFIX.items():
            candidate = self.config.srcdir / f"{config_name}{suffix}"
            if not candidate.exists() or not candidate.is_file():
                continue
            rendered = renderer.render(candidate)
            metadata = dict(rendered.metadata)
            return self._normalize_legacy_list_keys(metadata)

        return {}

    def _run_init_function(self) -> None:
        init_file = self.config.functions_dir / "init.py"
        if not init_file.exists() or not init_file.is_file():
            return

        init_context = dict(self.global_data)
        init_context["pages"] = self._global_pages_helper
        self.function_handler.execute(function_name="init", params={}, global_data=init_context)
        if init_context.get(SITE_RESOURCES_KEY) is not self.resources:
            raise ValueError(f"functions/init.py must not replace reserved global_data[{SITE_RESOURCES_KEY!r}]")
        self.global_data.update(init_context)

    def _close_after_failed_initialization(self, startup_error: BaseException) -> None:
        """Preserve the startup error while still releasing registered resources."""

        try:
            self.close()
        except BaseException as cleanup_error:
            startup_error.add_note(f"Additional site resource cleanup failure: {cleanup_error!r}")

    def _normalize_legacy_list_keys(self, metadata: dict[str, object]) -> dict[str, object]:
        normalized = dict(metadata)
        for key, value in list(metadata.items()):
            if not isinstance(key, str) or not key.endswith("-list"):
                continue
            base_key = key[: -len("-list")]
            if isinstance(value, list):
                normalized[base_key] = value
            elif isinstance(value, str):
                normalized[base_key] = [x.strip() for x in value.split(",") if x.strip()]
            elif value is None:
                normalized[base_key] = []
            else:
                normalized[base_key] = [value]
        return normalized

    def _global_pages_helper(self, path: str, field: str) -> object:
        target = self.resolve(path)
        if target.kind != "content" or target.source_path is None:
            return None

        rendered_page = self._render_content_without_templates(target)
        page_html, page_metadata = rendered_page.html, dict(rendered_page.metadata)
        metadata_value = self._metadata_field_value(page_metadata, field)
        if metadata_value is not None:
            return metadata_value
        if field in {"content", "html"}:
            return page_html
        if field == "relurl":
            route_key = normalize_route(path)
            return self._route_link_url(
                source_route_key=route_key,
                target_route_key=route_key,
                render_mode="publish",
                relative_start=False,
            )
        return None

    def _metadata_field_value(self, metadata: dict[str, object], field: str) -> object:
        if field in metadata:
            return metadata[field]
        for key, value in metadata.items():
            if isinstance(key, str) and key.lower() == field.lower():
                return value
        return None

    def _publish_route_mode_from_metadata(self, metadata: dict[str, object]) -> str:
        override = self._publish_route_mode_override(metadata)
        if override is not None:
            return override
        has_functions = any(isinstance(key, str) and key.lower().endswith("-function") for key in metadata)
        return "dynamic" if has_functions else "static"

    def _publish_route_mode_override(self, metadata: dict[str, object]) -> str | None:
        override_keys = {"publish_mode", "publish-mode", "hosting", "host"}
        for key, value in metadata.items():
            if not isinstance(key, str) or key.lower() not in override_keys:
                continue
            if not isinstance(value, str):
                continue
            normalized = value.strip().lower()
            if normalized in {"static", "dynamic"}:
                return normalized
        return None

    def _publish_route_mode(self, route_key: str) -> str:
        cached = self._publish_route_mode_cache.get(route_key)
        if cached is not None:
            return cached

        resolved = self.resolve(route_key)
        if resolved.kind != "content" or resolved.source_path is None:
            self._publish_route_mode_cache[route_key] = "static"
            return "static"
        metadata = self._render_content_without_templates(resolved).metadata
        mode = self._publish_route_mode_from_metadata(metadata)
        self._publish_route_mode_cache[route_key] = mode
        return mode

    def _route_url_path(self, route_key: str, *, render_mode: str) -> str:
        if render_mode == "publish" and not self.config.publish_use_urls_without_ext:
            if route_key.endswith(".html"):
                return route_key
            return f"{route_key}.html"
        return route_key

    _HTML_TAG_PATTERN = re.compile(r"""<(?:[^<>"']+|"[^"]*"|'[^']*')+>""")
    _ASSET_EXTENSIONS: ClassVar[set[str]] = {
        ".css",
        ".js",
        ".json",
        ".txt",
        ".xml",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".ico",
        ".webp",
        ".avif",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".mp4",
        ".webm",
        ".mp3",
        ".wav",
    }

    def _rewrite_publish_links(self, html: str, *, route_key: str) -> str:
        if self.config.publish_use_urls_without_ext:
            return html

        route_exists_cache: dict[str, bool] = {}

        def replace_tag(match: re.Match[str]) -> str:
            tag = match.group(0)
            # Keep declarations/comments/closing tags untouched.
            if tag.startswith(("</", "<!--", "<!")):
                return tag
            return self._rewrite_tag_urls(tag, route_key=route_key, route_exists_cache=route_exists_cache)

        return self._HTML_TAG_PATTERN.sub(replace_tag, html)

    def _rewrite_tag_urls(self, tag: str, *, route_key: str, route_exists_cache: dict[str, bool]) -> str:
        n = len(tag)
        if n < 3 or not tag.startswith("<") or not tag.endswith(">"):
            return tag

        i = 1
        while i < n - 1 and tag[i].isspace():
            i += 1

        # Skip tag name.
        while i < n - 1 and not tag[i].isspace() and tag[i] not in {"/", ">"}:
            i += 1

        parts: list[str] = []
        last = 0
        while i < n - 1:
            while i < n - 1 and tag[i].isspace():
                i += 1
            if i >= n - 1 or tag[i] in {">", "/"}:
                break

            name_start = i
            while i < n - 1 and not tag[i].isspace() and tag[i] not in {"=", ">", "/"}:
                i += 1
            if i == name_start:
                i += 1
                continue
            attr_name = tag[name_start:i].lower()

            while i < n - 1 and tag[i].isspace():
                i += 1
            if i >= n - 1 or tag[i] != "=":
                continue
            i += 1

            while i < n - 1 and tag[i].isspace():
                i += 1
            if i >= n - 1:
                break

            if tag[i] in {"'", '"'}:
                quote = tag[i]
                value_start = i + 1
                value_end = tag.find(quote, value_start)
                if value_end < 0:
                    break
                raw_value = tag[value_start:value_end]
                if attr_name in {"href", "src"}:
                    rewritten_value = self._rewrite_internal_url(
                        raw_value, route_key=route_key, route_exists_cache=route_exists_cache
                    )
                    if rewritten_value is not None:
                        parts.append(tag[last:value_start])
                        parts.append(rewritten_value)
                        last = value_end
                i = value_end + 1
                continue

            # Unquoted attribute value.
            value_start = i
            while i < n - 1 and not tag[i].isspace() and tag[i] != ">":
                i += 1
            value_end = i
            raw_value = tag[value_start:value_end]
            if attr_name in {"href", "src"}:
                rewritten_value = self._rewrite_internal_url(
                    raw_value, route_key=route_key, route_exists_cache=route_exists_cache
                )
                if rewritten_value is not None:
                    parts.append(tag[last:value_start])
                    parts.append(rewritten_value)
                    last = value_end

        if not parts:
            return tag
        parts.append(tag[last:])
        return "".join(parts)

    def _rewrite_internal_url(
        self,
        url: str,
        *,
        route_key: str,
        route_exists_cache: dict[str, bool],
    ) -> str | None:
        if not url or url.startswith(("#", "//")):
            return None

        parts = urlsplit(url)
        if parts.scheme or parts.netloc:
            return None

        if not parts.path:
            return None

        # Keep clearly non-page assets untouched.
        if parts.path.endswith("/"):
            path_ext = ""
        else:
            path_ext = posixpath.splitext(parts.path)[1].lower()
        if path_ext and path_ext not in {"", ".html"} and path_ext in self._ASSET_EXTENSIONS:
            return None

        candidate_route = self._candidate_route_from_link_path(parts.path, route_key=route_key)
        if candidate_route is None:
            return None

        is_content_route = route_exists_cache.get(candidate_route)
        if is_content_route is None:
            resolved = self.resolve(candidate_route)
            is_content_route = resolved.kind == "content" and resolved.source_path is not None
            route_exists_cache[candidate_route] = is_content_route
        if not is_content_route:
            return None

        rewritten_path = self._route_link_url(
            source_route_key=route_key,
            target_route_key=candidate_route,
            render_mode="publish",
            relative_start=not parts.path.startswith("/"),
        )
        rewritten = SplitResult(parts.scheme, parts.netloc, rewritten_path, parts.query, parts.fragment)
        return urlunsplit(rewritten)

    def _candidate_route_from_link_path(self, path: str, *, route_key: str) -> str | None:
        if not path:
            return None

        current_dir = posixpath.dirname(route_key)
        if path.startswith("/"):
            joined = posixpath.normpath(path.lstrip("/"))
        else:
            base = current_dir if current_dir else "."
            joined = posixpath.normpath(posixpath.join(base, path))

        if joined.startswith("../"):
            return None

        joined_lower = joined.lower()
        for suffix in sorted(RENDERERS_BY_SUFFIX.keys(), key=len, reverse=True):
            if joined_lower.endswith(suffix):
                joined = joined[: -len(suffix)]
                break

        return normalize_route(joined)

    def _format_rewritten_path(self, original_path: str, *, route_key: str, target_path: str) -> str:
        if original_path.startswith("/"):
            return f"/{target_path}"

        current_dir = posixpath.dirname(route_key)
        start = current_dir if current_dir else "."
        rel = posixpath.relpath(target_path, start=start)
        if rel == ".":
            return target_path
        return rel
