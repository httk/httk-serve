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
) -> Starlette:
    config = SiteConfig.from_srcdir(
        srcdir=srcdir,
        baseurl=baseurl,
        compatibility_mode=compatibility_mode,
        config_name=config_name,
    )
    engine = SiteEngine(config)
    return create_app(engine=engine, debug=debug)


def serve(
    srcdir: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    baseurl: str | None = None,
    compatibility_mode: bool = False,
    config_name: str = "config",
    debug: bool = False,
) -> None:
    app = create_asgi_app(
        srcdir=srcdir,
        baseurl=baseurl,
        compatibility_mode=compatibility_mode,
        config_name=config_name,
        debug=debug,
    )
    run_dev_server(app=app, host=host, port=port)


def publish(
    srcdir: str | Path,
    outdir: str | Path,
    baseurl: str,
    *,
    compatibility_mode: bool = False,
    config_name: str = "config",
) -> PublishReport:
    config = SiteConfig.from_srcdir(
        srcdir=srcdir,
        baseurl=baseurl,
        compatibility_mode=compatibility_mode,
        config_name=config_name,
    )
    engine = SiteEngine(config)
    return publish_site(engine=engine, outdir=outdir)
