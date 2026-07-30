from pathlib import Path

from starlette.applications import Starlette

from .engine.site_engine import SiteEngine
from .model.config import SiteConfig
from .model.page import PublishReport
from .publishing.static import publish_site
from .runtime.asgi import create_app
from .runtime.devserver import run_dev_server


def create_asgi_app(
    srcdir: str | Path,
    *,
    baseurl: str | None = None,
    compatibility_mode: bool = False,
    config_name: str = "config",
    debug: bool = False,
    table_token_secret: str | bytes | None = None,
) -> Starlette:
    config = SiteConfig.from_srcdir(
        srcdir=srcdir,
        baseurl=baseurl,
        compatibility_mode=compatibility_mode,
        config_name=config_name,
    )
    engine = SiteEngine(config, table_token_secret=table_token_secret)
    try:
        return create_app(engine=engine, debug=debug)
    except BaseException as exc:
        _close_after_operation_error(engine, exc)
        raise


def serve(
    srcdir: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    baseurl: str | None = None,
    compatibility_mode: bool = False,
    config_name: str = "config",
    debug: bool = False,
    table_token_secret: str | bytes | None = None,
) -> None:
    app = create_asgi_app(
        srcdir=srcdir,
        baseurl=baseurl,
        compatibility_mode=compatibility_mode,
        config_name=config_name,
        debug=debug,
        table_token_secret=table_token_secret,
    )
    try:
        run_dev_server(app=app, host=host, port=port)
    except BaseException as exc:
        _close_after_operation_error(app.state.engine, exc)
        raise
    app.state.engine.close()


def publish(
    srcdir: str | Path,
    outdir: str | Path,
    baseurl: str,
    *,
    host_static: str | None = None,
    compatibility_mode: bool = False,
    config_name: str = "config",
    use_urls_without_ext: bool | None = None,
) -> PublishReport:
    publish_use_urls_without_ext = use_urls_without_ext if use_urls_without_ext is not None else not compatibility_mode
    config = SiteConfig.from_srcdir(
        srcdir=srcdir,
        baseurl=baseurl,
        host_static=host_static,
        compatibility_mode=compatibility_mode,
        config_name=config_name,
        publish_use_urls_without_ext=publish_use_urls_without_ext,
    )
    engine = SiteEngine(config)
    try:
        report = publish_site(engine=engine, outdir=outdir)
    except BaseException as exc:
        _close_after_operation_error(engine, exc)
        raise
    engine.close()
    return report


def _close_after_operation_error(engine: SiteEngine, operation_error: BaseException) -> None:
    """Release an engine without concealing the operation that failed first."""

    try:
        engine.close()
    except BaseException as cleanup_error:
        operation_error.add_note(f"Additional site resource cleanup failure: {cleanup_error!r}")
