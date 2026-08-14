"""Provide lightweight HTTP applications independent of website sources."""

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

type JsonDocument = Mapping[str, object]
type JsonDocumentFactory = Callable[[], JsonDocument | Awaitable[JsonDocument]]

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
        raise ValueError("JSON URL path must be a canonical root-relative path")
    return path


def _parsed_media_type(value: object) -> tuple[str, str, dict[str, str]]:
    if not isinstance(value, str):
        raise ValueError("media_type must be a JSON media type")
    split_value = _split_quoted(value, ";")
    if split_value is None:
        raise ValueError("media_type must be a JSON media type")
    parts = [part.strip() for part in split_value]
    if parts[0].count("/") != 1:
        raise ValueError("media_type must be a JSON media type")
    major, minor = parts[0].lower().split("/", 1)
    if major != "application" or (minor != "json" and not minor.endswith("+json")):
        raise ValueError("media_type must be application/json or an application/*+json type")
    parameters: dict[str, str] = {}
    for parameter in parts[1:]:
        if "=" not in parameter:
            raise ValueError("media_type parameters must be name=value pairs")
        name, raw_value = parameter.split("=", 1)
        name = name.strip().lower()
        decoded = _parameter_value(raw_value)
        if not name or decoded is None or name in parameters:
            raise ValueError("media_type parameters must have unique non-empty names and values")
        parameters[name] = decoded
    return major, minor, parameters


def _accepts_media_type(header: str | None, response_media_type: str) -> bool:
    if header is None or not header.strip():
        return True
    response_major, response_minor, response_parameters = _parsed_media_type(response_media_type)
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
            decoded = _parameter_value(raw_value)
            if not name or decoded is None or name in seen_names:
                valid = False
                break
            seen_names.add(name)
            if name == "q":
                if _QVALUE.fullmatch(decoded) is None:
                    valid = False
                    break
                quality = float(decoded)
            else:
                parameters.append((name, decoded))
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


def json_get_app(
    document: JsonDocument | JsonDocumentFactory,
    *,
    path: str = "/",
    media_type: str = "application/json",
    profile: str | None = None,
    cache_control: str | None = "public, max-age=60",
    cors_allow_origin: str | None = "*",
    debug: bool = False,
) -> Starlette:
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
    _parsed_media_type(media_type)
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
) -> Starlette:
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
    _major, minor, _parameters = _parsed_media_type(media_type)
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


def create_file_map_app(files: Mapping[str, str | Path], *, debug: bool = False) -> Starlette:
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
