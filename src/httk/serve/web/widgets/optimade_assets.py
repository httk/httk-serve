"""Expose the built-in OPTIMADE protocol asset to site-local widgets."""

from importlib.resources import files

from .core import WidgetAsset, WidgetContext


def _internal_root(context: WidgetContext) -> str:
    """Return the deployment-relative internal asset root for a page.

    :param context: Widget invocation context.
    :return: Safe relative internal root URL.
    :raises ValueError: If the page has no safe relative base URL.
    """
    relative_base = context.page.get("relbaseurl", ".")
    if not isinstance(relative_base, str) or not relative_base or relative_base.startswith("/"):
        raise ValueError("page context has no safe relative base URL")
    return f"{relative_base.rstrip('/')}/_httk/serve"


def optimade_protocol_asset() -> WidgetAsset:
    """Return the registered-compatible built-in OPTIMADE protocol module.

    :return: The protocol module asset used by ``optimade_table``.
    """
    asset = files("httk.serve.web").joinpath("assets/serve-optimade-table-protocol.mjs")
    return WidgetAsset("serve-optimade-table-protocol.mjs", asset.read_bytes(), "text/javascript")


def optimade_protocol_href(context: WidgetContext) -> str:
    """Return the protocol module URL for a page.

    :param context: Widget invocation context.
    :return: Deployment-relative protocol module URL.
    """
    return f"{_internal_root(context)}/assets/serve-optimade-table-protocol.mjs"
