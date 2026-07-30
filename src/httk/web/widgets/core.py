"""The public contracts for static httk-web widgets."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, cast

from markupsafe import Markup


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


@dataclass(frozen=True)
class WidgetRenderResult:
    """An explicitly trusted HTML result returned by a widget."""

    html: str


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
    FunctionWidget(name="httk.text", render_function=_text_widget, source="httk.web.widgets.core"), alias="text"
)
