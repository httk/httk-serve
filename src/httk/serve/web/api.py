"""Expose high-level helpers for serving and publishing web sites."""

from collections.abc import Mapping
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Route


def create_file_map_app(files: Mapping[str, str | Path], *, debug: bool = False) -> Starlette:
    """Create an application exposing only explicitly mapped files.

    A fresh :class:`starlette.responses.FileResponse` is constructed for every
    request.  File metadata and contents are therefore read at request time,
    and replacing or removing a mapped file takes effect without rebuilding
    the application.

    :param files: Root-relative URL paths mapped to filesystem paths.
    :param debug: Whether Starlette debug responses are enabled.
    :return: A mountable application serving the declared files.
    """
    if not isinstance(files, Mapping):
        raise TypeError("files must be a mapping of URL paths to filesystem paths")
    routes: list[Route] = []
    seen: set[str] = set()
    for url_path, file_path in files.items():
        if (
            not isinstance(url_path, str)
            or not url_path.startswith("/")
            or url_path == "/"
            or url_path.endswith("/")
            or "?" in url_path
            or "#" in url_path
            or "//" in url_path
            or any(part in {".", ".."} for part in url_path.split("/"))
        ):
            raise ValueError("file-map URL paths must be canonical root-relative file paths")
        if url_path in seen:
            raise ValueError(f"duplicate file-map URL path: {url_path!r}")
        if not isinstance(file_path, str | Path):
            raise TypeError("file-map values must be str or Path filesystem paths")
        target = Path(file_path)

        async def mapped_file(_request: Request, *, path: Path = target) -> Response:
            if not path.is_file():
                return Response("Not Found", status_code=404, media_type="text/plain")
            return FileResponse(path)

        mapped_file.__name__ = f"mapped_file_{len(routes)}"
        routes.append(Route(url_path, mapped_file, methods=["GET"]))
        seen.add(url_path)
    return Starlette(debug=debug, routes=routes)


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
    """Create an ASGI application for a site source directory.

    :param srcdir: Site source directory.
    :param baseurl: Optional site base URL used when building links.
    :param compatibility_mode: Whether to use legacy site conventions.
    :param config_name: Configuration module name.
    :param debug: Whether to enable Starlette debug responses.
    :param table_token_secret: Secret used to authenticate table continuation tokens.
    :return: Configured Starlette application.
    """
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
    """Run a development server for a site source directory.

    :param srcdir: Site source directory.
    :param host: Interface on which to listen.
    :param port: TCP port on which to listen.
    :param baseurl: Optional site base URL used when building links.
    :param compatibility_mode: Whether to use legacy site conventions.
    :param config_name: Configuration module name.
    :param debug: Whether to enable Starlette debug responses.
    :param table_token_secret: Secret used to authenticate table continuation tokens.
    """
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
    """Render a site source directory into static output files.

    :param srcdir: Site source directory.
    :param outdir: Destination directory for published files.
    :param baseurl: Site base URL used when building links.
    :param host_static: Optional host URL for static assets.
    :param compatibility_mode: Whether to use legacy site conventions.
    :param config_name: Configuration module name.
    :param use_urls_without_ext: Whether published page links omit extensions.
    :return: Report of files written and rendering warnings.
    """
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
