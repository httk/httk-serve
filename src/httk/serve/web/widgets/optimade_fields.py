"""A server-side-static table of OPTIMADE property definitions.

Unlike ``optimade_table`` this widget performs no browser fetch and ships no
JavaScript: the served schema's property definitions are known at startup, so
the table is rendered once from the ``properties`` mapping a site supplies.
"""

import re
from collections.abc import Mapping
from html import escape
from importlib.resources import files
from urllib.parse import urlsplit

from .core import WidgetAsset, WidgetContext, WidgetRenderResult
from .optimade_table import (
    MAX_OPTIMADE_LABEL_CHARS,
    MAX_OPTIMADE_TEXT_CHARS,
    MAX_OPTIMADE_URL_CHARS,
    OptimadeTableProtocolError,
    _identifier,
    _internal_root,
    _text,
)

MAX_OPTIMADE_FIELDS = 512
_BLANK_LINE = re.compile(r"\n[^\S\n]*\n")
_OPTIMADE_FIELDS_ASSETS: tuple[WidgetAsset, ...] | None = None


def render(
    context: WidgetContext,
    *,
    properties: object,
    caption: str = "Field definitions",
) -> WidgetRenderResult:
    """Render a static table of OPTIMADE property definitions.

    Each row links a property's prefixed name to the human-readable definition
    page at its ``$id`` and shows the first paragraph of its ``description``.
    Ad-hoc synthesized ids (whose path contains ``/ad-hoc/``) and any non
    HTTP(S) id render the name as plain text rather than a link.

    :param context: Immutable widget invocation context.
    :param properties: Mapping of served property name to its OPTIMADE
        property-definition mapping (each carrying ``$id`` and ``description``).
    :param caption: Accessible table caption.
    :return: Static field-definition table and its stylesheet asset.
    :raises OptimadeTableProtocolError: If ``properties`` is not a non-empty
        mapping of at most 512 identifier-named definition mappings.
    """
    normalized_caption = _text(caption, field="caption", maximum=MAX_OPTIMADE_LABEL_CHARS)
    rows = _rows(properties)
    internal_root = _internal_root(context)
    body = "".join(
        f"<tr>{_name_cell(name, link)}<td>{escape(description, quote=False)}</td></tr>"
        for name, link, description in rows
    )
    html = (
        f'<section class="httk-serve-optimade-fields">'
        f'<link rel="stylesheet" href="{internal_root}/assets/serve-optimade-fields.css">'
        f"<table><caption>{escape(normalized_caption, quote=False)}</caption>"
        '<thead><tr><th scope="col">Field</th><th scope="col">Description</th></tr></thead>'
        f"<tbody>{body}</tbody></table>"
        "</section>"
    )
    return WidgetRenderResult(html, assets=_optimade_fields_assets())


def _rows(value: object) -> list[tuple[str, str | None, str]]:
    if not isinstance(value, Mapping):
        raise OptimadeTableProtocolError("properties must be a mapping of property name to definition mapping")
    if not value:
        raise OptimadeTableProtocolError("properties must not be empty")
    if len(value) > MAX_OPTIMADE_FIELDS:
        raise OptimadeTableProtocolError(f"properties may contain at most {MAX_OPTIMADE_FIELDS} entries")
    rows: list[tuple[str, str | None, str]] = []
    for name, definition in value.items():
        identifier = _identifier(name, field="property name")
        if not isinstance(definition, Mapping):
            raise OptimadeTableProtocolError("each property definition must be a mapping")
        rows.append((identifier, _link(definition.get("$id")), _description(definition.get("description"))))
    rows.sort(key=lambda row: row[0])
    return rows


def _name_cell(name: str, link: str | None) -> str:
    code = f"<code>{escape(name, quote=False)}</code>"
    if link is None:
        return f"<td>{code}</td>"
    return f'<td><a href="{escape(link, quote=True)}" target="_blank" rel="noopener noreferrer">{code}</a></td>'


def _description(value: object) -> str:
    if not isinstance(value, str):
        return ""
    first_paragraph = _BLANK_LINE.split(value, maxsplit=1)[0].strip()
    return first_paragraph[:MAX_OPTIMADE_TEXT_CHARS]


def _link(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > MAX_OPTIMADE_URL_CHARS:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.username is not None or parsed.password is not None:
            return None
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if "/ad-hoc/" in parsed.path:
        return None
    return value


def _optimade_fields_assets() -> tuple[WidgetAsset, ...]:
    global _OPTIMADE_FIELDS_ASSETS
    if _OPTIMADE_FIELDS_ASSETS is None:
        assets = files("httk.serve.web").joinpath("assets")
        _OPTIMADE_FIELDS_ASSETS = (
            WidgetAsset(
                "serve-optimade-fields.css", assets.joinpath("serve-optimade-fields.css").read_bytes(), "text/css"
            ),
        )
    return _OPTIMADE_FIELDS_ASSETS
