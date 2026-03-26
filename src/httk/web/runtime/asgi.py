from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from httk.web.engine.site_engine import SiteEngine
from httk.web.model.errors import WebError


async def _handle_request(request: Request) -> Response:
    engine: SiteEngine = request.app.state.engine
    route = request.path_params.get("path", "")
    query = dict(request.query_params)
    try:
        result = engine.render(route, query=query)
    except WebError as exc:
        return Response(content=str(exc), status_code=exc.status_code, media_type="text/plain")

    return Response(content=result.body, status_code=result.status_code, media_type=result.content_type)


def create_app(*, engine: SiteEngine, debug: bool = False) -> Starlette:
    app = Starlette(debug=debug, routes=[Route("/{path:path}", _handle_request, methods=["GET"])])
    app.state.engine = engine
    return app
