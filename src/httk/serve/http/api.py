"""Provide lightweight HTTP applications independent of website sources."""

import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Route

from .accept import best_quality_matching_parameters, parse_accept, parse_media_type
from .apptypes import ServeApp

type JsonDocument = Mapping[str, object]
type JsonDocumentFactory = Callable[[], JsonDocument | Awaitable[JsonDocument]]


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
        raise ValueError("JSON URL path must be a canonical root-relative path")
    return path


def _accepts_media_type(header: str | None, response_media_type: str) -> bool:
    if header is None or not header.strip():
        return True
    response_major, response_minor, response_parameters = parse_media_type(response_media_type)
    ranges = parse_accept(header)
    quality = best_quality_matching_parameters(ranges, response_major, response_minor, response_parameters)
    return quality is not None and quality > 0


def _etag_matches(header: str | None, etag: str) -> bool:
    if header is None:
        return False
    return any(candidate.strip().removeprefix("W/") in {"*", etag} for candidate in header.split(","))


def json_get_app(
    document: JsonDocument | JsonDocumentFactory,
    *,
    path: str = "/",
    media_type: str = "application/json",
    profile: str | None = None,
    cache_control: str | None = "public, max-age=60",
    cors_allow_origin: str | None = "*",
    debug: bool = False,
) -> ServeApp:
    """Create a lightweight application serving one live JSON document.

    :param document: JSON mapping or live zero-argument document factory.
    :param path: Canonical root-relative route, normally ``/`` for mounting.
    :param media_type: JSON response media type, including optional parameters.
    :param profile: Optional profile IRI emitted as an RFC 6906 ``Link`` header.
    :param cache_control: Optional ``Cache-Control`` response value.
    :param cors_allow_origin: Optional ``Access-Control-Allow-Origin`` value.
    :param debug: Whether Starlette debug responses are enabled.
    :return: A mountable application serving the declared JSON resource.
    """
    route_path = _canonical_get_path(path)
    if not isinstance(document, Mapping) and not callable(document):
        raise TypeError("document must be a mapping or zero-argument document factory")
    parse_media_type(media_type)
    for name, value in (
        ("profile", profile),
        ("cache_control", cache_control),
        ("cors_allow_origin", cors_allow_origin),
    ):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{name} must be a non-empty string or None")

    async def json_document(request: Request) -> Response:
        if not _accepts_media_type(request.headers.get("accept"), media_type):
            headers = {"Vary": "Accept"}
            if cors_allow_origin is not None:
                headers["Access-Control-Allow-Origin"] = cors_allow_origin
            return Response("Not Acceptable", status_code=406, media_type="text/plain", headers=headers)
        value = document() if callable(document) else document
        if inspect.isawaitable(value):
            value = await value
        if not isinstance(value, Mapping):
            raise TypeError("the JSON document factory must return a mapping")
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

    app = Starlette(debug=debug, routes=[Route(route_path, json_document, methods=["GET"])])
    app.state.json_document = document
    return app


def jsonld_get_app(
    document: JsonDocument | JsonDocumentFactory,
    *,
    path: str = "/",
    media_type: str = "application/ld+json",
    profile: str | None = None,
    cache_control: str | None = "public, max-age=60",
    cors_allow_origin: str | None = "*",
    debug: bool = False,
) -> ServeApp:
    """Create a lightweight application serving one live JSON-LD document.

    :param document: JSON-LD mapping or live zero-argument document factory.
    :param path: Canonical root-relative route, normally ``/`` for mounting.
    :param media_type: JSON-LD response media type, including optional parameters.
    :param profile: Optional profile IRI emitted as an RFC 6906 ``Link`` header.
    :param cache_control: Optional ``Cache-Control`` response value.
    :param cors_allow_origin: Optional ``Access-Control-Allow-Origin`` value.
    :param debug: Whether Starlette debug responses are enabled.
    :return: A mountable application serving the declared JSON-LD resource.
    """
    _major, minor, _parameters = parse_media_type(media_type)
    if minor != "ld+json":
        raise ValueError("media_type must be application/ld+json, optionally with parameters")
    return json_get_app(
        document,
        path=path,
        media_type=media_type,
        profile=profile,
        cache_control=cache_control,
        cors_allow_origin=cors_allow_origin,
        debug=debug,
    )


def create_file_map_app(files: Mapping[str, str | Path], *, debug: bool = False) -> ServeApp:
    """Create an application exposing only explicitly mapped files.

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


__all__ = [
    "JsonDocument",
    "JsonDocumentFactory",
    "create_file_map_app",
    "json_get_app",
    "jsonld_get_app",
]
