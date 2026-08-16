"""Mount-aware Starlette runtime for the generic OPTIMADE engine."""

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from httk.core.report import collect_reports
from starlette.applications import Starlette
from starlette.datastructures import MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..endpoints.error import format_optimade_error
from ..endpoints.meta import merge_collected_warnings
from ..engine.processing import SnapshotCutoff, process
from ..engine.validate import determine_optimade_version
from ..model.config import OptimadeConfig
from ..model.request import EndpointResponse, RawRequest
from ..model.results import QueryFunction
from ..model.versions import optimade_default_version
from ..schema.served import ServedSchema

_MAX_CORS_ORIGINS = 32
_MAX_CORS_ORIGIN_LENGTH = 255


def _normalized_root_path(root_path: object) -> str:
    """Normalize the ASGI mount path without consulting the request path."""
    if root_path in (None, "", "/"):
        return ""
    if not isinstance(root_path, str):
        raise ValueError("ASGI root_path must be a string")
    if (
        not root_path.startswith("/")
        or "\\" in root_path
        or "?" in root_path
        or "#" in root_path
        or any(ord(character) < 32 or ord(character) == 127 for character in root_path)
    ):
        raise ValueError("ASGI root_path must be an absolute path without query or fragment")
    segments = root_path.split("/")
    if any(segment in (".", "..") for segment in segments):
        raise ValueError("ASGI root_path must not contain dot segments")
    return root_path.strip("/")


def _implicit_baseurl(request: Request) -> str:
    root_path = _normalized_root_path(request.scope.get("root_path", ""))
    origin = f"{request.url.scheme}://{request.url.netloc}"
    return origin + ("/" + root_path if root_path else "") + "/"


def _canonical_cors_origin(origin: object) -> str:
    if not isinstance(origin, str) or not origin or len(origin) > _MAX_CORS_ORIGIN_LENGTH:
        raise ValueError("CORS origins must be non-empty HTTP(S) origins within the configured length limit")
    if (
        origin != origin.strip()
        or "\\" in origin
        or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in origin)
        or "?" in origin
        or "#" in origin
    ):
        raise ValueError("CORS origins must not contain whitespace, control characters, paths, queries, or fragments")

    parsed = urlsplit(origin)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or "*" in parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("CORS origins must be exact HTTP(S) origins without credentials or a path")
    try:
        port = parsed.port
    except ValueError as ex:
        raise ValueError("CORS origin has an invalid port") from ex
    if parsed.netloc.endswith(":"):
        raise ValueError("CORS origin has an invalid port")

    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    if any(ord(char) > 127 for char in host):
        raise ValueError("CORS origin host must be ASCII; use browser-compatible punycode")
    rendered_host = f"[{host}]" if ":" in host else host
    if port is None or (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        return f"{scheme}://{rendered_host}"
    return f"{scheme}://{rendered_host}:{port}"


def _validated_cors_origins(origins: object) -> tuple[str, ...]:
    if not isinstance(origins, tuple):
        raise ValueError("cors_origins must be a tuple of exact origins")
    if len(origins) > _MAX_CORS_ORIGINS:
        raise ValueError("cors_origins exceeds the configured origin count limit")
    return tuple(dict.fromkeys(_canonical_cors_origin(origin) for origin in origins))


class _VaryOriginMiddleware:
    """Add the cache key required for every configured Origin-bearing request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not any(name.lower() == b"origin" for name, _value in scope["headers"]):
            await self.app(scope, receive, send)
            return

        async def send_with_vary(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                vary = {value.strip().lower() for value in headers.get("vary", "").split(",")}
                if "origin" not in vary and "*" not in vary:
                    headers.add_vary_header("Origin")
            await send(message)

        await self.app(scope, receive, send_with_vary)


def _json_format(response: Any) -> str:
    return json.dumps(response, indent=4, separators=(',', ': '), sort_keys=True)


def _render(output: EndpointResponse) -> Response:
    if output.content_type in ('application/vnd.api+json', 'application/json'):
        body = _json_format(output.json_response)
    else:
        body = output.content if output.content is not None else ""
    return Response(
        content=body,
        status_code=output.response_code,
        media_type=output.content_type,
        headers={"X-Content-Type-Options": "nosniff"},
    )


def _error_output(ex: Exception, request: RawRequest, config: OptimadeConfig) -> EndpointResponse:
    try:
        version = determine_optimade_version(request)
    except Exception:
        return format_optimade_error(ex, request, config, version=optimade_default_version)
    try:
        return format_optimade_error(ex, request, config, version=version)
    except Exception:
        return format_optimade_error(ex, request, config, version=optimade_default_version)


async def _handle_request(request: Request) -> Response:
    state = request.app.state
    path = request.path_params.get("path", "")
    querystr = request.url.query

    if state.baseurl is not None:
        baseurl = state.baseurl
    else:
        baseurl = _implicit_baseurl(request)

    raw_request = RawRequest(
        baseurl=baseurl,
        representation="/" + path + ("?" + querystr if querystr else ""),
        relurl="/" + path,
        querystr=querystr,
        query=dict(request.query_params),
    )

    with collect_reports(level=state.report_level, context_levels=state.report_context_levels):
        try:
            version = determine_optimade_version(raw_request)
            output = process(
                raw_request,
                state.query_function,
                version,
                state.config,
                state.schema,
                snapshot_cutoff_ns=state.snapshot_cutoff_ns,
                debug=state.debug,
            )
        except Exception as ex:
            output = _error_output(ex, raw_request, state.config)
        if output.content_type in ('application/vnd.api+json', 'application/json') and isinstance(
            output.json_response, dict
        ):
            merge_collected_warnings(output.json_response)

    return _render(output)


def create_app(
    *,
    query_function: QueryFunction,
    config: OptimadeConfig,
    schema: ServedSchema,
    baseurl: str | None = None,
    debug: bool = False,
    report_level: str | int = "warning",
    report_context_levels: Mapping[str, str | int] | None = None,
    snapshot_cutoff_ns: SnapshotCutoff | None = None,
) -> Starlette:
    """Create a mount-aware ASGI application for an explicit schema and query callback.

    An explicit ``baseurl`` is authoritative. Without one, the application
    derives links from the request origin and ASGI ``root_path``, preserving the
    mount path and exactly one version segment. CORS remains disabled unless
    ``config.cors_origins`` contains a bounded exact allow-list; request report
    collections are merged into response warnings, including error responses.

    :param query_function: Callback that executes entry queries.
    :param config: Service and runtime configuration.
    :param schema: Required schema describing served entries and properties.
    :param baseurl: Authoritative public API base URL, or ``None`` for derivation.
    :param debug: Enable Starlette and backend diagnostics.
    :param report_level: Minimum report level collected per request.
    :param report_context_levels: Context-specific report levels.
    :param snapshot_cutoff_ns: Optional stored-backend snapshot capability.
    :return: Configured Starlette ASGI application.
    :raises ValueError: If configured CORS origins are invalid or excessive.
    """
    if baseurl is not None and not baseurl.endswith("/"):
        baseurl += "/"

    cors_origins = _validated_cors_origins(config.cors_origins)

    app = Starlette(debug=debug, routes=[Route("/{path:path}", _handle_request, methods=["GET"])])
    # The embedding application owns logging configuration; core preserves
    # lastResort stderr output and scopes warning capture to each request.
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_origins),
            allow_methods=["GET", "HEAD", "OPTIONS"],
            allow_headers=["Accept"],
            allow_credentials=False,
        )
        app.add_middleware(_VaryOriginMiddleware)
    app.state.query_function = query_function
    app.state.snapshot_cutoff_ns = snapshot_cutoff_ns
    app.state.config = config
    app.state.schema = schema
    app.state.baseurl = baseurl
    app.state.debug = debug
    app.state.report_level = report_level
    app.state.report_context_levels = {"optimade": "info"} if report_context_levels is None else report_context_levels
    return app
