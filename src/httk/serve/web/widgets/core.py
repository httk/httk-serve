"""The public contracts for static httk-serve widgets."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, cast

from markupsafe import Markup

MAX_WIDGET_ASSET_BYTES = 1_000_000
"""Largest individual trusted widget asset accepted by :class:`WidgetAsset`."""

SUPPORTED_WIDGET_ASSET_CONTENT_TYPES = frozenset({"text/css", "text/javascript"})
"""The deliberately small content-type vocabulary for internal widget assets."""


@dataclass(frozen=True)
class WidgetAsset:
    """An immutable, deployment-relative asset declared by trusted widget code.

    ``path`` is relative to ``/_httk/serve/assets/`` and is never interpreted as a
    filesystem path.  The engine serves only assets it has registered while
    rendering this site instance.
    """

    path: str
    content: bytes
    content_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path or len(self.path) > 256:
            raise ValueError("widget asset path must be a non-empty string of at most 256 characters")
        if (
            self.path.startswith("/")
            or "\\" in self.path
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in self.path)
        ):
            raise ValueError("widget asset path must be a safe relative POSIX path")
        segments = self.path.split("/")
        if any(not segment or segment in {".", ".."} for segment in segments):
            raise ValueError("widget asset path must not contain empty or dot segments")
        if any(
            not all(char.isascii() and (char.isalnum() or char in "._-") for char in segment) for segment in segments
        ):
            raise ValueError("widget asset path contains unsupported characters")
        if not isinstance(self.content, bytes):
            raise TypeError("widget asset content must be immutable bytes")
        if not self.content:
            raise ValueError("widget asset content must not be empty")
        if len(self.content) > MAX_WIDGET_ASSET_BYTES:
            raise ValueError(f"widget asset content exceeds {MAX_WIDGET_ASSET_BYTES} bytes")
        if not isinstance(self.content_type, str) or self.content_type not in SUPPORTED_WIDGET_ASSET_CONTENT_TYPES:
            allowed = ", ".join(sorted(SUPPORTED_WIDGET_ASSET_CONTENT_TYPES))
            raise ValueError(f"widget asset content type must be one of: {allowed}")


@dataclass(frozen=True)
class WidgetContext:
    """Immutable request and page information made available to widgets."""

    route: str
    render_mode: str
    widget_id: str
    query: Mapping[str, str]
    postvars: Mapping[str, str]
    page: Mapping[str, object]
    source_path: Path
    url_for: Callable[[str], str]
    absolute_url_for: Callable[[str], str]
    table_runtime: object | None = None


@dataclass(frozen=True)
class WidgetRenderResult:
    """An explicitly trusted HTML result returned by a widget."""

    html: str
    assets: tuple[WidgetAsset, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.assets, tuple) or not all(isinstance(asset, WidgetAsset) for asset in self.assets):
            raise TypeError("WidgetRenderResult.assets must be a tuple of WidgetAsset values")


class Widget(Protocol):
    """Advanced immutable widget definition protocol."""

    @property
    def name(self) -> str: ...

    @property
    def source(self) -> str: ...

    def render(self, context: WidgetContext, **props: object) -> str | WidgetRenderResult: ...


WidgetRenderer = Callable[..., str | WidgetRenderResult]


@dataclass(frozen=True)
class FunctionWidget:
    """An immutable adapter for the common module-level ``render`` facade."""

    name: str
    render_function: WidgetRenderer
    source: str

    def render(self, context: WidgetContext, **props: object) -> str | WidgetRenderResult:
        return self.render_function(context, **props)


def function_widget(render: WidgetRenderer, *, name: str = "", source: str = "") -> FunctionWidget:
    """Wrap a callable as a :class:`FunctionWidget`.

    Site-local modules normally need no wrapper: a module-level ``render`` is
    discovered automatically.  The helper is useful for explicit definitions.
    """

    return FunctionWidget(name=name, render_function=render, source=source)


def trusted_html(value: str) -> WidgetRenderResult:
    """Mark a widget's reviewed HTML output as trusted."""

    return WidgetRenderResult(Markup(value))


def _immutable_mapping[ValueT](values: Mapping[str, ValueT]) -> Mapping[str, ValueT]:
    """Return a recursively immutable snapshot for widget-facing context."""

    return cast(Mapping[str, ValueT], MappingProxyType({key: _immutable_value(value) for key, value in values.items()}))


def _immutable_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _immutable_value(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_immutable_value(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_immutable_value(item) for item in value)
    return value


@dataclass
class WidgetRegistry:
    """Registry for built-ins; aliases never participate in site resolution."""

    _widgets: dict[str, Widget] = field(default_factory=dict)
    _aliases: dict[str, str] = field(default_factory=dict)

    def register(self, widget: Widget, *, alias: str | None = None) -> None:
        if not widget.name.startswith("httk."):
            raise ValueError("Built-in widget names must use the 'httk.' prefix")
        if widget.name in self._widgets:
            raise ValueError(f"Widget is already registered: {widget.name}")
        self._widgets[widget.name] = widget
        if alias:
            if alias in self._aliases:
                raise ValueError(f"Widget alias is already registered: {alias}")
            self._aliases[alias] = widget.name

    def resolve(self, name: str) -> Widget | None:
        return self._widgets.get(self._aliases.get(name, name))

    def available(self) -> list[tuple[str, str]]:
        return sorted((name, widget.source) for name, widget in self._widgets.items())


def _text_widget(context: WidgetContext, *, text: object = "", **props: object) -> str:
    """A tiny built-in useful for smoke tests and safe examples."""

    del context, props
    return str(text)


BUILTIN_WIDGETS = WidgetRegistry()
BUILTIN_WIDGETS.register(
    FunctionWidget(name="httk.text", render_function=_text_widget, source="httk.serve.web.widgets.core"), alias="text"
)

# Imported after the public contracts above so the table implementation can use
# the same WidgetContext and WidgetRenderResult types without an import cycle.
from .optimade_table import render as _render_optimade_table
from .table import TableWidget

BUILTIN_WIDGETS.register(TableWidget(), alias="table")
BUILTIN_WIDGETS.register(
    FunctionWidget(
        name="httk.serve.optimade_table",
        render_function=_render_optimade_table,
        source="httk.serve.web.widgets.optimade_table",
    ),
    alias="optimade_table",
)
