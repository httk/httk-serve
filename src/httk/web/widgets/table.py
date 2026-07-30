"""The built-in, cursor-paginated ``httk.table`` widget."""

import base64
import binascii
import hmac
import json
import logging
import math
import os
import secrets
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast
from urllib.parse import urlencode

from httk.web.providers import ProviderContext, TableColumn, TablePage, TableRequest

from .core import WidgetContext, WidgetRenderResult

if TYPE_CHECKING:
    from httk.web.engine.site_engine import SiteEngine


TABLE_ENDPOINT = "/_httk/table/page"
TABLE_ASSET_ROOT = "/_httk/assets"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500
MAX_CURSOR_CHARS = 16_384
MAX_TOKEN_BYTES = 48_000
MAX_TOKEN_PAYLOAD_BYTES = 32_768
MAX_TABLE_HTML_BYTES = 512_000
MAX_TABLE_RESPONSE_BYTES = 600_000
TOKEN_VERSION = 1
DEFAULT_TOKEN_TTL_SECONDS = 900

_LOGGER = logging.getLogger(__name__)


class TableProtocolError(ValueError):
    """An expected bad table declaration, continuation, or provider result."""


class TableContinuationError(TableProtocolError):
    """A continuation token did not meet the endpoint protocol."""


class TableContinuationExpired(TableContinuationError):
    """A valid continuation token has passed its expiry."""


class TableProviderError(TableProtocolError):
    """A site-local provider could not safely supply the requested page."""


class TableRevisionMismatch(TableProviderError):
    """A provider changed a revision-pinned result while paging."""


class _TableState(TypedDict):
    args: dict[str, object]
    columns: tuple[TableColumn, ...]
    cursor: str
    direction: str
    expires: int
    page: dict[str, object]
    page_size: int
    provider: str
    query: dict[str, object]
    revision: str | None
    route: str
    row_template: str | None
    widget_id: str


class TableTokenSigner:
    """Authenticate the canonical, bounded continuation envelope."""

    def __init__(self, secret: str | bytes | None = None) -> None:
        if secret is None:
            secret = os.environ.get("HTTK_WEB_TABLE_TOKEN_SECRET")
        if secret is None:
            self._key = secrets.token_bytes(32)
        elif isinstance(secret, str):
            if len(secret.encode("utf-8")) < 32:
                raise ValueError("table token secret must contain at least 32 UTF-8 bytes")
            self._key = secret.encode("utf-8")
        elif isinstance(secret, bytes):
            if len(secret) < 32:
                raise ValueError("table token secret must contain at least 32 bytes")
            self._key = secret
        else:
            raise TypeError("table token secret must be str, bytes, or None")

    def sign(self, payload: Mapping[str, object]) -> str:
        encoded = _canonical_json(payload)
        if len(encoded) > MAX_TOKEN_PAYLOAD_BYTES:
            raise TableProtocolError("table continuation state is too large")
        encoded_part = _urlsafe_b64encode(encoded)
        signature = hmac.digest(self._key, encoded_part.encode("ascii"), "sha256")
        token = f"{encoded_part}.{_urlsafe_b64encode(signature)}"
        if len(token) > MAX_TOKEN_BYTES:
            raise TableProtocolError("table continuation token is too large")
        return token

    def verify(self, token: object) -> dict[str, object]:
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_BYTES:
            raise TableContinuationError("invalid table continuation")
        pieces = token.split(".")
        if len(pieces) != 2 or not all(pieces):
            raise TableContinuationError("invalid table continuation")
        encoded_part, signature_part = pieces
        try:
            payload_bytes = _urlsafe_b64decode(encoded_part)
            supplied_signature = _urlsafe_b64decode(signature_part)
        except (ValueError, binascii.Error):
            raise TableContinuationError("invalid table continuation") from None
        if len(payload_bytes) > MAX_TOKEN_PAYLOAD_BYTES or len(supplied_signature) != 32:
            raise TableContinuationError("invalid table continuation")
        if (
            _urlsafe_b64encode(payload_bytes) != encoded_part
            or _urlsafe_b64encode(supplied_signature) != signature_part
        ):
            raise TableContinuationError("invalid table continuation")
        expected_signature = hmac.digest(self._key, encoded_part.encode("ascii"), "sha256")
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise TableContinuationError("invalid table continuation")
        try:
            value = json.loads(payload_bytes.decode("utf-8"), object_pairs_hook=_no_duplicate_object_pairs)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise TableContinuationError("invalid table continuation") from None
        if not isinstance(value, dict):
            raise TableContinuationError("invalid table continuation")
        try:
            if _canonical_json(value) != payload_bytes:
                raise TableContinuationError("invalid table continuation")
        except TableProtocolError:
            raise TableContinuationError("invalid table continuation") from None
        return cast(dict[str, object], value)


class TableRuntime:
    """Engine-local table dispatcher, renderer, and continuation authority."""

    def __init__(
        self,
        *,
        engine: "SiteEngine",
        token_secret: str | bytes | None = None,
        token_ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
    ) -> None:
        if isinstance(token_ttl_seconds, bool) or not isinstance(token_ttl_seconds, int) or token_ttl_seconds <= 0:
            raise ValueError("table token TTL must be a positive integer")
        self.engine = engine
        self.signer = TableTokenSigner(token_secret)
        self.token_ttl_seconds = token_ttl_seconds
        self._clock = time.time

    def render(self, context: WidgetContext, **props: object) -> WidgetRenderResult:
        provider, page_size, row_template, caption, provider_args = self._parse_props(props)
        page_state = _json_object(context.page, field="page context")
        query_state = _json_object(context.query, field="query")
        table_page = self._provide(
            provider=provider,
            route=context.route,
            widget_id=context.widget_id,
            query=query_state,
            page=page_state,
            page_size=page_size,
            cursor=None,
            revision=None,
            provider_args=provider_args,
        )
        tbody = self._render_tbody(
            table_page,
            row_template=row_template,
            route=context.route,
            widget_id=context.widget_id,
            query=query_state,
            page_context=page_state,
        )
        if context.render_mode == "serve":
            next_token = self._continuation_token(
                provider=provider,
                route=context.route,
                widget_id=context.widget_id,
                query=query_state,
                page=page_state,
                page_size=page_size,
                provider_args=provider_args,
                row_template=row_template,
                columns=table_page.columns,
                revision=table_page.revision,
                direction="next",
                cursor=table_page.next_cursor,
            )
            previous_token = self._continuation_token(
                provider=provider,
                route=context.route,
                widget_id=context.widget_id,
                query=query_state,
                page=page_state,
                page_size=page_size,
                provider_args=provider_args,
                row_template=row_template,
                columns=table_page.columns,
                revision=table_page.revision,
                direction="previous",
                cursor=table_page.previous_cursor,
            )
            live = True
        else:
            next_token = None
            previous_token = None
            live = False
        return WidgetRenderResult(
            self._render_table_shell(
                context=context,
                page=table_page,
                tbody=tbody,
                next_token=next_token,
                previous_token=previous_token,
                live=live,
                caption=caption,
            )
        )

    def continuation(self, payload: object) -> dict[str, object]:
        if not isinstance(payload, Mapping):
            raise TableContinuationError("invalid table continuation request")
        if set(payload) != {"token", "route", "widget_id"}:
            raise TableContinuationError("invalid table continuation request")
        route = payload.get("route")
        widget_id = payload.get("widget_id")
        if not isinstance(route, str) or not route or len(route) > 512:
            raise TableContinuationError("invalid table continuation request")
        if not isinstance(widget_id, str) or not widget_id or len(widget_id) > 256:
            raise TableContinuationError("invalid table continuation request")
        state = self._validated_state(self.signer.verify(payload.get("token")))
        if state["route"] != route or state["widget_id"] != widget_id:
            raise TableContinuationError("table continuation belongs to another widget")
        now = int(self._clock())
        if state["expires"] < now:
            raise TableContinuationExpired("table continuation has expired")
        table_page = self._provide(
            provider=state["provider"],
            route=state["route"],
            widget_id=state["widget_id"],
            query=state["query"],
            page=state["page"],
            page_size=state["page_size"],
            cursor=state["cursor"],
            revision=state["revision"],
            provider_args=state["args"],
        )
        if table_page.revision != state["revision"]:
            raise TableRevisionMismatch("provider result revision changed during pagination")
        if table_page.columns != state["columns"]:
            raise TableProviderError("provider changed table columns during pagination")
        row_template = state["row_template"]
        tbody = self._render_tbody(
            table_page,
            row_template=row_template,
            route=state["route"],
            widget_id=state["widget_id"],
            query=state["query"],
            page_context=state["page"],
        )
        next_token = self._continuation_token(
            provider=state["provider"],
            route=state["route"],
            widget_id=state["widget_id"],
            query=state["query"],
            page=state["page"],
            page_size=state["page_size"],
            provider_args=state["args"],
            row_template=row_template,
            columns=table_page.columns,
            revision=table_page.revision,
            direction="next",
            cursor=table_page.next_cursor,
        )
        previous_token = self._continuation_token(
            provider=state["provider"],
            route=state["route"],
            widget_id=state["widget_id"],
            query=state["query"],
            page=state["page"],
            page_size=state["page_size"],
            provider_args=state["args"],
            row_template=row_template,
            columns=table_page.columns,
            revision=table_page.revision,
            direction="previous",
            cursor=table_page.previous_cursor,
        )
        response: dict[str, object] = {
            "tbody": tbody,
            "next": next_token,
            "previous": previous_token,
            "total": table_page.total,
            "summary": self._summary(table_page),
        }
        try:
            response_bytes = json.dumps(
                response,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise TableProviderError("provider response is not JSON-compatible") from exc
        if len(response_bytes) > MAX_TABLE_RESPONSE_BYTES:
            raise TableProviderError("provider page is too large to render")
        return response

    def _parse_props(self, props: Mapping[str, object]) -> tuple[str, int, str | None, str, dict[str, object]]:
        values = dict(props)
        values.pop("id", None)
        provider = values.pop("provider", None)
        if not isinstance(provider, str) or not provider.strip() or len(provider) > 256:
            raise TableProtocolError("table provider must be a non-empty string")
        page_size = values.pop("page_size", DEFAULT_PAGE_SIZE)
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= MAX_PAGE_SIZE:
            raise TableProtocolError(f"table page_size must be an integer between 1 and {MAX_PAGE_SIZE}")
        row_template = values.pop("row_template", None)
        if row_template is not None:
            if not isinstance(row_template, str) or not row_template.strip() or len(row_template) > 256:
                raise TableProtocolError("table row_template must be a non-empty relative template name")
            self._validate_row_template(row_template)
        caption = values.pop("caption", "Data table")
        if not isinstance(caption, str) or not caption.strip() or len(caption) > 256:
            raise TableProtocolError("table caption must be a non-empty string of at most 256 characters")
        return (
            provider.strip(),
            page_size,
            row_template,
            caption.strip(),
            _json_object(values, field="provider arguments"),
        )

    def _provide(
        self,
        *,
        provider: str,
        route: str,
        widget_id: str,
        query: dict[str, object],
        page: dict[str, object],
        page_size: int,
        cursor: str | None,
        revision: str | None,
        provider_args: dict[str, object],
    ) -> TablePage:
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in query.items()):
            raise TableContinuationError("invalid table continuation")
        provider_context = ProviderContext(
            route=route,
            widget_id=widget_id,
            query=cast(dict[str, str], query),
            page=page,
            global_data=self.engine.global_data,
            _url_builder=lambda target, query: self._provider_url(route, target, query),
        )
        try:
            result = self.engine.function_handler.execute_provider(
                provider_name=provider,
                context=provider_context,
                request=TableRequest(page_size=page_size, cursor=cursor, revision=revision),
                provider_args=provider_args,
            )
            table_page = TablePage.from_result(result)
        except TableProtocolError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            _LOGGER.exception("httk.table provider %r failed for route %r", provider, route)
            raise TableProviderError(str(exc)) from exc
        except Exception as exc:
            _LOGGER.exception("httk.table provider %r failed for route %r", provider, route)
            raise TableProviderError(f"provider failed ({type(exc).__name__})") from exc
        if len(table_page.rows) > page_size:
            raise TableProviderError("provider returned more rows than requested page_size")
        return table_page

    def _provider_url(self, source_route: str, target: str, query: Mapping[str, str] | None) -> str:
        normalized_target = target.strip()
        if "?" in normalized_target or "#" in normalized_target or "\\" in normalized_target:
            raise ValueError("provider URL route must not contain query, fragment, or backslash syntax")
        candidate = Path(normalized_target)
        if candidate.is_absolute() or ".." in candidate.parts or "." in candidate.parts:
            raise ValueError("provider URL route must be a contained site route")
        target_route = str(candidate).replace("\\", "/")
        if not target_route:
            raise ValueError("provider URL route must be a non-empty string")
        path = self.engine._route_link_url(
            source_route_key=source_route,
            target_route_key=target_route,
            render_mode="serve",
            relative_start=False,
        )
        encoded_query = urlencode(dict(query or {}))
        return f"{path}?{encoded_query}" if encoded_query else path

    def _continuation_token(
        self,
        *,
        provider: str,
        route: str,
        widget_id: str,
        query: dict[str, object],
        page: dict[str, object],
        page_size: int,
        provider_args: dict[str, object],
        row_template: str | None,
        columns: tuple[TableColumn, ...],
        revision: str | None,
        direction: str,
        cursor: str | None,
    ) -> str | None:
        if cursor is None:
            return None
        payload: dict[str, object] = {
            "args": provider_args,
            "columns": [asdict(column) for column in columns],
            "cursor": cursor,
            "direction": direction,
            "expires": int(self._clock()) + self.token_ttl_seconds,
            "page": page,
            "page_size": page_size,
            "provider": provider,
            "query": query,
            "revision": revision,
            "route": route,
            "row_template": row_template,
            "version": TOKEN_VERSION,
            "widget_id": widget_id,
        }
        return self.signer.sign(payload)

    def _validated_state(self, state: Mapping[str, object]) -> _TableState:
        expected_keys = {
            "args",
            "columns",
            "cursor",
            "direction",
            "expires",
            "page",
            "page_size",
            "provider",
            "query",
            "revision",
            "route",
            "row_template",
            "version",
            "widget_id",
        }
        if set(state) != expected_keys or state.get("version") != TOKEN_VERSION:
            raise TableContinuationError("invalid table continuation")
        provider = state.get("provider")
        route = state.get("route")
        widget_id = state.get("widget_id")
        cursor = state.get("cursor")
        direction = state.get("direction")
        expires = state.get("expires")
        page_size = state.get("page_size")
        row_template = state.get("row_template")
        revision = state.get("revision")
        if (
            not isinstance(provider, str)
            or not provider
            or len(provider) > 256
            or not isinstance(route, str)
            or not route
            or len(route) > 512
            or not isinstance(widget_id, str)
            or not widget_id
            or len(widget_id) > 256
            or not isinstance(cursor, str)
            or len(cursor) > MAX_CURSOR_CHARS
            or direction not in {"next", "previous"}
            or isinstance(expires, bool)
            or not isinstance(expires, int)
            or isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= MAX_PAGE_SIZE
            or (row_template is not None and not isinstance(row_template, str))
            or (revision is not None and (not isinstance(revision, str) or len(revision) > 256))
        ):
            raise TableContinuationError("invalid table continuation")
        try:
            args = _json_object(state.get("args"), field="provider arguments")
            query = _json_object(state.get("query"), field="query")
            page = _json_object(state.get("page"), field="page")
            raw_columns = state.get("columns")
            if not isinstance(raw_columns, list):
                raise TableProtocolError("invalid columns")
            columns = tuple(TableColumn.from_value(column) for column in raw_columns)
            if not columns or len(columns) > 64 or len({column.key for column in columns}) != len(columns):
                raise TableProtocolError("invalid columns")
            if row_template is not None:
                self._validate_row_template(row_template)
        except (TypeError, ValueError, TableProtocolError):
            raise TableContinuationError("invalid table continuation") from None
        return _TableState(
            args=args,
            columns=columns,
            cursor=cast(str, cursor),
            direction=cast(str, direction),
            expires=cast(int, expires),
            page=page,
            page_size=cast(int, page_size),
            provider=cast(str, provider),
            query=query,
            revision=cast(str | None, revision),
            route=cast(str, route),
            row_template=cast(str | None, row_template),
            widget_id=cast(str, widget_id),
        )

    def _validate_row_template(self, name: str) -> None:
        candidate = Path(name.strip())
        if candidate.is_absolute() or ".." in candidate.parts:
            raise TableProtocolError("table row_template must stay within the templates directory")
        root = self.engine.config.template_dir.resolve(strict=False)
        try:
            (root / candidate).resolve(strict=False).relative_to(root)
        except ValueError as exc:
            raise TableProtocolError("table row_template must stay within the templates directory") from exc
        if self._row_template_path(candidate) is None:
            raise TableProtocolError(f"table row_template was not found: {name}")

    def _row_template_path(self, candidate: Path) -> Path | None:
        root = self.engine.config.template_dir
        direct = root / candidate
        if direct.exists() and direct.is_file():
            return direct
        if candidate.suffix:
            if candidate.name.endswith(".html"):
                for suffix in (".j2", ".jinja"):
                    alternative = root / f"{candidate}{suffix}"
                    if alternative.exists() and alternative.is_file():
                        return alternative
            return None
        suffixes = getattr(self.engine.template_engine, "template_suffixes", ())
        for suffix in suffixes:
            alternative = root / f"{candidate}{suffix}"
            if alternative.exists() and alternative.is_file():
                return alternative
        return None

    def _render_tbody(
        self,
        page: TablePage,
        *,
        row_template: str | None,
        route: str,
        widget_id: str,
        query: dict[str, object],
        page_context: dict[str, object],
    ) -> str:
        rows: list[str] = []
        for row in page.rows:
            if row_template is not None:
                template_context: dict[str, object] = {
                    "row": row,
                    "columns": page.columns,
                    "page": page_context,
                    "query": query,
                    "table": {"route": route, "widget_id": widget_id},
                }
                rendered = self.engine.template_engine.render_fragment(
                    template_name=row_template, context=template_context
                )
                if rendered is None:
                    raise TableProviderError(f"table row_template was not found: {row_template}")
                rows.append(rendered)
            else:
                cells: list[str] = []
                for column in page.columns:
                    classes = ["httk-table__cell"]
                    if column.align is not None:
                        classes.append(f"httk-table__cell--{column.align}")
                    if column.class_name is not None:
                        classes.append(column.class_name)
                    cells.append(
                        f'<td class="{escape(" ".join(classes), quote=True)}">'
                        f"{escape(_cell_text(row.get(column.key)), quote=False)}</td>"
                    )
                rows.append("<tr>" + "".join(cells) + "</tr>")
        tbody = "".join(rows)
        if len(tbody.encode("utf-8")) > MAX_TABLE_HTML_BYTES:
            raise TableProviderError("provider page is too large to render")
        return tbody

    def _render_table_shell(
        self,
        *,
        context: WidgetContext,
        page: TablePage,
        tbody: str,
        next_token: str | None,
        previous_token: str | None,
        live: bool,
        caption: str,
    ) -> str:
        headers = "".join(
            (
                f'<th scope="col" class="httk-table__header{_column_modifier(column)}">'
                f"{escape(column.label, quote=False)}</th>"
            )
            for column in page.columns
        )
        previous_disabled = "" if live and previous_token is not None else " disabled"
        next_disabled = "" if live and next_token is not None else " disabled"
        previous_attr = escape(previous_token or "", quote=True)
        next_attr = escape(next_token or "", quote=True)
        route = escape(context.route, quote=True)
        widget_id = escape(context.widget_id, quote=True)
        status = self._summary(page)
        if not live and (page.next_cursor is not None or page.previous_cursor is not None):
            status = f"{status} Pagination is available on the live site."
        assets = ""
        internal_root = self._internal_root(context)
        if live:
            assets = (
                f'<link rel="stylesheet" href="{internal_root}/assets/table.css">'
                f'<script defer src="{internal_root}/assets/table.js"></script>'
            )
        return (
            f"{assets}<section class=\"httk-table\" data-httk-table=\"1\" data-route=\"{route}\" "
            f"data-widget-id=\"{widget_id}\" data-endpoint=\"{internal_root}/table/page\" aria-busy=\"false\">"
            f"<table><caption>{escape(caption, quote=False)}</caption><thead><tr>{headers}</tr></thead><tbody>{tbody}</tbody></table>"
            "<nav class=\"httk-table__pager\" aria-label=\"Table pagination\">"
            f'<button type="button" data-httk-table-previous data-token="{previous_attr}"{previous_disabled}>Previous</button>'
            f'<span data-httk-table-status role="status" aria-live="polite">{escape(status, quote=False)}</span>'
            f'<button type="button" data-httk-table-next data-token="{next_attr}"{next_disabled}>Next</button>'
            "</nav></section>"
        )

    @staticmethod
    def _internal_root(context: WidgetContext) -> str:
        relative_base = context.page.get("relbaseurl", ".")
        if not isinstance(relative_base, str) or not relative_base or relative_base.startswith("/"):
            raise TableProtocolError("page context has no safe relative base URL")
        return f"{relative_base.rstrip('/')}/_httk"

    @staticmethod
    def _summary(page: TablePage) -> str:
        count = len(page.rows)
        noun = "row" if count == 1 else "rows"
        if page.total is not None:
            return f"Showing {count} of {page.total} {noun}."
        return f"Showing {count} {noun}."


class TableWidget:
    """Built-in adapter supplied with :class:`httk.web.widgets.WidgetContext`."""

    name = "httk.table"
    source = "httk.web.widgets.table"

    def render(self, context: WidgetContext, **props: object) -> WidgetRenderResult:
        runtime = context.table_runtime
        if not isinstance(runtime, TableRuntime):
            raise TableProtocolError("httk.table needs an engine table runtime")
        return runtime.render(context, **props)


def _column_modifier(column: TableColumn) -> str:
    classes: list[str] = []
    if column.align is not None:
        classes.append(f" httk-table__cell--{column.align}")
    if column.class_name is not None:
        classes.append(f" {escape(column.class_name, quote=True)}")
    return "".join(classes)


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str | int | bool):
        return str(value)
    if isinstance(value, float):
        return str(value) if math.isfinite(value) else ""
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return ", ".join(_cell_text(item) for item in value)
    raise TableProviderError("default table cells support only text, numbers, booleans, and simple sequences")


def _json_object(value: object, *, field: str) -> dict[str, object]:
    normalized = _json_value(value, field=field)
    if not isinstance(normalized, dict):
        raise TableProtocolError(f"{field} must be a mapping")
    return normalized


def _json_value(value: object, *, field: str, depth: int = 0) -> object:
    if depth > 12:
        raise TableProtocolError(f"{field} is nested too deeply")
    if value is None or isinstance(value, bool | str | int):
        if isinstance(value, str) and len(value) > 16_384:
            raise TableProtocolError(f"{field} contains an oversized string")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TableProtocolError(f"{field} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        if len(value) > 256:
            raise TableProtocolError(f"{field} contains too many entries")
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 256:
                raise TableProtocolError(f"{field} keys must be compact strings")
            normalized[key] = _json_value(item, field=field, depth=depth + 1)
        return normalized
    if isinstance(value, list | tuple):
        if len(value) > 256:
            raise TableProtocolError(f"{field} contains too many values")
        return [_json_value(item, field=field, depth=depth + 1) for item in value]
    raise TableProtocolError(f"{field} must contain JSON-compatible literal values")


def _canonical_json(value: Mapping[str, object]) -> bytes:
    try:
        normalized = _json_object(value, field="table state")
        return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode(
            "ascii"
        )
    except (TypeError, ValueError) as exc:
        raise TableProtocolError("table state is not JSON-compatible") from exc


def _urlsafe_b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _urlsafe_b64decode(value: str) -> bytes:
    if not value or any(
        char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for char in value
    ):
        raise ValueError("not base64url")
    padded = value + "=" * (-len(value) % 4)
    return base64.b64decode(padded, altchars=b"-_", validate=True)


def _no_duplicate_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result
