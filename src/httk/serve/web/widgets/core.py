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
    """Declare an immutable, deployment-relative asset from trusted widget code.

    ``path`` is relative to ``/_httk/serve/assets/`` and is never interpreted as a
    filesystem path.  The engine serves only assets it has registered while
    rendering this site instance.

    :param path: Safe path below ``/_httk/serve/assets/``.
    :param content: Immutable asset bytes.
    :param content_type: Supported asset content type.
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
    """Provide immutable request and page information to a widget.

    :param route: Route containing the widget.
    :param render_mode: ``serve`` for live rendering or ``publish`` for static output.
    :param widget_id: Stable identifier for this widget placement.
    :param query: Request query values.
    :param postvars: Parsed request body values.
    :param page: Page metadata and context.
    :param source_path: Source file containing the widget invocation.
    :param url_for: Builder for site-relative URLs.
    :param absolute_url_for: Builder for absolute site URLs.
    :param table_runtime: Engine-local table runtime when available.
    """

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
    """Return explicitly trusted HTML and its declared widget assets.

    :param html: Trusted HTML output.
    :param assets: Immutable assets used by the output.
    """

    html: str
    assets: tuple[WidgetAsset, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.assets, tuple) or not all(isinstance(asset, WidgetAsset) for asset in self.assets):
            raise TypeError("WidgetRenderResult.assets must be a tuple of WidgetAsset values")


class Widget(Protocol):
    """Define the advanced immutable widget protocol."""

    @property
    def name(self) -> str:
        """Return the widget's canonical name."""
        ...

    @property
    def source(self) -> str:
        """Return the widget's source identifier."""
        ...

    def render(self, context: WidgetContext, **props: object) -> str | WidgetRenderResult:
        """Render trusted widget output for one invocation."""
        ...


WidgetRenderer = Callable[..., str | WidgetRenderResult]


@dataclass(frozen=True)
class FunctionWidget:
    """Adapt a module-level ``render`` facade to the widget protocol.

    :param name: Canonical widget name.
    :param render_function: Function used to render the widget.
    :param source: Source identifier for diagnostics and discovery.
    """

    name: str
    render_function: WidgetRenderer
    source: str

    def render(self, context: WidgetContext, **props: object) -> str | WidgetRenderResult:
        """Render the widget through its wrapped callable.

        :param context: Immutable widget invocation context.
        :param \\*\\*props: Literal widget properties.
        :return: HTML string or explicitly trusted render result.
        """
        return self.render_function(context, **props)


def function_widget(render: WidgetRenderer, *, name: str = "", source: str = "") -> FunctionWidget:
    """Wrap a callable as a :class:`FunctionWidget`.

    Site-local modules normally need no wrapper: a module-level ``render`` is
    discovered automatically.  The helper is useful for explicit definitions.

    :param render: Function used to render the widget.
    :param name: Canonical widget name.
    :param source: Source identifier for diagnostics and discovery.
    :return: Immutable function-backed widget.
    """

    return FunctionWidget(name=name, render_function=render, source=source)


def trusted_html(value: str) -> WidgetRenderResult:
    """Mark a widget's reviewed HTML output as trusted.

    :param value: HTML reviewed by the widget author.
    :return: Trusted widget render result.
    """

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
    """Register built-in widgets whose aliases do not resolve site widgets."""

    _widgets: dict[str, Widget] = field(default_factory=dict)
    _aliases: dict[str, str] = field(default_factory=dict)

    def register(self, widget: Widget, *, alias: str | None = None) -> None:
        """Register one built-in widget and optional display alias.

        :param widget: Built-in widget to register.
        :param alias: Optional shorthand alias.
        :raises ValueError: If the name or alias is already registered.
        """
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
        """Resolve a built-in name or alias.

        :param name: Built-in widget name or alias.
        :return: Matching widget, or ``None`` when absent.
        """
        return self._widgets.get(self._aliases.get(name, name))

    def available(self) -> list[tuple[str, str]]:
        """List built-in widget names and source identifiers.

        :return: Sorted ``(name, source)`` pairs.
        """
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
