"""The HTML/configuration boundary for the browser-driven OPTIMADE table."""

import json
import math
import re
from collections.abc import Mapping, Sequence
from html import escape
from importlib.resources import files
from typing import cast
from urllib.parse import urlsplit

from httk.core.optimade import ParserError, parse_optimade_filter

from httk.serve.web.providers import _validate_site_route

from .core import WidgetAsset, WidgetContext, WidgetRenderResult
from .optimade_assets import _internal_root as _asset_internal_root
from .optimade_assets import optimade_protocol_asset, optimade_protocol_href

MAX_OPTIMADE_URL_CHARS = 2_048
MAX_OPTIMADE_IDENTIFIER_CHARS = 128
MAX_OPTIMADE_TEXT_CHARS = 4_096
MAX_OPTIMADE_LABEL_CHARS = 256
MAX_OPTIMADE_ORIGINS = 16
MAX_OPTIMADE_COLUMNS = 32
MAX_OPTIMADE_SUMMARY_FIELDS = 64
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
    sort_query: str | None = None,
    sort_aliases: Mapping[str, str] | None = None,
    allowed_origins: tuple[str, ...] = (),
    detail_route: str | None = None,
    detail_column: str | None = None,
    detail_query: str = "id",
    summary: object = None,
    advanced_filter: object = None,
) -> WidgetRenderResult:
    """Render an inert, accessible OPTIMADE table shell and trusted assets.

    The browser negotiates continuation URLs from inert configuration; those
    URLs are not placed in DOM attributes, events, storage, or history.
    ``allowed_origins`` is a client continuation allow-list, not an access
    control boundary. ``filter_query`` and ``sort_query`` replace the whole
    browser filter and sort values.

    :param context: Immutable widget invocation context.
    :param base_url: Origin- or path-relative OPTIMADE endpoint base URL.
    :param entry_type: OPTIMADE entry resource type.
    :param columns: Column names or column mappings to display.
    :param page_size: Number of entries requested per browser page.
    :param caption: Accessible table caption.
    :param filter: Initial OPTIMADE filter expression.
    :param filter_query: Whole-filter query parameter name used by the browser.
    :param sort: Optional OPTIMADE sort expression.
    :param sort_query: Query parameter name whose complete value replaces ``sort``.
    :param sort_aliases: Optional mapping of display sort values (e.g. a human-facing
        ``"rank"``) to complete OPTIMADE sort expressions. The browser resolves an
        authored or URL-supplied sort through this mapping before querying, so a
        display alias is never sent to OPTIMADE; unmapped values pass through unchanged.
    :param allowed_origins: Client-side allow-list for continuation origins.
    :param detail_route: Optional site route for entry details.
    :param detail_column: Column supplying the detail value.
    :param detail_query: Query parameter receiving the detail value.
    :param summary: Optional results-summary configuration. ``None`` disables it,
        ``True`` enables it with defaults (noun ``"entries"``), and a mapping may set
        ``noun`` and a ``fields`` mapping of property name to ``label``, ``format``,
        and ``values`` overlays used to describe the active filter and sort in human
        terms. Field presentation defaults to the matching column's label and format.
    :param advanced_filter: Optional advanced-filter disclosure configuration. ``None``
        disables it, ``True`` enables it with defaults, and a mapping may set ``label``
        (the disclosure heading) and ``help_url`` (an absolute HTTP(S) URL or site-relative
        path to an "available fields" reference). The disclosure is a plain GET form that
        submits a raw OPTIMADE filter under the ``filter_query`` parameter, so it requires
        ``filter_query`` to be set.
    :return: Accessible table shell and its trusted assets.
    :raises OptimadeTableProtocolError: If configuration violates the browser protocol.
    """

    normalized_base_url = _base_url(base_url)
    normalized_entry_type = _identifier(entry_type, field="entry_type")
    normalized_columns = _columns(columns)
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 500:
        raise OptimadeTableProtocolError("page_size must be an integer between 1 and 500")
    normalized_caption = _text(caption, field="caption", maximum=MAX_OPTIMADE_LABEL_CHARS)
    normalized_filter = _filter(filter)
    normalized_filter_query = _optional_identifier(filter_query, field="filter_query")
    normalized_sort = _optional_text(sort, field="sort")
    normalized_sort_query = _optional_identifier(sort_query, field="sort_query")
    normalized_sort_aliases = _sort_aliases(sort_aliases)
    normalized_origins = _origins(allowed_origins)
    normalized_detail_query = _identifier(detail_query, field="detail_query")
    normalized_detail_route, normalized_detail_column = _detail(
        detail_route,
        detail_column,
        column_keys={column["key"] for column in normalized_columns if isinstance(column["key"], str)},
    )
    if normalized_detail_route is not None:
        normalized_detail_route = context.url_for(normalized_detail_route)
    normalized_summary = _summary(summary, normalized_columns)
    normalized_advanced_filter = _advanced_filter(advanced_filter, filter_query=normalized_filter_query)

    configuration = {
        "advanced_filter": normalized_advanced_filter,
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
        "sort_aliases": normalized_sort_aliases,
        "sort_query": normalized_sort_query,
        "summary": normalized_summary,
        "widget_id": context.widget_id,
    }
    config_json = _safe_json(configuration)
    internal_root = _internal_root(context)
    widget_id = escape(context.widget_id, quote=True)
    config_id = escape(f"httk-serve-optimade-table-{context.widget_id}-config", quote=True)
    headers = "".join(
        f'<th scope="col" class="httk-serve-optimade-table__header httk-serve-optimade-table__header--{column["align"]}">'
        f'{escape(cast(str, column["label"]), quote=False)}</th>'
        for column in normalized_columns
    )
    summary_shell = (
        '<div class="httk-serve-optimade-table__summary" data-httk-serve-optimade-summary hidden></div>'
        if normalized_summary is not None
        else ""
    )
    advanced_shell = (
        _advanced_filter_shell(normalized_advanced_filter, cast(str, normalized_filter_query))
        if normalized_advanced_filter is not None
        else ""
    )
    html = (
        f'<link rel="stylesheet" href="{internal_root}/assets/serve-optimade-table.css">'
        f'<script type="module" src="{optimade_protocol_href(context)}"></script>'
        f'<script type="module" src="{internal_root}/assets/serve-optimade-table.mjs"></script>'
        f'<section class="httk-serve-optimade-table" data-httk-serve-optimade-table="1" data-widget-id="{widget_id}" '
        f'data-config-id="{config_id}" aria-busy="true">'
        f"{summary_shell}"
        f"{advanced_shell}"
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


def _sort_aliases(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise OptimadeTableProtocolError("sort_aliases must be a mapping of display value to OPTIMADE sort")
    aliases: dict[str, str] = {}
    for alias, sort in value.items():
        if not isinstance(alias, str) or not alias or len(alias) > MAX_OPTIMADE_TEXT_CHARS:
            raise OptimadeTableProtocolError("sort_aliases keys must be non-empty strings")
        aliases[alias] = _text(sort, field="sort_aliases value", maximum=MAX_OPTIMADE_TEXT_CHARS)
    return aliases


def _filter(value: object) -> str | None:
    text = _optional_text(value, field="filter")
    if text is None:
        return None
    try:
        parse_optimade_filter(text)
    except ParserError:
        raise OptimadeTableProtocolError("filter must be a valid OPTIMADE filter") from None
    return text


def _columns(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise OptimadeTableProtocolError("columns must be a sequence of strings or mappings")
    if not 1 <= len(value) <= MAX_OPTIMADE_COLUMNS:
        raise OptimadeTableProtocolError(f"columns must contain between 1 and {MAX_OPTIMADE_COLUMNS} entries")
    result: list[dict[str, object]] = []
    keys: set[str] = set()
    for column in value:
        if isinstance(column, str):
            key = _identifier(column, field="column key")
            label = key
            align = "start"
        elif isinstance(column, Mapping):
            if set(column) - {"key", "label", "align", "format"} or "key" not in column:
                raise OptimadeTableProtocolError("column mappings may contain only key, label, align, and format")
            key = _identifier(column["key"], field="column key")
            label = _text(column.get("label", key), field="column label", maximum=MAX_OPTIMADE_LABEL_CHARS)
            align = column.get("align", "start")
            if not isinstance(align, str) or align not in _ALIGNMENTS:
                raise OptimadeTableProtocolError("column align must be start, center, or end")
        else:
            raise OptimadeTableProtocolError("columns must contain only strings or mappings")
        normalized_format = (
            _column_format(column.get("format")) if isinstance(column, Mapping) and "format" in column else None
        )
        if key in keys:
            raise OptimadeTableProtocolError("column keys must be unique")
        keys.add(key)
        result.append(
            {
                "key": key,
                "label": label,
                "align": align,
                **({"format": normalized_format} if normalized_format is not None else {}),
            }
        )
    return result


def _column_format(value: object) -> str | dict[str, object]:
    if value == "formula":
        return "formula"
    if not isinstance(value, Mapping) or "name" not in value:
        raise OptimadeTableProtocolError("column format must be formula, number, or join")
    name = value["name"]
    if name == "number":
        if set(value) - {"name", "digits", "scale", "suffix"} or "digits" not in value:
            raise OptimadeTableProtocolError("number format has invalid keys")
        digits = value["digits"]
        if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 10:
            raise OptimadeTableProtocolError("number format digits must be an integer between 0 and 10")
        scale = value.get("scale", 1)
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale == 0:
            raise OptimadeTableProtocolError("number format scale must be a finite non-zero number")
        suffix = value.get("suffix", "")
        _short_format_text(suffix, "number format suffix")
        return {"name": "number", "digits": digits, "scale": float(scale), "suffix": suffix}
    if name == "join":
        if set(value) - {"name", "separator"}:
            raise OptimadeTableProtocolError("join format has invalid keys")
        separator = value.get("separator", ", ")
        _short_format_text(separator, "join format separator")
        return {"name": "join", "separator": separator}
    raise OptimadeTableProtocolError("column format name must be number or join")


def _short_format_text(value: object, field: str) -> None:
    if not isinstance(value, str) or len(value) > 16 or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise OptimadeTableProtocolError(f"{field} must be a string of at most 16 characters without controls")


def _summary(value: object, columns: Sequence[Mapping[str, object]]) -> dict[str, object] | None:
    if value is None:
        return None
    if value is True:
        noun = "entries"
        overlay: dict[str, dict[str, object]] = {}
    elif isinstance(value, Mapping):
        if set(value) - {"noun", "fields"}:
            raise OptimadeTableProtocolError("summary may contain only noun and fields")
        noun = _text(value.get("noun", "entries"), field="summary noun", maximum=MAX_OPTIMADE_LABEL_CHARS)
        overlay = _summary_fields(value.get("fields"))
    else:
        raise OptimadeTableProtocolError("summary must be True, a mapping, or None")
    fields: dict[str, dict[str, object]] = {
        cast(str, column["key"]): {"label": column["label"], "format": column.get("format"), "values": None}
        for column in columns
    }
    for prop, spec in overlay.items():
        entry = fields.get(prop, {"label": prop, "format": None, "values": None})
        if "label" in spec:
            entry["label"] = spec["label"]
        if "format" in spec:
            entry["format"] = spec["format"]
        entry["values"] = spec.get("values")
        fields[prop] = entry
    return {"noun": noun, "fields": fields}


def _summary_fields(value: object) -> dict[str, dict[str, object]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise OptimadeTableProtocolError("summary fields must be a mapping of property to presentation")
    if len(value) > MAX_OPTIMADE_SUMMARY_FIELDS:
        raise OptimadeTableProtocolError(f"summary fields may contain at most {MAX_OPTIMADE_SUMMARY_FIELDS} entries")
    overlay: dict[str, dict[str, object]] = {}
    for prop, spec in value.items():
        key = _identifier(prop, field="summary field property")
        if not isinstance(spec, Mapping) or set(spec) - {"label", "format", "values"}:
            raise OptimadeTableProtocolError("summary field entries may contain only label, format, and values")
        entry: dict[str, object] = {}
        if "label" in spec:
            entry["label"] = _text(spec["label"], field="summary field label", maximum=MAX_OPTIMADE_LABEL_CHARS)
        if "format" in spec:
            entry["format"] = _column_format(spec["format"])
        if "values" in spec:
            entry["values"] = _summary_values(spec["values"])
        overlay[key] = entry
    return overlay


def _summary_values(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise OptimadeTableProtocolError("summary field values must be a mapping of value to label")
    if len(value) > MAX_OPTIMADE_SUMMARY_FIELDS:
        raise OptimadeTableProtocolError(
            f"summary field values may contain at most {MAX_OPTIMADE_SUMMARY_FIELDS} entries"
        )
    values: dict[str, str] = {}
    for raw, label in value.items():
        key = _text(raw, field="summary value key", maximum=MAX_OPTIMADE_LABEL_CHARS)
        values[key] = _text(label, field="summary value label", maximum=MAX_OPTIMADE_LABEL_CHARS)
    return values


def _advanced_filter(value: object, *, filter_query: str | None) -> dict[str, object] | None:
    if value is None:
        return None
    if value is True:
        label = "Advanced OPTIMADE filter"
        help_url: str | None = None
    elif isinstance(value, Mapping):
        if set(value) - {"label", "help_url"}:
            raise OptimadeTableProtocolError("advanced_filter may contain only label and help_url")
        label = _text(
            value.get("label", "Advanced OPTIMADE filter"),
            field="advanced_filter label",
            maximum=MAX_OPTIMADE_LABEL_CHARS,
        )
        help_url = _advanced_filter_help_url(value.get("help_url"))
    else:
        raise OptimadeTableProtocolError("advanced_filter must be True, a mapping, or None")
    if filter_query is None:
        raise OptimadeTableProtocolError("advanced_filter requires filter_query to name the form parameter")
    return {"label": label, "help_url": help_url}


def _advanced_filter_help_url(value: object) -> str | None:
    if value is None:
        return None
    text = _text(value, field="advanced_filter help_url", maximum=MAX_OPTIMADE_URL_CHARS)
    if "\\" in text or any(char.isspace() for char in text):
        raise OptimadeTableProtocolError("advanced_filter help_url must not contain whitespace or backslashes")
    parsed = urlsplit(text)
    if parsed.scheme:
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise OptimadeTableProtocolError("advanced_filter help_url must use HTTP(S) with a host")
        if parsed.username is not None or parsed.password is not None:
            raise OptimadeTableProtocolError("advanced_filter help_url must not include credentials")
    elif parsed.netloc or text.startswith("//"):
        raise OptimadeTableProtocolError("advanced_filter help_url must not be protocol-relative")
    elif not parsed.path:
        raise OptimadeTableProtocolError("advanced_filter help_url must be an HTTP(S) URL or site-relative path")
    return text


def _advanced_filter_shell(spec: Mapping[str, object], filter_query: str) -> str:
    label = escape(cast(str, spec["label"]), quote=False)
    name = escape(filter_query, quote=True)
    marker = escape(f"{filter_query}_advanced", quote=True)
    help_url = spec["help_url"]
    help_link = (
        f'<a class="httk-serve-optimade-table__advanced-help" '
        f'href="{escape(cast(str, help_url), quote=True)}" target="_blank" rel="noopener noreferrer">Available fields</a>'
        if help_url is not None
        else ""
    )
    return (
        '<details class="httk-serve-optimade-table__advanced" data-httk-serve-optimade-advanced>'
        f"<summary>{label}</summary>"
        '<form method="get" class="httk-serve-optimade-table__advanced-form">'
        f'<input type="hidden" name="{marker}" value="1">'
        '<label class="httk-serve-optimade-table__advanced-label">OPTIMADE filter '
        f'<input type="text" name="{name}" class="httk-serve-optimade-table__advanced-input" '
        'data-httk-serve-optimade-advanced-filter autocomplete="off" spellcheck="false"></label>'
        '<button type="submit">Search</button>'
        f"{help_link}"
        "</form>"
        "</details>"
    )


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
    try:
        return _asset_internal_root(context)
    except ValueError as exc:
        raise OptimadeTableProtocolError(str(exc)) from exc


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
            optimade_protocol_asset(),
        )
    return _OPTIMADE_TABLE_ASSETS
