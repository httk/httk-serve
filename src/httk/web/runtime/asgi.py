import json
from urllib.parse import parse_qsl

from starlette.applications import Starlette
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from httk.web.engine.site_engine import SiteEngine
from httk.web.model.errors import WebError
from httk.web.model.request import HttpRequestContext

MAX_POST_BODY_BYTES = 1_000_000


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
        result = engine.render(route, request=request_context)
    except WebError as exc:
        return Response(content=str(exc), status_code=exc.status_code, media_type="text/plain")

    return Response(content=result.body, status_code=result.status_code, media_type=result.content_type)


def create_app(*, engine: SiteEngine, debug: bool = False) -> Starlette:
    app = Starlette(debug=debug, routes=[Route("/{path:path}", _handle_request, methods=["GET", "POST"])])
    app.state.engine = engine
    return app


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
