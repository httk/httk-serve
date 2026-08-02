from starlette.applications import Starlette

from .model.config import OptimadeConfig
from .model.results import OptimadeAdapter
from .runtime.asgi import create_app
from .runtime.devserver import run_dev_server


def create_asgi_app(
    adapter: OptimadeAdapter,
    config: OptimadeConfig | None = None,
    *,
    baseurl: str | None = None,
    debug: bool = False,
) -> Starlette:
    """Create an ASGI application serving an OPTIMADE API for the given backend."""
    if config is None:
        config = OptimadeConfig()
    return create_app(
        query_function=adapter.query_function(), config=config, schema=adapter.schema, baseurl=baseurl, debug=debug
    )


def serve(
    adapter: OptimadeAdapter,
    config: OptimadeConfig | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    baseurl: str | None = None,
    debug: bool = False,
) -> None:
    """Serve an OPTIMADE API for the given backend with a development web server."""
    if baseurl is None:
        baseurl = f"http://{host}:{port}/" if port != 80 else f"http://{host}/"
    app = create_asgi_app(adapter, config, baseurl=baseurl, debug=debug)
    run_dev_server(app=app, host=host, port=port)
