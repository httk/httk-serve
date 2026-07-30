"""The HTML/configuration boundary for the browser-driven OPTIMADE table."""

import json
import re
from collections.abc import Mapping, Sequence
from html import escape
from importlib.resources import files
from urllib.parse import urlsplit

from httk.core import ParserError, parse_optimade_filter

from httk.serve.web.providers import _validate_site_route

from .core import WidgetAsset, WidgetContext, WidgetRenderResult

MAX_OPTIMADE_URL_CHARS = 2_048
MAX_OPTIMADE_IDENTIFIER_CHARS = 128
MAX_OPTIMADE_TEXT_CHARS = 4_096
MAX_OPTIMADE_LABEL_CHARS = 256
MAX_OPTIMADE_ORIGINS = 16
MAX_OPTIMADE_COLUMNS = 32
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_ALIGNMENTS = frozenset({"start", "center", "end"})
_OPTIMADE_TABLE_ASSETS: tuple[WidgetAsset, ...] | None = None


class OptimadeTableProtocolError(ValueError):
    """A declared OPTIMADE table shell cannot meet the browser protocol."""


def render(
    context: WidgetContext,
    *,
    base_url: str,
    entry_type: str = "structures",
    columns: object,
    page_size: int = 50,
    caption: str = "OPTIMADE results",
    filter: str | None = None,
    filter_query: str | None = None,
    sort: str | None = None,
    allowed_origins: tuple[str, ...] = (),
    detail_route: str | None = None,
    detail_column: str | None = None,
    detail_query: str = "id",
) -> WidgetRenderResult:
    """Render an inert, accessible OPTIMADE table shell and its trusted assets."""

    normalized_base_url = _base_url(base_url)
    normalized_entry_type = _identifier(entry_type, field="entry_type")
    normalized_columns = _columns(columns)
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 500:
        raise OptimadeTableProtocolError("page_size must be an integer between 1 and 500")
    normalized_caption = _text(caption, field="caption", maximum=MAX_OPTIMADE_LABEL_CHARS)
    normalized_filter = _filter(filter)
    normalized_filter_query = _optional_identifier(filter_query, field="filter_query")
    normalized_sort = _optional_text(sort, field="sort")
    normalized_origins = _origins(allowed_origins)
    normalized_detail_query = _identifier(detail_query, field="detail_query")
    normalized_detail_route, normalized_detail_column = _detail(
        detail_route, detail_column, column_keys={column["key"] for column in normalized_columns}
    )
    if normalized_detail_route is not None:
        normalized_detail_route = context.url_for(normalized_detail_route)

    configuration = {
        "allowed_origins": normalized_origins,
        "base_url": normalized_base_url,
        "caption": normalized_caption,
        "columns": normalized_columns,
        "detail_column": normalized_detail_column,
        "detail_query": normalized_detail_query,
        "detail_route": normalized_detail_route,
        "entry_type": normalized_entry_type,
        "filter": normalized_filter,
        "filter_query": normalized_filter_query,
        "page_size": page_size,
        "sort": normalized_sort,
        "widget_id": context.widget_id,
    }
    config_json = _safe_json(configuration)
    internal_root = _internal_root(context)
    widget_id = escape(context.widget_id, quote=True)
    config_id = escape(f"httk-serve-optimade-table-{context.widget_id}-config", quote=True)
    headers = "".join(
        f'<th scope="col" class="httk-serve-optimade-table__header httk-serve-optimade-table__header--{column["align"]}">'
        f'{escape(column["label"], quote=False)}</th>'
        for column in normalized_columns
    )
    html = (
        f'<link rel="stylesheet" href="{internal_root}/assets/serve-optimade-table.css">'
        f'<script type="module" src="{internal_root}/assets/serve-optimade-table-protocol.mjs"></script>'
        f'<script type="module" src="{internal_root}/assets/serve-optimade-table.mjs"></script>'
        f'<section class="httk-serve-optimade-table" data-httk-serve-optimade-table="1" data-widget-id="{widget_id}" '
        f'data-config-id="{config_id}" aria-busy="true">'
        f'<table><caption>{escape(normalized_caption, quote=False)}</caption><thead><tr>{headers}</tr></thead><tbody></tbody></table>'
        '<nav class="httk-serve-optimade-table__pager" aria-label="OPTIMADE table pagination">'
        '<button type="button" data-httk-serve-optimade-previous disabled aria-disabled="true">Previous</button>'
        '<span data-httk-serve-optimade-status role="status" aria-live="polite">Loading OPTIMADE results.</span>'
        '<button type="button" data-httk-serve-optimade-next disabled aria-disabled="true">Next</button>'
        "</nav>"
        f'<script id="{config_id}" type="application/json">{config_json}</script>'
        "</section>"
    )
    return WidgetRenderResult(html, assets=_optimade_table_assets())


def _base_url(value: object) -> str:
    text = _text(value, field="base_url", maximum=MAX_OPTIMADE_URL_CHARS)
    if "\\" in text or any(char.isspace() for char in text):
        raise OptimadeTableProtocolError("base_url must not contain whitespace or backslashes")
    parsed = urlsplit(text)
    if parsed.query or parsed.fragment:
        raise OptimadeTableProtocolError("base_url must not contain a query string or fragment")
    if parsed.username is not None or parsed.password is not None:
        raise OptimadeTableProtocolError("base_url must not include credentials")
    if parsed.scheme:
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise OptimadeTableProtocolError("base_url must use HTTP(S) with a host")
        try:
            host = parsed.hostname
            _port = parsed.port
        except ValueError:
            raise OptimadeTableProtocolError("base_url must contain a valid HTTP(S) host and port") from None
        if not host:
            raise OptimadeTableProtocolError("base_url must contain a valid HTTP(S) host and port")
    elif parsed.netloc or text.startswith("//"):
        raise OptimadeTableProtocolError("base_url must not be protocol-relative")
    elif not parsed.path:
        raise OptimadeTableProtocolError("base_url must be an origin-relative or path-relative URL")
    return text


def _identifier(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_OPTIMADE_IDENTIFIER_CHARS
        or not _IDENTIFIER.fullmatch(value)
    ):
        raise OptimadeTableProtocolError(f"{field} must be a bounded ASCII OPTIMADE-style identifier")
    return value


def _optional_identifier(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    return _identifier(value, field=field)


def _text(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise OptimadeTableProtocolError(f"{field} must be a non-empty string of at most {maximum} characters")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise OptimadeTableProtocolError(f"{field} must not contain control characters")
    return value


def _optional_text(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, field=field, maximum=MAX_OPTIMADE_TEXT_CHARS)


def _filter(value: object) -> str | None:
    text = _optional_text(value, field="filter")
    if text is None:
        return None
    try:
        parse_optimade_filter(text)
    except ParserError:
        raise OptimadeTableProtocolError("filter must be a valid OPTIMADE filter") from None
    return text


def _columns(value: object) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise OptimadeTableProtocolError("columns must be a sequence of strings or mappings")
    if not 1 <= len(value) <= MAX_OPTIMADE_COLUMNS:
        raise OptimadeTableProtocolError(f"columns must contain between 1 and {MAX_OPTIMADE_COLUMNS} entries")
    result: list[dict[str, str]] = []
    keys: set[str] = set()
    for column in value:
        if isinstance(column, str):
            key = _identifier(column, field="column key")
            label = key
            align = "start"
        elif isinstance(column, Mapping):
            if set(column) - {"key", "label", "align"} or "key" not in column:
                raise OptimadeTableProtocolError("column mappings may contain only key, label, and align")
            key = _identifier(column["key"], field="column key")
            label = _text(column.get("label", key), field="column label", maximum=MAX_OPTIMADE_LABEL_CHARS)
            align = column.get("align", "start")
            if not isinstance(align, str) or align not in _ALIGNMENTS:
                raise OptimadeTableProtocolError("column align must be start, center, or end")
        else:
            raise OptimadeTableProtocolError("columns must contain only strings or mappings")
        if key in keys:
            raise OptimadeTableProtocolError("column keys must be unique")
        keys.add(key)
        result.append({"key": key, "label": label, "align": align})
    return result


def _origins(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise OptimadeTableProtocolError("allowed_origins must be a sequence of exact HTTP(S) origins")
    if len(value) > MAX_OPTIMADE_ORIGINS:
        raise OptimadeTableProtocolError(f"allowed_origins may contain at most {MAX_OPTIMADE_ORIGINS} entries")
    origins: list[str] = []
    for origin in value:
        text = _text(origin, field="allowed origin", maximum=MAX_OPTIMADE_URL_CHARS)
        normalized = _origin(text)
        if normalized in origins:
            raise OptimadeTableProtocolError("allowed_origins entries must be unique")
        origins.append(normalized)
    return origins


def _origin(value: str) -> str:
    if "\\" in value or any(char.isspace() for char in value):
        raise OptimadeTableProtocolError("allowed_origins entries must be exact HTTP(S) origins")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise OptimadeTableProtocolError("allowed_origins entries must be exact HTTP(S) origins")
    try:
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise OptimadeTableProtocolError("allowed_origins entries must be exact HTTP(S) origins") from None
    if host is None:
        raise OptimadeTableProtocolError("allowed_origins entries must be exact HTTP(S) origins")
    if any(ord(char) > 127 for char in host):
        raise OptimadeTableProtocolError("allowed_origins host must be ASCII; use browser-compatible punycode")
    host = host.lower()
    if ":" in host:
        host = f"[{host}]"
    scheme = parsed.scheme.lower()
    if port is None or (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _detail(value_route: object, value_column: object, *, column_keys: set[str]) -> tuple[str | None, str | None]:
    if value_route is None and value_column is None:
        return None, None
    if value_route is None or value_column is None:
        raise OptimadeTableProtocolError("detail_route and detail_column must be supplied together")
    if not isinstance(value_route, str):
        raise OptimadeTableProtocolError("detail_route must be a safe relative site route")
    try:
        route = _validate_site_route(value_route)
    except ValueError as exc:
        raise OptimadeTableProtocolError(str(exc)) from exc
    column = _identifier(value_column, field="detail_column")
    if column not in column_keys:
        raise OptimadeTableProtocolError("detail_column must select one declared column")
    return route, column


def _internal_root(context: WidgetContext) -> str:
    relative_base = context.page.get("relbaseurl", ".")
    if not isinstance(relative_base, str) or not relative_base or relative_base.startswith("/"):
        raise OptimadeTableProtocolError("page context has no safe relative base URL")
    return f"{relative_base.rstrip('/')}/_httk/serve"


def _safe_json(value: object) -> str:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _optimade_table_assets() -> tuple[WidgetAsset, ...]:
    global _OPTIMADE_TABLE_ASSETS
    if _OPTIMADE_TABLE_ASSETS is None:
        assets = files("httk.serve.web").joinpath("assets")
        _OPTIMADE_TABLE_ASSETS = (
            WidgetAsset(
                "serve-optimade-table.css", assets.joinpath("serve-optimade-table.css").read_bytes(), "text/css"
            ),
            WidgetAsset(
                "serve-optimade-table.mjs", assets.joinpath("serve-optimade-table.mjs").read_bytes(), "text/javascript"
            ),
            WidgetAsset(
                "serve-optimade-table-protocol.mjs",
                assets.joinpath("serve-optimade-table-protocol.mjs").read_bytes(),
                "text/javascript",
            ),
        )
    return _OPTIMADE_TABLE_ASSETS
