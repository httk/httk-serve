"""The browser-driven OPTIMADE catalog widget and the shell shared by the explorer widgets.

``optimade_catalog``, ``optimade_browser`` and ``optimade_entry`` emit an inert
shell plus JSON configuration; their browser modules fetch only through
``OptimadeTransport`` and render served data as DOM text, never as markup.
"""

from collections.abc import Mapping
from functools import cache
from html import escape
from importlib.resources import files
from urllib.parse import urlsplit

from httk.serve.web.providers import _validate_site_route

from .core import WidgetAsset, WidgetContext, WidgetRenderResult
from .optimade_assets import optimade_protocol_asset
from .optimade_table import OptimadeTableProtocolError, _base_url, _internal_root, _safe_json


def render(context: WidgetContext, *, base_url: str, browse_route: str = "browse") -> WidgetRenderResult:
    """Render a home-page catalog of the entry types an OPTIMADE service serves.

    The browser shows the provider name and description from ``/info``, one card per
    served entry type with its description and entry count, and the result-record
    kinds of ``_httk_records`` as links into the browse page.

    :param context: Immutable widget invocation context.
    :param base_url: OPTIMADE base URL: absolute, root-relative, or site-root-relative
        (no scheme and no leading ``/``, resolved against the page's relative base URL).
    :param browse_route: Site route of the browse page receiving ``type`` and ``kind``.
    :return: Catalog shell and its trusted assets.
    :raises httk.serve.web.widgets.optimade_table.OptimadeTableProtocolError: If ``base_url`` or
        ``browse_route`` is invalid.
    """

    configuration = {
        "base_url": _explorer_base_url(context, base_url),
        "browse_route": context.url_for(_site_route(browse_route, field="browse_route")),
        "widget_id": context.widget_id,
    }
    body = _status("Loading the OPTIMADE catalog.")
    return _explorer_shell(context, "catalog", configuration, body)


def _explorer_base_url(context: WidgetContext, value: object) -> str:
    text = _base_url(value)
    if urlsplit(text).scheme or text.startswith("/"):
        return text
    # _internal_root validates the page's relative base; the OPTIMADE mount sits beside it.
    return f"{_internal_root(context).removesuffix('/_httk/serve')}/{text}"


def _site_route(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise OptimadeTableProtocolError(f"{field} must be a safe relative site route")
    try:
        return _validate_site_route(value)
    except ValueError as exc:
        raise OptimadeTableProtocolError(f"{field}: {exc}") from exc


def _status(message: str) -> str:
    return (
        f'<p class="httk-serve-optimade-explorer__note" role="status" aria-live="polite">{escape(message)}</p>'
        '<noscript><p class="httk-serve-optimade-explorer__note">This view requires JavaScript.</p></noscript>'
    )


@cache
def _asset(name: str) -> WidgetAsset:
    content_type = "text/css" if name.endswith(".css") else "text/javascript"
    return WidgetAsset(name, files("httk.serve.web").joinpath("assets", name).read_bytes(), content_type)


def _explorer_assets(name: str) -> tuple[WidgetAsset, ...]:
    return (
        _asset("serve-optimade-explorer.css"),
        optimade_protocol_asset(),
        _asset("serve-optimade-present.mjs"),
        _asset(f"serve-optimade-{name}.mjs"),
    )


def _explorer_shell(
    context: WidgetContext,
    name: str,
    configuration: Mapping[str, object],
    body: str,
    *,
    extra_assets: tuple[WidgetAsset, ...] = (),
) -> WidgetRenderResult:
    internal_root = _internal_root(context)
    widget_id = escape(context.widget_id, quote=True)
    config_id = escape(f"httk-serve-optimade-{name}-{context.widget_id}-config", quote=True)
    html = (
        f'<link rel="stylesheet" href="{internal_root}/assets/serve-optimade-explorer.css">'
        f'<script type="module" src="{internal_root}/assets/serve-optimade-{name}.mjs"></script>'
        f'<section class="httk-serve-optimade-explorer httk-serve-optimade-{name}" data-httk-serve-optimade-{name}="1" '
        f'data-widget-id="{widget_id}" data-config-id="{config_id}" aria-busy="true">'
        f"{body}"
        f'<script id="{config_id}" type="application/json">{_safe_json(configuration)}</script>'
        "</section>"
    )
    assets = tuple(dict.fromkeys((*_explorer_assets(name), *extra_assets)))
    return WidgetRenderResult(html, assets=assets)
