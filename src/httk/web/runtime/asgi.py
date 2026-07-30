import json
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from httk.web.engine.site_engine import SiteEngine
from httk.web.model.errors import WebError
from httk.web.model.request import HttpRequestContext
from httk.web.widgets.table import (
    MAX_TABLE_RESPONSE_BYTES,
    TableContinuationError,
    TableContinuationExpired,
    TableProviderError,
    TableRevisionMismatch,
    _no_duplicate_object_pairs,
)

MAX_POST_BODY_BYTES = 1_000_000
MAX_TABLE_POST_BODY_BYTES = 64_000


async def _handle_request(request: Request) -> Response:
    engine: SiteEngine = request.app.state.engine
    route = request.path_params.get("path", "")
    try:
        request_context = HttpRequestContext(
            method=request.method,
            query=dict(request.query_params),
            postvars=await _extract_postvars(request),
            headers={k.lower(): v for k, v in request.headers.items()},
        )
        result = await run_in_threadpool(engine.render, route, request=request_context)
    except WebError as exc:
        if _caused_by_table_provider(exc):
            return _table_error(
                "Table provider could not load this page.",
                status_code=500,
            )
        return Response(content=str(exc), status_code=exc.status_code, media_type="text/plain")

    return Response(content=result.body, status_code=result.status_code, media_type=result.content_type)


@asynccontextmanager
async def _engine_lifespan(app: Starlette):
    """Make ASGI startup/shutdown the owner of its site engine."""

    try:
        yield
    finally:
        engine: SiteEngine = app.state.engine
        engine.close()


def create_app(*, engine: SiteEngine, debug: bool = False) -> Starlette:
    app = Starlette(
        debug=debug,
        lifespan=_engine_lifespan,
        routes=[
            Route("/_httk/table/page", _handle_table_page, methods=["GET", "POST"]),
            Route("/_httk/assets/{path:path}", _handle_widget_asset, methods=["GET"]),
            Route("/_httk/{path:path}", _handle_reserved_httk_route, methods=["GET", "POST"]),
            Route("/{path:path}", _handle_request, methods=["GET", "POST"]),
        ],
    )
    app.state.engine = engine
    return app


async def _handle_reserved_httk_route(request: Request) -> Response:
    if request.url.path.startswith("/_httk/assets/"):
        return Response("Method Not Allowed", status_code=405, media_type="text/plain", headers={"Allow": "GET"})
    return Response("Not Found", status_code=404, media_type="text/plain")


async def _handle_table_page(request: Request) -> Response:
    if request.method != "POST":
        return _table_error("Method not allowed.", status_code=405, headers={"Allow": "POST"})
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        return _table_error("Table pagination requires application/json.", status_code=415)
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_TABLE_POST_BODY_BYTES:
                return _table_error("Table pagination request is too large.", status_code=413)
        except ValueError:
            return _table_error("Invalid table pagination request.", status_code=400)
    raw = await _read_limited_body(request, limit=MAX_TABLE_POST_BODY_BYTES)
    if raw is None:
        return _table_error("Table pagination request is too large.", status_code=413)
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_no_duplicate_object_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return _table_error("Invalid table pagination request.", status_code=400)
    try:
        engine: SiteEngine = request.app.state.engine
        result = await run_in_threadpool(engine.table_runtime.continuation, payload)
        response_bytes = json.dumps(result, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        if len(response_bytes) > MAX_TABLE_RESPONSE_BYTES:
            raise TableProviderError("provider response is too large")
    except TableContinuationExpired:
        return _table_error("Table pagination link has expired. Reload the page and try again.", status_code=410)
    except TableRevisionMismatch:
        return _table_error("Table data changed. Reload the page and try again.", status_code=409)
    except TableContinuationError:
        return _table_error("Invalid table pagination request.", status_code=400)
    except TableProviderError:
        return _table_error("Table provider could not load this page.", status_code=500)
    except Exception:
        return _table_error("Table provider could not load this page.", status_code=500)
    return JSONResponse(result, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


async def _handle_widget_asset(request: Request) -> Response:
    path = request.path_params.get("path", "")
    engine: SiteEngine = request.app.state.engine
    asset = engine.widget_assets.get(path)
    if asset is None:
        return Response("Not Found", status_code=404, media_type="text/plain")
    return Response(
        asset.content,
        media_type=asset.content_type,
        headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


def _table_error(message: str, *, status_code: int, headers: dict[str, str] | None = None) -> Response:
    response_headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    response_headers.update(headers or {})
    return Response(message, status_code=status_code, media_type="text/plain", headers=response_headers)


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant: {value}")


async def _read_limited_body(request: Request, *, limit: int) -> bytes | None:
    """Read at most *limit* request bytes, including chunked bodies."""

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _caused_by_table_provider(error: BaseException) -> bool:
    """Keep source-aware author diagnostics out of live provider responses."""

    current: BaseException | None = error
    while current is not None:
        if isinstance(current, TableProviderError):
            return True
        current = current.__cause__
    return False


async def _extract_postvars(request: Request) -> dict[str, str]:
    if request.method.upper() != "POST":
        return {}

    content_type = request.headers.get("content-type", "").lower()
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_POST_BODY_BYTES:
                raise WebError(
                    f"POST body too large (>{MAX_POST_BODY_BYTES} bytes).",
                    status_code=413,
                )
        except ValueError:
            # Ignore invalid content-length values and attempt best-effort parsing.
            pass

    if "application/x-www-form-urlencoded" in content_type:
        raw = await request.body()
        if len(raw) > MAX_POST_BODY_BYTES:
            raise WebError(
                f"POST body too large (>{MAX_POST_BODY_BYTES} bytes).",
                status_code=413,
            )
        body = raw.decode("utf-8", errors="replace")
        return {k: v for k, v in parse_qsl(body, keep_blank_values=True)}

    if "application/json" in content_type:
        raw = await request.body()
        if len(raw) > MAX_POST_BODY_BYTES:
            raise WebError(
                f"POST body too large (>{MAX_POST_BODY_BYTES} bytes).",
                status_code=413,
            )
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return {}
        if isinstance(payload, dict):
            out_json: dict[str, str] = {}
            for key, value in payload.items():
                if isinstance(value, (str, int, float, bool)) or value is None:
                    out_json[str(key)] = "" if value is None else str(value)
            return out_json
        return {}

    if "multipart/form-data" in content_type:
        try:
            form = await request.form()
        except Exception:
            return {}
        out_form: dict[str, str] = {}
        for key, value in form.multi_items():
            normalized_key = str(key)
            if isinstance(value, UploadFile):
                # Keep uploads explicit and compact in postvars.
                out_form[normalized_key] = value.filename or ""
            else:
                out_form[normalized_key] = str(value)
        return out_form

    return {}
