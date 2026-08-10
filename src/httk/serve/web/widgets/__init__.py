"""Static, safe widget authoring contracts and discovery helpers."""

from .core import (
    MAX_WIDGET_ASSET_BYTES,
    SUPPORTED_WIDGET_ASSET_CONTENT_TYPES,
    FunctionWidget,
    Widget,
    WidgetAsset,
    WidgetContext,
    WidgetRegistry,
    WidgetRenderResult,
    function_widget,
    trusted_html,
)
from .loader import SiteWidgetLoader
from .optimade_assets import optimade_protocol_asset, optimade_protocol_href
from .optimade_table import OptimadeTableProtocolError

__all__ = [
    "MAX_WIDGET_ASSET_BYTES",
    "SUPPORTED_WIDGET_ASSET_CONTENT_TYPES",
    "FunctionWidget",
    "OptimadeTableProtocolError",
    "SiteWidgetLoader",
    "Widget",
    "WidgetAsset",
    "WidgetContext",
    "WidgetRegistry",
    "WidgetRenderResult",
    "function_widget",
    "optimade_protocol_asset",
    "optimade_protocol_href",
    "trusted_html",
]
