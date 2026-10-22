import json
import re
from collections.abc import Mapping
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from httk.serve.web.api import create_asgi_app
from httk.serve.web.widgets import OptimadeTableProtocolError, WidgetContext
from httk.serve.web.widgets.core import BUILTIN_WIDGETS
from httk.serve.web.widgets.optimade_browser import render as render_browser
from httk.serve.web.widgets.optimade_catalog import render as render_catalog
from httk.serve.web.widgets.optimade_entry import render as render_entry
from httk.serve.web.widgets.optimade_table import render as render_table


def _context(query: Mapping[str, str] | None = None, relbaseurl: str = ".") -> WidgetContext:
    return WidgetContext(
        route="browse",
        render_mode="serve",
        widget_id="w",
        query=dict(query or {}),
        postvars={},
        page={"relbaseurl": relbaseurl},
        source_path=Path("browse.md"),
        url_for=lambda route: route,
        absolute_url_for=lambda route: route,
    )


def _configs(html: str) -> dict[str, dict[str, object]]:
    return {
        name: json.loads(body)
        for name, body in re.findall(
            r'<script id="httk-serve-optimade-(\w+)-[^"]*-config" type="application/json">(.*?)</script>', html
        )
    }


def _asset_paths(result: object) -> set[str]:
    return {asset.path for asset in result.assets}  # type: ignore[attr-defined]


_EXPLORER = {"serve-optimade-explorer.css", "serve-optimade-table-protocol.mjs", "serve-optimade-present.mjs"}


@pytest.mark.parametrize(
    ("base_url", "relbaseurl", "expected"),
    [
        ("optimade", ".", "./optimade"),
        ("optimade", "..", "../optimade"),
        ("/optimade", "..", "/optimade"),
        ("https://api.example.test/optimade", ".", "https://api.example.test/optimade"),
    ],
)
def test_catalog_resolves_site_root_relative_base_and_declares_assets(
    base_url: str, relbaseurl: str, expected: str
) -> None:
    result = render_catalog(_context(relbaseurl=relbaseurl), base_url=base_url, browse_route="browse")
    assert _configs(result.html)["catalog"] == {"base_url": expected, "browse_route": "browse", "widget_id": "w"}
    assert 'data-httk-serve-optimade-catalog="1"' in result.html
    assert _asset_paths(result) == _EXPLORER | {"serve-optimade-catalog.mjs"}


def test_entry_shell_config_and_assets() -> None:
    result = render_entry(_context(), base_url="optimade", entry_route="entry", browse_route="browse")
    assert _configs(result.html)["entry"] == {
        "base_url": "./optimade",
        "browse_route": "browse",
        "entry_route": "entry",
        "widget_id": "w",
    }
    assert _asset_paths(result) == _EXPLORER | {"serve-optimade-entry.mjs"}


@pytest.mark.parametrize(
    "bad", [{"base_url": "javascript:alert(1)"}, {"base_url": "//evil.example"}, {"browse_route": "/abs"}]
)
def test_explorer_widgets_reject_invalid_authored_arguments(bad: dict[str, str]) -> None:
    arguments = {"base_url": "optimade", "browse_route": "browse"} | bad
    with pytest.raises(OptimadeTableProtocolError):
        render_catalog(_context(), **arguments)


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ({}, "Choose what to browse."),
        ({"type": ""}, "Choose what to browse."),
        ({"type": "Structures"}, "not valid"),
        ({"type": "../x"}, "not valid"),
        ({"type": "x" * 129}, "not valid"),
        ({"type": "_httk_records", "kind": "bad kind"}, "not valid"),
    ],
)
def test_browser_without_a_valid_type_renders_only_the_picker(query: dict[str, str], message: str) -> None:
    result = render_browser(_context(query), base_url="optimade", entry_route="entry")
    configs = _configs(result.html)
    assert set(configs) == {"browser"}
    assert configs["browser"]["entry_type"] is None and configs["browser"]["kind"] is None
    assert "data-httk-serve-optimade-picker" in result.html
    assert "data-httk-serve-optimade-conditions" not in result.html
    assert message in result.html
    assert _asset_paths(result) == _EXPLORER | {"serve-optimade-browser.mjs"}


def test_browser_with_a_type_renders_a_real_optimade_table() -> None:
    result = render_browser(
        _context({"type": "structures", "filter": 'nsites > 2'}), base_url="optimade", entry_route="entry"
    )
    configs = _configs(result.html)
    table = configs["table"]
    assert [column["key"] for column in table["columns"]] == ["id", "chemical_formula_reduced", "nelements", "nsites"]  # type: ignore[index, union-attr]
    assert table["base_url"] == "./optimade"
    assert table["entry_type"] == "structures"
    assert table["filter"] is None
    assert (table["filter_query"], table["sort_query"], table["page_size_query"]) == ("filter", "sort", "page_size")
    assert (table["detail_route"], table["detail_column"], table["detail_query"]) == ("entry", "id", "id")
    assert table["detail_parameters"] == {"type": "structures"}
    assert table["advanced_filter"] == {"label": "Raw OPTIMADE filter", "help_url": None}
    assert "data-httk-serve-optimade-conditions" in result.html
    assert {"serve-optimade-table.mjs", "serve-optimade-table.css"} < _asset_paths(result)


@pytest.mark.parametrize(
    ("query", "columns", "authored_filter", "kind"),
    [
        (
            {"type": "_httk_records", "kind": "_httk_total_energy"},
            ["id", "_httk_total_energy"],
            "_httk_total_energy IS KNOWN",
            "_httk_total_energy",
        ),
        ({"type": "_httk_records"}, ["id", "last_modified"], None, None),
        ({"type": "_httk_runs"}, ["id", "_httk_name", "_httk_source_id", "_httk_finished_at"], None, None),
        ({"type": "files"}, ["id", "name", "size", "media_type"], None, None),
        ({"type": "references"}, ["id", "title"], None, None),
        ({"type": "_exmpl_things"}, ["id", "last_modified"], None, None),
        # A kind only applies to result records.
        ({"type": "files", "kind": "_httk_total_energy"}, ["id", "name", "size", "media_type"], None, None),
        # There is no column override parameter.
        (
            {"type": "structures", "columns": "id"},
            ["id", "chemical_formula_reduced", "nelements", "nsites"],
            None,
            None,
        ),
    ],
)
def test_browser_columns_kind_and_filter(
    query: dict[str, str], columns: list[str], authored_filter: str | None, kind: str | None
) -> None:
    configs = _configs(render_browser(_context(query), base_url="optimade").html)
    assert [column["key"] for column in configs["table"]["columns"]] == columns  # type: ignore[index, union-attr]
    assert configs["table"]["filter"] == authored_filter
    assert configs["browser"]["kind"] == kind


def test_table_detail_parameters_are_validated_and_serialized() -> None:
    plain = _configs(render_table(_context(), base_url="/optimade", columns=["id"]).html)["table"]
    assert plain["detail_parameters"] is None
    with_parameters = render_table(
        _context(),
        base_url="/optimade",
        columns=["id"],
        detail_route="entry",
        detail_column="id",
        detail_parameters={"type": "files", "view": "full"},
    )
    assert _configs(with_parameters.html)["table"]["detail_parameters"] == {"type": "files", "view": "full"}
    for bad in ({"id": "x"}, {"bad key": "x"}, {"type": ""}, {}, {f"k{index}": "v" for index in range(9)}):
        with pytest.raises(OptimadeTableProtocolError):
            render_table(
                _context(),
                base_url="/optimade",
                columns=["id"],
                detail_route="entry",
                detail_column="id",
                detail_parameters=bad,
            )
    with pytest.raises(OptimadeTableProtocolError, match="requires detail_route"):
        render_table(_context(), base_url="/optimade", columns=["id"], detail_parameters={"type": "files"})


def test_explorer_widgets_are_registered_with_aliases() -> None:
    for name in ("optimade_catalog", "optimade_browser", "optimade_entry"):
        widget = BUILTIN_WIDGETS.resolve(f"httk.serve.{name}")
        assert widget is not None and BUILTIN_WIDGETS.resolve(name) is widget


def test_explorer_pages_render_and_serve_their_assets(tmp_path: Path) -> None:
    src = tmp_path / "src"
    for name in ("content", "static", "templates", "functions", "widgets"):
        (src / name).mkdir(parents=True)
    # Page invocations use the aliases: the widget-name grammar allows one httk./site. prefix only.
    pages = {
        "index": '{{ widget("optimade_catalog", base_url="optimade", browse_route="browse") }}',
        "browse": '{{ widget("optimade_browser", base_url="optimade", entry_route="entry") }}',
        "entry": '{{ widget("optimade_entry", base_url="optimade", entry_route="entry", browse_route="browse") }}',
    }
    for page, invocation in pages.items():
        (src / "content" / f"{page}.md").write_text(invocation, encoding="utf-8")
    with TestClient(create_asgi_app(src)) as client:
        assert "catalog" in _configs(client.get("/").text)
        assert set(_configs(client.get("/browse").text)) == {"browser"}
        browse = _configs(client.get("/browse", params={"type": "files"}).text)
        assert browse["table"]["detail_parameters"] == {"type": "files"}
        assert "entry" in _configs(client.get("/entry", params={"type": "files", "id": "x"}).text)
        for asset in (
            *_EXPLORER,
            "serve-optimade-catalog.mjs",
            "serve-optimade-browser.mjs",
            "serve-optimade-entry.mjs",
        ):
            assert client.get(f"/_httk/serve/assets/{asset}").status_code == 200, asset
