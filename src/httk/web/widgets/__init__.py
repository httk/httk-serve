"""Static, safe widget authoring contracts and discovery helpers."""

from .core import (
    FunctionWidget,
    Widget,
    WidgetContext,
    WidgetRegistry,
    WidgetRenderResult,
    function_widget,
    trusted_html,
)
from .loader import SiteWidgetLoader

__all__ = [
    "FunctionWidget",
    "SiteWidgetLoader",
    "Widget",
    "WidgetContext",
    "WidgetRegistry",
    "WidgetRenderResult",
    "function_widget",
    "trusted_html",
]
