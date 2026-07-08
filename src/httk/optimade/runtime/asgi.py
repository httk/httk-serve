import json
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ..endpoints.error import format_optimade_error
from ..engine.process import process, process_init
from ..engine.validate import determine_optimade_version
from ..model.config import OptimadeConfig
from ..model.request import EndpointResponse, RawRequest
from ..model.results import QueryFunction
from ..model.versions import optimade_default_version
from ..schema.served import ServedSchema, default_served_schema


def _json_format(response: Any) -> str:
    return json.dumps(response, indent=4, separators=(',', ': '), sort_keys=True)


def _render(output: EndpointResponse) -> Response:
    if output.content_type in ('application/vnd.api+json', 'application/json'):
        body = _json_format(output.json_response)
    else:
        body = output.content if output.content is not None else ""
    return Response(content=body, status_code=output.response_code, media_type=output.content_type)


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
        baseurl = f"{request.url.scheme}://{request.url.netloc}/"

    raw_request = RawRequest(
        baseurl=baseurl,
        representation="/" + path + ("?" + querystr if querystr else ""),
        relurl="/" + path,
        querystr=querystr,
        query=dict(request.query_params),
    )

    try:
        version = determine_optimade_version(raw_request)
        output = process(raw_request, state.query_function, version, state.config, state.schema, debug=state.debug)
    except Exception as ex:
        output = _error_output(ex, raw_request, state.config)

    return _render(output)


def create_app(
    *,
    query_function: QueryFunction,
    config: OptimadeConfig,
    schema: ServedSchema | None = None,
    baseurl: str | None = None,
    debug: bool = False,
) -> Starlette:
    if schema is None:
        schema = default_served_schema()
    if baseurl is not None and not baseurl.endswith("/"):
        baseurl += "/"

    process_init(config, query_function, schema, debug=debug)

    app = Starlette(debug=debug, routes=[Route("/{path:path}", _handle_request, methods=["GET"])])
    app.state.query_function = query_function
    app.state.config = config
    app.state.schema = schema
    app.state.baseurl = baseurl
    app.state.debug = debug
    return app
