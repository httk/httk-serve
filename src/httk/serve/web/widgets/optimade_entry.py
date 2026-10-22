"""The browser-driven single-entry OPTIMADE widget."""

from .core import WidgetContext, WidgetRenderResult
from .optimade_catalog import _explorer_base_url, _explorer_shell, _site_route, _status


def render(
    context: WidgetContext, *, base_url: str, entry_route: str = "entry", browse_route: str = "browse"
) -> WidgetRenderResult:
    """Render a single-entry page shell for ``entry?type=<type>&id=<id>``.

    The browser shows the entry's header and type panel, its provenance and other
    relationships with inline summaries of the related entries, its alternatives,
    and every served property. An id ``<main>~<kind>`` opens that httk alternative.

    :param context: Immutable widget invocation context.
    :param base_url: OPTIMADE base URL: absolute, root-relative, or site-root-relative.
    :param entry_route: Site route of this entry page, used for links to related entries.
    :param browse_route: Site route of the browse page, used for the not-found state.
    :return: Entry shell and its trusted assets.
    :raises httk.serve.web.widgets.optimade_table.OptimadeTableProtocolError: If an argument is invalid.
    """

    configuration = {
        "base_url": _explorer_base_url(context, base_url),
        "browse_route": context.url_for(_site_route(browse_route, field="browse_route")),
        "entry_route": context.url_for(_site_route(entry_route, field="entry_route")),
        "widget_id": context.widget_id,
    }
    return _explorer_shell(context, "entry", configuration, _status("Loading the OPTIMADE entry."))
