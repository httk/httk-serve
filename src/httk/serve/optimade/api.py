import logging
from collections.abc import Mapping

from httk.core.report import configure_reporting
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
    report_level: str | int = "warning",
    report_context_levels: Mapping[str, str | int] | None = None,
) -> Starlette:
    """Create an ASGI application serving an OPTIMADE API for the given backend."""
    if config is None:
        config = OptimadeConfig()
    return create_app(
        query_function=adapter.query_function(),
        config=config,
        schema=adapter.schema,
        baseurl=baseurl,
        debug=debug,
        report_level=report_level,
        report_context_levels=report_context_levels,
    )


def serve(
    adapter: OptimadeAdapter,
    config: OptimadeConfig | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    baseurl: str | None = None,
    debug: bool = False,
    report_level: str | int = "warning",
    report_context_levels: Mapping[str, str | int] | None = None,
) -> None:
    """Serve an OPTIMADE API for the given backend with a development web server."""
    if baseurl is None:
        baseurl = f"http://{host}:{port}/" if port != 80 else f"http://{host}/"
    # Unlike create_app (embedders own logging), the development server IS the
    # host process: give diagnostics a console unless one is already configured.
    if not logging.getLogger("httk").handlers and not logging.getLogger().handlers:
        configure_reporting()
    app = create_asgi_app(
        adapter,
        config,
        baseurl=baseurl,
        debug=debug,
        report_level=report_level,
        report_context_levels=report_context_levels,
    )
    run_dev_server(app=app, host=host, port=port)
