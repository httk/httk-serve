"""The server-rendered OPTIMADE browse widget: type/kind picker, condition builder, table."""

import re
from collections.abc import Mapping
from html import escape

from .core import WidgetContext, WidgetRenderResult
from .optimade_catalog import _explorer_base_url, _explorer_shell, _site_route
from .optimade_table import render as render_optimade_table

MAX_QUERY_IDENTIFIER_CHARS = 128
_QUERY_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")
_CURATED_COLUMNS = {
    "structures": ("id", "chemical_formula_reduced", "nelements", "nsites"),
    "_httk_runs": ("id", "_httk_name", "_httk_source_id", "_httk_finished_at"),
    "files": ("id", "name", "size", "media_type"),
    "references": ("id", "title"),
}
_COLUMN_LABELS = {
    "id": "ID",
    "chemical_formula_reduced": "Formula",
    "nelements": "Elements",
    "nsites": "Sites",
    "_httk_name": "Name",
    "_httk_source_id": "Source",
    "_httk_finished_at": "Finished",
    "name": "Name",
    "size": "Size (bytes)",
    "media_type": "Media type",
    "title": "Title",
    "last_modified": "Last modified",
}
_TYPE_LABELS = {
    "structures": "Structures",
    "calculations": "Calculations",
    "references": "References",
    "files": "Files",
    "_httk_runs": "Runs (provenance)",
    "_httk_records": "Result records",
}


def render(
    context: WidgetContext, *, base_url: str, entry_route: str = "entry", page_size: int = 50
) -> WidgetRenderResult:
    """Render the browse page for ``?type=<type>&kind=<kind>&filter=<filter>&sort=<sort>``.

    A missing or invalid ``type`` (or ``kind``) renders only the picker. A valid type
    renders an ``optimade_table`` with curated columns, a raw-filter disclosure and
    detail links to ``entry_route?type=<type>&id=<id>``; for ``_httk_records`` a
    ``kind`` names a record-value property and selects records where it is known.
    The browser fills the type/kind picker and the condition builder from ``/info``.

    :param context: Immutable widget invocation context.
    :param base_url: OPTIMADE base URL: absolute, root-relative, or site-root-relative.
    :param entry_route: Site route of the single-entry page.
    :param page_size: Default number of rows per table page.
    :return: Browse shell, table, and their trusted assets.
    :raises httk.serve.web.widgets.optimade_table.OptimadeTableProtocolError: If an authored argument is invalid.
    """

    resolved_base_url = _explorer_base_url(context, base_url)
    entry = _site_route(entry_route, field="entry_route")
    entry_type, type_valid = _query_identifier(context.query, "type")
    kind, kind_valid = _query_identifier(context.query, "kind")
    if entry_type != "_httk_records":
        kind = None
    if not (type_valid and kind_valid):
        entry_type = kind = None
        message = "The requested entry type or record kind is not valid. Choose what to browse."
    elif entry_type is None:
        message = "Choose what to browse."
    else:
        message = ""

    table = None
    columns: list[str] = []
    if entry_type is not None:
        columns = _columns(entry_type, kind)
        label = _TYPE_LABELS.get(entry_type, entry_type)
        table = render_optimade_table(
            context,
            base_url=resolved_base_url,
            entry_type=entry_type,
            columns=[
                {"key": key, "label": _COLUMN_LABELS.get(key, key)}
                | ({"format": "formula"} if key == "chemical_formula_reduced" else {})
                for key in columns
            ],
            page_size=page_size,
            page_size_query="page_size",
            caption=f"{label}: {kind}" if kind else label,
            filter=f"{kind} IS KNOWN" if kind else None,
            filter_query="filter",
            sort_query="sort",
            detail_route=entry,
            detail_column="id",
            detail_parameters={"type": entry_type},
            advanced_filter={"label": "Raw OPTIMADE filter"},
        )

    configuration = {
        "base_url": resolved_base_url,
        "entry_type": entry_type,
        "kind": kind,
        "widget_id": context.widget_id,
    }
    body = (
        _picker(entry_type, kind)
        + f'<p class="httk-serve-optimade-explorer__note" data-httk-serve-optimade-browser-status role="status"'
        f"{'' if message else ' hidden'}>{escape(message)}</p>"
        + (_conditions() + table.html if table is not None else "")
    )
    return _explorer_shell(context, "browser", configuration, body, extra_assets=table.assets if table else ())


def _query_identifier(query: Mapping[str, str], name: str) -> tuple[str | None, bool]:
    """Return a URL identifier value and whether it was absent-or-valid."""

    value = query.get(name)
    if value is None or value == "":
        return None, True
    if len(value) > MAX_QUERY_IDENTIFIER_CHARS or not _QUERY_IDENTIFIER.fullmatch(value):
        return None, False
    return value, True


def _columns(entry_type: str, kind: str | None) -> list[str]:
    # Curated columns are ones httk stores always advertise; a foreign server lacking one
    # gets the table's own "does not advertise" error (accepted).
    if entry_type == "_httk_records":
        return ["id", kind] if kind else ["id", "last_modified"]
    return list(_CURATED_COLUMNS.get(entry_type, ("id", "last_modified")))


def _picker(entry_type: str | None, kind: str | None) -> str:
    current = (
        f'<option value="{escape(entry_type)}" selected>{escape(_TYPE_LABELS.get(entry_type, entry_type))}</option>'
        if entry_type
        else '<option value="" selected>Choose…</option>'
    )
    kind_option = f'<option value="{escape(kind)}" selected>{escape(kind)}</option>' if kind else ""
    records = entry_type == "_httk_records"
    return (
        '<form class="httk-serve-optimade-explorer__picker" method="get" data-httk-serve-optimade-picker>'
        f'<label>Browse <select name="type" data-httk-serve-optimade-type>{current}</select></label>'
        f'<label data-httk-serve-optimade-kind-label{"" if records else " hidden"}>Record kind '
        f'<select name="kind" data-httk-serve-optimade-kind{"" if records else " disabled"}>'
        f'<option value=""{"" if kind else " selected"}>All records</option>{kind_option}</select></label>'
        '<button type="submit">Show</button>'
        "</form>"
    )


def _conditions() -> str:
    return (
        '<form class="httk-serve-optimade-explorer__conditions" data-httk-serve-optimade-conditions hidden>'
        "<fieldset><legend>Search</legend>"
        "<div data-httk-serve-optimade-condition-rows></div>"
        '<label class="httk-serve-optimade-explorer__condition" data-httk-serve-optimade-elements-label hidden>'
        'Elements <input type="text" data-httk-serve-optimade-elements placeholder="Si, O" autocomplete="off"></label>'
        '<div class="httk-serve-optimade-explorer__conditions-actions">'
        '<button type="button" data-httk-serve-optimade-add-condition>Add condition</button>'
        '<button type="submit">Search</button></div>'
        '<p class="httk-serve-optimade-explorer__preview">Filter: <output data-httk-serve-optimade-preview></output></p>'
        '<p class="httk-serve-optimade-explorer__problem" role="alert" data-httk-serve-optimade-condition-error hidden></p>'
        "</fieldset></form>"
    )
