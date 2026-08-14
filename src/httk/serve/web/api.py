"""Expose high-level helpers for serving and publishing web sites."""

import hashlib
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Route

type JsonLdDocument = Mapping[str, object]
type JsonLdDocumentFactory = Callable[[], JsonLdDocument | Awaitable[JsonLdDocument]]

_QVALUE = re.compile(r"(?:0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?)\Z")


@dataclass(frozen=True, slots=True)
class _AcceptRange:
    """One valid media range from an HTTP ``Accept`` header."""

    major: str
    minor: str
    parameters: tuple[tuple[str, str], ...]
    quality: float


def _split_quoted(value: str, delimiter: str) -> tuple[str, ...] | None:
    """Split an HTTP field outside quoted strings, rejecting broken quoting."""
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    escaped = False
    for character in value:
        if escaped:
            current.append(character)
            escaped = False
        elif quoted and character == "\\":
            current.append(character)
            escaped = True
        elif character == '"':
            current.append(character)
            quoted = not quoted
        elif character == delimiter and not quoted:
            parts.append("".join(current))
            current = []
        else:
            current.append(character)
    if quoted or escaped:
        return None
    parts.append("".join(current))
    return tuple(parts)


def _parameter_value(value: str) -> str | None:
    """Decode one token or quoted-string parameter value."""
    value = value.strip()
    if not value:
        return None
    if not value.startswith('"'):
        return None if '"' in value else value
    if len(value) < 2 or not value.endswith('"'):
        return None
    decoded: list[str] = []
    escaped = False
    for character in value[1:-1]:
        if escaped:
            decoded.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == '"':
            return None
        else:
            decoded.append(character)
    if escaped:
        return None
    return "".join(decoded)


def _canonical_get_path(path: object) -> str:
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or "//" in path
        or "?" in path
        or "#" in path
        or "%" in path
        or "\\" in path
        or any(part in {".", ".."} for part in path.split("/"))
    ):
        raise ValueError("JSON-LD URL path must be a canonical root-relative path")
    return path


def _accepts_json_ld(header: str | None, response_media_type: str = "application/ld+json") -> bool:
    if header is None or not header.strip():
        return True
    split_response = _split_quoted(response_media_type, ";")
    if split_response is None:
        return False
    response_parts = [part.strip() for part in split_response]
    response_major, response_minor = response_parts[0].lower().split("/", 1)
    response_parameters: dict[str, str] = {}
    for parameter in response_parts[1:]:
        if "=" not in parameter:
            return False
        name, raw_value = parameter.split("=", 1)
        name = name.strip().lower()
        value = _parameter_value(raw_value)
        if not name or value is None or name in response_parameters:
            return False
        response_parameters[name] = value
    ranges: list[_AcceptRange] = []
    items = _split_quoted(header, ",")
    if items is None:
        return False
    for header_item in items:
        split_parts = _split_quoted(header_item, ";")
        if split_parts is None:
            continue
        parts = [part.strip() for part in split_parts]
        media_type = parts[0].lower()
        if media_type.count("/") != 1:
            continue
        major, minor = media_type.split("/", 1)
        if not major or not minor or (major == "*" and minor != "*") or ("*" in minor and minor != "*"):
            continue
        parameters: list[tuple[str, str]] = []
        quality = 1.0
        seen_names: set[str] = set()
        valid = True
        for parameter in parts[1:]:
            if "=" not in parameter:
                valid = False
                break
            name, raw_value = parameter.split("=", 1)
            name = name.strip().lower()
            value = _parameter_value(raw_value)
            if not name or value is None or name in seen_names:
                valid = False
                break
            seen_names.add(name)
            if name == "q":
                if _QVALUE.fullmatch(value) is None:
                    valid = False
                    break
                quality = float(value)
            else:
                parameters.append((name, value))
        if valid:
            ranges.append(_AcceptRange(major, minor, tuple(parameters), quality))

    best: tuple[float, tuple[int, int]] | None = None
    for accept_range in ranges:
        if accept_range.major not in {"*", response_major} or accept_range.minor not in {"*", response_minor}:
            continue
        if any(response_parameters.get(name) != value for name, value in accept_range.parameters):
            continue
        specificity = (
            2
            if accept_range.major == response_major and accept_range.minor == response_minor
            else 1
            if accept_range.major == response_major
            else 0,
            len(accept_range.parameters),
        )
        if best is None or specificity > best[1]:
            best = (accept_range.quality, specificity)
    return best is not None and best[0] > 0


def _etag_matches(header: str | None, etag: str) -> bool:
    if header is None:
        return False
    return any(candidate.strip().removeprefix("W/") in {"*", etag} for candidate in header.split(","))


def jsonld_http_get_app(
    document: JsonLdDocument | JsonLdDocumentFactory,
    *,
    path: str = "/",
    media_type: str = "application/ld+json",
    profile: str | None = None,
    cache_control: str | None = "public, max-age=60",
    cors_allow_origin: str | None = "*",
    debug: bool = False,
) -> Starlette:
    """Create a lightweight application serving one live JSON-LD document.

    The document may be a fixed mapping or a zero-argument synchronous or
    asynchronous factory. A factory is evaluated for every request, allowing
    a caller-owned database or provider to remain the live source of truth.
    This helper supplies generic HTTP representation behavior only; vocabulary,
    discovery, schema validation, and protocol conformance remain the caller's
    responsibility.

    :param document: JSON-LD mapping or live document factory.
    :param path: Canonical root-relative route, normally ``/`` for mounting.
    :param media_type: JSON-LD response media type, including optional parameters.
    :param profile: Optional profile IRI emitted as an RFC 6906 ``Link`` header.
    :param cache_control: Optional ``Cache-Control`` response value.
    :param cors_allow_origin: Optional ``Access-Control-Allow-Origin`` value.
    :param debug: Whether Starlette debug responses are enabled.
    :return: A mountable application serving the declared JSON-LD resource.
    """
    route_path = _canonical_get_path(path)
    if not isinstance(document, Mapping) and not callable(document):
        raise TypeError("document must be a mapping or zero-argument document factory")
    if not isinstance(media_type, str) or media_type.split(";", 1)[0].strip().lower() != "application/ld+json":
        raise ValueError("media_type must be application/ld+json, optionally with parameters")
    for name, value in (
        ("profile", profile),
        ("cache_control", cache_control),
        ("cors_allow_origin", cors_allow_origin),
    ):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{name} must be a non-empty string or None")

    async def jsonld_document(request: Request) -> Response:
        if not _accepts_json_ld(request.headers.get("accept"), media_type):
            headers = {"Vary": "Accept"}
            if cors_allow_origin is not None:
                headers["Access-Control-Allow-Origin"] = cors_allow_origin
            return Response("Not Acceptable", status_code=406, media_type="text/plain", headers=headers)
        value = document() if callable(document) else document
        if inspect.isawaitable(value):
            value = await value
        if not isinstance(value, Mapping):
            raise TypeError("the JSON-LD document factory must return a mapping")
        content = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        etag = f'"{hashlib.sha256(content).hexdigest()}"'
        headers = {"ETag": etag, "Vary": "Accept"}
        if cache_control is not None:
            headers["Cache-Control"] = cache_control
        if cors_allow_origin is not None:
            headers["Access-Control-Allow-Origin"] = cors_allow_origin
        if profile is not None:
            headers["Link"] = f'<{profile}>; rel="profile"'
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        return Response(content, media_type=media_type, headers=headers)

    app = Starlette(debug=debug, routes=[Route(route_path, jsonld_document, methods=["GET"])])
    app.state.jsonld_document = document
    return app


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
