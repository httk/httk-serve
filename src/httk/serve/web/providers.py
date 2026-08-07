"""Small, dependency-free contracts for site-local paginated data providers."""

import json
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Self
from urllib.parse import urlencode

MAX_CURSOR_CHARS = 16_384
MAX_ROW_DATA_BYTES = 512_000


def _validate_site_route(route: str) -> str:
    """Return a canonical provider route or reject non-local URL syntax.

    Provider routes name pages in the site's route namespace; they are never
    general URLs.  Keeping this check at the public contract boundary means
    providers cannot accidentally turn an HTML ``href`` into a scheme or an
    external/protocol-relative URL when an engine supplies the builder.
    """

    if not isinstance(route, str) or not route.strip():
        raise ValueError("provider URL route must be a non-empty string")
    normalized = route.strip()
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in normalized):
        raise ValueError("provider URL route must not contain control characters")
    if any(character in normalized for character in (":", "?", "#", "\\")):
        raise ValueError("provider URL route must not contain URL, query, fragment, or backslash syntax")
    if normalized.startswith("/"):
        raise ValueError("provider URL route must be a relative site route")
    segments = normalized.split("/")
    if any(segment in {".", ".."} for segment in segments):
        raise ValueError("provider URL route must not contain dot path segments")
    return normalized


def _default_url_builder(route: str, query: Mapping[str, str] | None) -> str:
    encoded_query = urlencode(dict(query or {}))
    return f"{route}?{encoded_query}" if encoded_query else route


def immutable_mapping(values: Mapping[str, object]) -> Mapping[str, object]:
    """Snapshot mappings and standard containers into immutable values.

    Other leaves are preserved as-is.

    :param values: String-keyed mapping to snapshot.
    :return: Mapping proxy containing immutable mappings and standard containers.
    """

    return MappingProxyType({key: _immutable_value(value) for key, value in values.items()})


def _immutable_value(value: object) -> object:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("provider context mappings must use string keys")
        return immutable_mapping(dict(value))
    if isinstance(value, list | tuple):
        return tuple(_immutable_value(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_immutable_value(item) for item in value)
    return value


@dataclass(frozen=True)
class ProviderContext:
    """Provide immutable site-local context to a table provider.

    :param route: Route containing the table widget.
    :param widget_id: Stable identifier of the table widget on the route.
    :param query: Request query values.
    :param page: Page context supplied by the site.
    :param global_data: Site-global data supplied by startup code.
    """

    route: str
    widget_id: str
    query: Mapping[str, str]
    page: Mapping[str, object]
    global_data: Mapping[str, object]
    _url_builder: Callable[[str, Mapping[str, str] | None], str] = _default_url_builder

    def __post_init__(self) -> None:
        if not self.route:
            raise ValueError("ProviderContext.route cannot be empty")
        if not self.widget_id:
            raise ValueError("ProviderContext.widget_id cannot be empty")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in self.query.items()):
            raise TypeError("ProviderContext.query must map strings to strings")
        if not all(isinstance(key, str) for key in self.page):
            raise TypeError("ProviderContext.page must use string keys")
        if not all(isinstance(key, str) for key in self.global_data):
            raise TypeError("ProviderContext.global_data must use string keys")
        object.__setattr__(self, "query", immutable_mapping(dict(self.query)))
        object.__setattr__(self, "page", immutable_mapping(dict(self.page)))
        object.__setattr__(self, "global_data", immutable_mapping(dict(self.global_data)))

    def url_for(self, route: str, *, query: Mapping[str, str] | None = None) -> str:
        """Build a site-local route URL without exposing the ASGI request.

        :param route: Relative site route to link to.
        :param query: Optional string query values.
        :return: Site-local URL for the route.
        :raises ValueError: If the route is not a safe relative site route.
        :raises TypeError: If query keys or values are not strings.
        """

        route = _validate_site_route(route)
        if query is not None and not all(
            isinstance(key, str) and isinstance(value, str) for key, value in query.items()
        ):
            raise TypeError("provider URL query must map strings to strings")
        return self._url_builder(route, query)


@dataclass(frozen=True)
class TableRequest:
    """Describe one bounded page request passed to a table provider.

    :param page_size: Maximum number of rows requested.
    :param cursor: Opaque provider cursor for continuation.
    :param revision: Optional provider revision pinned across pages.
    """

    page_size: int = 50
    cursor: str | None = None
    revision: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.page_size, bool) or not isinstance(self.page_size, int) or not 1 <= self.page_size <= 500:
            raise ValueError("TableRequest.page_size must be an integer between 1 and 500")
        if self.cursor is not None and (not isinstance(self.cursor, str) or len(self.cursor) > MAX_CURSOR_CHARS):
            raise ValueError("TableRequest.cursor must be a string of at most 16384 characters or None")
        if self.revision is not None and (not isinstance(self.revision, str) or len(self.revision) > 256):
            raise ValueError("TableRequest.revision must be a string of at most 256 characters or None")


@dataclass(frozen=True)
class TableColumn:
    """Describe one table column and its presentation-only metadata.

    :param key: Stable row value key.
    :param label: Accessible column label.
    :param align: Optional horizontal alignment.
    :param class_name: Optional whitespace-free CSS class name.
    """

    key: str
    label: str
    align: str | None = None
    class_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key or len(self.key) > 128:
            raise ValueError("TableColumn.key must be a non-empty string of at most 128 characters")
        if not isinstance(self.label, str) or len(self.label) > 256:
            raise ValueError("TableColumn.label must be a string of at most 256 characters")
        if self.align not in {None, "start", "center", "end"}:
            raise ValueError("TableColumn.align must be 'start', 'center', 'end', or None")
        if self.class_name is not None and (
            not isinstance(self.class_name, str)
            or len(self.class_name) > 128
            or any(char.isspace() for char in self.class_name)
        ):
            raise ValueError("TableColumn.class_name must be a whitespace-free string of at most 128 characters")

    @classmethod
    def from_value(cls, value: Self | str | Mapping[str, object]) -> Self:
        """Adapt a compact column declaration to a table column.

        :param value: Existing column, string key, or explicit field mapping.
        :return: Normalized table column.
        :raises TypeError: If the declaration has an unsupported shape.
        :raises ValueError: If the declaration contains unknown or invalid fields.
        """

        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(key=value, label=value.replace("_", " ").title())
        if not isinstance(value, Mapping):
            raise TypeError("Table columns must be TableColumn values, strings, or mappings")
        allowed = {"key", "label", "align", "class_name"}
        extra = set(value).difference(allowed)
        if extra:
            raise ValueError(f"Unknown TableColumn fields: {', '.join(sorted(map(str, extra)))}")
        key = value.get("key")
        label = value.get("label", key)
        if not isinstance(key, str) or not isinstance(label, str):
            raise TypeError("TableColumn mapping requires string key and label")
        align = value.get("align")
        class_name = value.get("class_name")
        if align is not None and not isinstance(align, str):
            raise TypeError("TableColumn.align must be a string or None")
        if class_name is not None and not isinstance(class_name, str):
            raise TypeError("TableColumn.class_name must be a string or None")
        return cls(key=key, label=label, align=align, class_name=class_name)


@dataclass(frozen=True)
class TablePage:
    """Represent one bounded page of presentation rows and opaque cursors.

    :param rows: Presentation-only row mappings.
    :param columns: Table columns.
    :param next_cursor: Opaque cursor for the next page.
    :param previous_cursor: Opaque cursor for the previous page.
    :param total: Optional exact total row count.
    :param revision: Optional provider revision for stable pagination.
    """

    rows: tuple[Mapping[str, object], ...]
    columns: tuple[TableColumn, ...]
    next_cursor: str | None = None
    previous_cursor: str | None = None
    total: int | None = None
    revision: str | None = None

    def __post_init__(self) -> None:
        try:
            rows = tuple(self.rows)
        except TypeError as exc:
            raise TypeError("TablePage.rows must be an iterable of mappings") from exc
        try:
            columns = tuple(TableColumn.from_value(column) for column in self.columns)
        except TypeError as exc:
            raise TypeError("TablePage.columns must be an iterable of table-column values") from exc
        if not columns:
            raise ValueError("TablePage.columns cannot be empty")
        if len(columns) > 64:
            raise ValueError("TablePage supports at most 64 columns")
        if len({column.key for column in columns}) != len(columns):
            raise ValueError("TablePage column keys must be unique")
        normalized_rows: list[Mapping[str, object]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise TypeError("TablePage rows must be mappings")
            if not all(isinstance(key, str) for key in row):
                raise TypeError("TablePage row keys must be strings")
            if len(row) > 256:
                raise ValueError("TablePage rows may contain at most 256 fields")
            normalized_rows.append(immutable_mapping(_presentation_mapping(row)))
        for cursor_name, cursor in (("next_cursor", self.next_cursor), ("previous_cursor", self.previous_cursor)):
            if cursor is not None and (not isinstance(cursor, str) or len(cursor) > MAX_CURSOR_CHARS):
                raise ValueError(f"TablePage.{cursor_name} must be a string of at most 16384 characters or None")
        if self.total is not None and (
            isinstance(self.total, bool) or not isinstance(self.total, int) or self.total < 0
        ):
            raise ValueError("TablePage.total must be a non-negative integer or None")
        if self.revision is not None and (not isinstance(self.revision, str) or len(self.revision) > 256):
            raise ValueError("TablePage.revision must be a string of at most 256 characters or None")
        object.__setattr__(self, "rows", tuple(normalized_rows))
        object.__setattr__(self, "columns", columns)
        try:
            encoded_rows = json.dumps(
                normalized_rows, default=_json_proxy_default, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError) as exc:
            raise TypeError("TablePage rows must contain JSON-like presentation values") from exc
        if len(encoded_rows.encode("utf-8")) > MAX_ROW_DATA_BYTES:
            raise ValueError("TablePage rows exceed the 512000-byte presentation limit")

    @classmethod
    def from_rows(
        cls,
        rows: Iterable[Mapping[str, object]],
        *,
        columns: Iterable[TableColumn | str | Mapping[str, object]],
        next_cursor: str | None = None,
        previous_cursor: str | None = None,
        total: int | None = None,
        revision: str | None = None,
    ) -> Self:
        """Construct a page while adapting compact column declarations.

        :param rows: Presentation-only row mappings.
        :param columns: Column declarations.
        :param next_cursor: Opaque cursor for the next page.
        :param previous_cursor: Opaque cursor for the previous page.
        :param total: Optional exact total row count.
        :param revision: Optional provider revision for stable pagination.
        :return: Normalized table page.
        """

        return cls(
            rows=tuple(rows),
            columns=tuple(TableColumn.from_value(column) for column in columns),
            next_cursor=next_cursor,
            previous_cursor=previous_cursor,
            total=total,
            revision=revision,
        )

    @classmethod
    def from_result(cls, value: object) -> Self:
        """Normalize a table page or equivalent explicit mapping.

        :param value: Provider result to normalize.
        :return: Normalized table page.
        :raises TypeError: If the result is not a table page or mapping.
        :raises ValueError: If the mapping contains invalid fields or values.
        """

        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("Provider provide() must return TablePage or a table-page mapping")
        allowed = {"rows", "columns", "next_cursor", "previous_cursor", "total", "revision"}
        extra = set(value).difference(allowed)
        if extra:
            raise ValueError(f"Unknown TablePage fields: {', '.join(sorted(map(str, extra)))}")
        rows = value.get("rows")
        columns = value.get("columns")
        if isinstance(rows, str | bytes) or not isinstance(rows, Iterable):
            raise TypeError("TablePage mapping requires iterable rows")
        if isinstance(columns, str | bytes) or not isinstance(columns, Iterable):
            raise TypeError("TablePage mapping requires iterable columns")
        return cls.from_rows(
            rows,
            columns=columns,
            next_cursor=value.get("next_cursor"),
            previous_cursor=value.get("previous_cursor"),
            total=value.get("total"),
            revision=value.get("revision"),
        )


def _presentation_mapping(value: Mapping[str, object], *, depth: int = 0) -> dict[str, object]:
    return {key: _presentation_value(item, depth=depth + 1) for key, item in value.items()}


def _presentation_value(value: object, *, depth: int = 0) -> object:
    """Copy only neutral values which are safe to give both render paths."""

    if depth > 12:
        raise ValueError("TablePage row values are nested too deeply")
    if value is None or type(value) in {bool, int, str}:
        if type(value) is str and len(value) > 16_384:
            raise ValueError("TablePage row strings may contain at most 16384 characters")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("TablePage row values cannot contain non-finite floats")
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("TablePage nested row mappings must use string keys")
        if len(value) > 256:
            raise ValueError("TablePage nested row mappings may contain at most 256 fields")
        return immutable_mapping(_presentation_mapping(value, depth=depth))
    if isinstance(value, list | tuple):
        if len(value) > 256:
            raise ValueError("TablePage row sequences may contain at most 256 values")
        return tuple(_presentation_value(item, depth=depth + 1) for item in value)
    raise TypeError("TablePage rows support only JSON-like presentation values")


def _json_proxy_default(value: object) -> object:
    if isinstance(value, MappingProxyType):
        return dict(value)
    if isinstance(value, tuple):
        return list(value)
    raise TypeError
