import json
from urllib.parse import parse_qsl

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from httk.web.engine.site_engine import SiteEngine
from httk.web.model.errors import WebError
from httk.web.model.request import HttpRequestContext


async def _handle_request(request: Request) -> Response:
    engine: SiteEngine = request.app.state.engine
    route = request.path_params.get("path", "")
    request_context = HttpRequestContext(
        method=request.method,
        query=dict(request.query_params),
        postvars=await _extract_postvars(request),
        headers={k.lower(): v for k, v in request.headers.items()},
    )
    try:
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

    if "application/x-www-form-urlencoded" in content_type:
        body = (await request.body()).decode("utf-8", errors="replace")
        return {k: v for k, v in parse_qsl(body, keep_blank_values=True)}

    if "application/json" in content_type:
        try:
            payload = json.loads((await request.body()).decode("utf-8", errors="replace"))
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
        for key, value in form.items():
            out_form[str(key)] = str(value)
        return out_form

    return {}
