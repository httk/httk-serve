import json
import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from httk.serve.web.api import create_asgi_app, publish
from httk.serve.web.engine.site_engine import SiteEngine
from httk.serve.web.model.config import SiteConfig
from httk.serve.web.publishing.static import publish_site
from httk.serve.web.widgets import (
    MAX_WIDGET_ASSET_BYTES,
    OptimadeTableProtocolError,
    WidgetAsset,
    WidgetContext,
    WidgetRenderResult,
    optimade_protocol_asset,
    optimade_protocol_href,
)
from httk.serve.web.widgets.assets import WidgetAssetRegistry
from httk.serve.web.widgets.optimade_table import render as render_optimade_table


def _src(tmp_path: Path, content: str) -> Path:
    src = tmp_path / "src"
    for name in ("content", "static", "templates", "functions", "widgets"):
        (src / name).mkdir(parents=True)
    (src / "content" / "index.md").write_text(content, encoding="utf-8")
    return src


def test_widget_asset_is_immutable_and_rejects_unsafe_values() -> None:
    asset = WidgetAsset("nested/site.css", b".site{}", "text/css")
    result = WidgetRenderResult("<p>ok</p>", assets=(asset,))
    assert result.assets == (asset,)
    with pytest.raises(ValueError):
        WidgetAsset("../secret.css", b"x", "text/css")
    with pytest.raises(ValueError):
        WidgetAsset("site\\secret.css", b"x", "text/css")
    with pytest.raises(ValueError):
        WidgetAsset("site.css", b"x" * (MAX_WIDGET_ASSET_BYTES + 1), "text/css")
    with pytest.raises(TypeError):
        WidgetRenderResult("<p>bad</p>", assets=[asset])  # type: ignore[arg-type]
    registry = WidgetAssetRegistry()
    assert registry.register(asset) is asset
    assert registry.register(WidgetAsset("nested/site.css", b".site{}", "text/css")) == asset
    with pytest.raises(ValueError, match="conflicting"):
        registry.register(WidgetAsset("nested/site.css", b".changed{}", "text/css"))


def test_public_optimade_protocol_asset_matches_builtin_href() -> None:
    context = WidgetContext(
        route="guide",
        render_mode="serve",
        widget_id="table",
        query={},
        postvars={},
        page={"relbaseurl": ".."},
        source_path=Path("guide.md"),
        url_for=lambda route: route,
        absolute_url_for=lambda route: route,
    )
    result = render_optimade_table(context, base_url="/optimade/v1", columns=["nsites"])
    asset = optimade_protocol_asset()
    assert asset.path == "serve-optimade-table-protocol.mjs"
    assert asset in result.assets
    assert optimade_protocol_href(context) in result.html


def test_optimade_column_formats_are_strict_and_serialized() -> None:
    context = WidgetContext(
        route="index",
        render_mode="serve",
        widget_id="table",
        query={},
        postvars={},
        page={"relbaseurl": "."},
        source_path=Path("index.md"),
        url_for=lambda route: route,
        absolute_url_for=lambda route: route,
    )
    result = render_optimade_table(
        context,
        base_url="/optimade/v1",
        sort_query="sort",
        columns=[
            {"key": "formula", "format": "formula"},
            {"key": "energy", "format": {"name": "number", "digits": 2, "scale": 2, "suffix": " eV"}},
            {"key": "elements", "format": {"name": "join"}},
        ],
    )
    assert '"sort_query":"sort"' in result.html
    assert '"scale":2.0' in result.html
    for invalid in (
        {"name": "number"},
        {"name": "number", "digits": 11},
        {"name": "number", "digits": 1, "scale": 0},
        {"name": "join", "separator": "x" * 17},
        {"name": "join", "unknown": ","},
        "html",
    ):
        with pytest.raises(OptimadeTableProtocolError):
            render_optimade_table(context, base_url="/optimade/v1", columns=[{"key": "x", "format": invalid}])


def test_optimade_sort_aliases_are_validated_and_serialized() -> None:
    context = WidgetContext(
        route="index",
        render_mode="serve",
        widget_id="table",
        query={},
        postvars={},
        page={"relbaseurl": "."},
        source_path=Path("index.md"),
        url_for=lambda route: route,
        absolute_url_for=lambda route: route,
    )
    result = render_optimade_table(
        context,
        base_url="/optimade/v1",
        columns=["nsites"],
        sort_query="sort",
        sort_aliases={"rank": "id", "best": "-nsites,id"},
    )
    config = _optimade_config(result.html)
    assert config["sort_aliases"] == {"rank": "id", "best": "-nsites,id"}
    # Omitting sort_aliases serializes an explicit null.
    plain = _optimade_config(render_optimade_table(context, base_url="/optimade/v1", columns=["nsites"]).html)
    assert plain["sort_aliases"] is None
    for invalid in ([("rank", "id")], {"rank": ""}, {"": "id"}, {"rank": 1}, "id"):
        with pytest.raises(OptimadeTableProtocolError):
            render_optimade_table(context, base_url="/optimade/v1", columns=["nsites"], sort_aliases=invalid)


def test_optimade_summary_is_off_by_default_and_normalizes_when_enabled() -> None:
    context = WidgetContext(
        route="index",
        render_mode="serve",
        widget_id="table",
        query={},
        postvars={},
        page={"relbaseurl": "."},
        source_path=Path("index.md"),
        url_for=lambda route: route,
        absolute_url_for=lambda route: route,
    )
    columns = [
        {"key": "nsites", "label": "Sites"},
        {"key": "energy", "format": {"name": "number", "digits": 2, "scale": 1, "suffix": " eV"}},
    ]
    # Off: no summary element and an explicit null summary key.
    off = render_optimade_table(context, base_url="/optimade/v1", columns=columns)
    assert "data-httk-serve-optimade-summary" not in off.html
    assert _optimade_config(off.html)["summary"] is None

    # True: element present and defaults derived entirely from the columns.
    enabled = render_optimade_table(context, base_url="/optimade/v1", columns=columns, summary=True)
    assert "data-httk-serve-optimade-summary" in enabled.html
    assert _optimade_config(enabled.html)["summary"] == {
        "noun": "entries",
        "fields": {
            "nsites": {"label": "Sites", "format": None, "values": None},
            "energy": {"label": "energy", "format": {"name": "number", "digits": 2, "scale": 1.0, "suffix": " eV"}, "values": None},
        },
    }

    # Mapping: overlay replaces label/format and adds values; a filter-only field is added.
    mapped = render_optimade_table(
        context,
        base_url="/optimade/v1",
        columns=columns,
        summary={
            "noun": "screened entries",
            "fields": {
                "nsites": {"label": "Number of sites"},
                "_amdb_collinearity": {"label": "Collinearity", "values": {"collinear": "Collinear"}},
                "energy": {"format": {"name": "number", "digits": 0, "scale": 100, "suffix": " %"}},
            },
        },
    )
    assert _optimade_config(mapped.html)["summary"] == {
        "noun": "screened entries",
        "fields": {
            "nsites": {"label": "Number of sites", "format": None, "values": None},
            "energy": {"label": "energy", "format": {"name": "number", "digits": 0, "scale": 100.0, "suffix": " %"}, "values": None},
            "_amdb_collinearity": {"label": "Collinearity", "format": None, "values": {"collinear": "Collinear"}},
        },
    }

    for invalid in (
        {"unknown": 1},
        {"fields": {"nsites": {"bad": 1}}},
        {"fields": {"nsites": {"values": {"x": 1}}}},
        {"fields": {"nsites": {"values": {f"k{n}": "v" for n in range(65)}}}},
        {"fields": {f"f{n}": {} for n in range(65)}},
        "on",
    ):
        with pytest.raises(OptimadeTableProtocolError):
            render_optimade_table(context, base_url="/optimade/v1", columns=columns, summary=invalid)


def test_optimade_advanced_filter_disclosure_is_off_by_default_and_validated() -> None:
    context = WidgetContext(
        route="index",
        render_mode="serve",
        widget_id="table",
        query={},
        postvars={},
        page={"relbaseurl": "."},
        source_path=Path("index.md"),
        url_for=lambda route: route,
        absolute_url_for=lambda route: route,
    )
    # Off: explicit null key and no disclosure element (and no filter_query is fine).
    off = render_optimade_table(context, base_url="/optimade/v1", columns=["nsites"])
    assert "data-httk-serve-optimade-advanced" not in off.html
    assert _optimade_config(off.html)["advanced_filter"] is None

    # True: defaults, disclosure present, GET form posts under filter_query.
    enabled = render_optimade_table(
        context, base_url="/optimade/v1", columns=["nsites"], filter_query="filter", advanced_filter=True
    )
    assert _optimade_config(enabled.html)["advanced_filter"] == {
        "label": "Advanced OPTIMADE filter",
        "help_url": None,
    }
    assert "data-httk-serve-optimade-advanced" in enabled.html
    assert "data-httk-serve-optimade-advanced-filter" in enabled.html
    assert "<summary>Advanced OPTIMADE filter</summary>" in enabled.html
    assert '<form method="get"' in enabled.html
    assert 'name="filter"' in enabled.html
    # The submit marker (filter_query + "_advanced") lets the browser reopen the disclosure.
    assert '<input type="hidden" name="filter_advanced" value="1">' in enabled.html
    assert "Available fields" not in enabled.html

    # Mapping: custom label and a site-relative help link.
    mapped = render_optimade_table(
        context,
        base_url="/optimade/v1",
        columns=["nsites"],
        filter_query="q",
        advanced_filter={"label": "Custom filter", "help_url": "/fields"},
    )
    assert _optimade_config(mapped.html)["advanced_filter"] == {"label": "Custom filter", "help_url": "/fields"}
    assert '<input type="hidden" name="q_advanced" value="1">' in mapped.html
    assert "<summary>Custom filter</summary>" in mapped.html
    assert 'href="/fields" target="_blank" rel="noopener noreferrer"' in mapped.html
    assert 'name="q"' in mapped.html

    # A disclosure without a filter_query has no form parameter name and is rejected.
    with pytest.raises(OptimadeTableProtocolError):
        render_optimade_table(context, base_url="/optimade/v1", columns=["nsites"], advanced_filter=True)
    for invalid in (
        {"unknown": 1},
        {"help_url": "javascript:alert(1)"},
        {"help_url": "//evil.example"},
        {"help_url": "https://user:pass@host/fields"},
        {"help_url": 5},
        {"label": ""},
        "on",
    ):
        with pytest.raises(OptimadeTableProtocolError):
            render_optimade_table(
                context, base_url="/optimade/v1", columns=["nsites"], filter_query="filter", advanced_filter=invalid
            )


def test_site_local_declared_asset_is_served_only_after_its_page_renders(tmp_path: Path) -> None:
    src = _src(tmp_path, '{{ widget("site.asset") }}')
    (src / "widgets" / "asset.py").write_text(
        "from httk.serve.web.widgets import WidgetAsset, WidgetRenderResult\n"
        "def render(context):\n"
        "    return WidgetRenderResult('<p>asset</p>', assets=(WidgetAsset('site/asset.css', b'.asset{}', 'text/css'),))\n",
        encoding="utf-8",
    )
    app = create_asgi_app(src)
    other_app = create_asgi_app(_src(tmp_path / "other", "other"))

    with TestClient(app) as client, TestClient(other_app) as other_client:
        assert client.get("/_httk/serve/assets/site/asset.css").status_code == 404
        assert client.get("/").status_code == 200
        served = client.get("/_httk/serve/assets/site/asset.css")
        assert served.status_code == 200
        assert served.headers["content-type"].startswith("text/css")
        assert served.headers["x-content-type-options"] == "nosniff"
        assert client.post("/_httk/serve/assets/site/asset.css").status_code == 405
        assert other_client.get("/_httk/serve/assets/site/asset.css").status_code == 404
        assert client.get("/_httk/serve/assets/../widgets/asset.py").status_code == 404
        assert client.get("/_httk/serve/assets/%2e%2e/widgets/asset.py").status_code == 404


def test_failed_page_asset_registration_is_atomic(tmp_path: Path) -> None:
    src = _src(tmp_path, '{{ widget("site.seed") }}')
    (src / "content" / "fail.md").write_text(
        '{{ widget("site.new") }}\n\n{{ widget("site.conflict") }}', encoding="utf-8"
    )
    (src / "content" / "valid.md").write_text('{{ widget("site.new") }}', encoding="utf-8")
    declarations = {
        "seed": ("shared.css", b".shared{}"),
        "new": ("new.css", b".new{}"),
        "conflict": ("shared.css", b".changed{}"),
    }
    for name, (path, content) in declarations.items():
        (src / "widgets" / f"{name}.py").write_text(
            "from httk.serve.web.widgets import WidgetAsset, WidgetRenderResult\n"
            f"def render(context):\n    return WidgetRenderResult('<p>{name}</p>', "
            f"assets=(WidgetAsset({path!r}, {content!r}, 'text/css'),))\n",
            encoding="utf-8",
        )
    app = create_asgi_app(src)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/fail").status_code == 500
        assert client.get("/_httk/serve/assets/new.css").status_code == 404
        assert client.get("/valid").status_code == 200
        assert client.get("/_httk/serve/assets/new.css").status_code == 200


def test_static_publishing_writes_only_used_assets_and_rejects_static_collision(tmp_path: Path) -> None:
    src = _src(tmp_path, '{{ widget("optimade_table", base_url="/optimade/v1", columns=["nsites"]) }}')
    (src / "content" / "guide").mkdir()
    (src / "content" / "guide" / "index.md").write_text(
        '{{ widget("optimade_table", base_url="/optimade/v1", columns=["nsites"]) }}', encoding="utf-8"
    )
    report = publish(src, tmp_path / "public", "https://example.test")
    assets = tmp_path / "public" / "_httk" / "serve" / "assets"
    assert (assets / "serve-optimade-table.css").exists()
    assert (assets / "serve-optimade-table.mjs").exists()
    assert (assets / "serve-optimade-table-protocol.mjs").exists()
    assert len([path for path in report.written_files if path.parent == assets]) == 3
    nested_path = tmp_path / "public" / "guide" / "index.html"
    nested = nested_path.read_text(encoding="utf-8")
    assert 'src="../_httk/serve/assets/serve-optimade-table.mjs"' in nested
    asset_urls = re.findall(r'(?:href|src)="([^"]*_httk/serve/assets/[^"]+)"', nested)
    assert asset_urls
    assert all((nested_path.parent / asset_url).resolve().is_file() for asset_url in asset_urls)


@pytest.mark.parametrize(
    ("collision_path", "directory"),
    [
        (Path("_httk/serve/assets/serve-optimade-table.css"), False),
        (Path("_httk/serve/assets/serve-optimade-table.css"), True),
        (Path("_httk/serve/assets"), False),
    ],
)
def test_static_asset_collision_preflight_writes_nothing(tmp_path: Path, collision_path: Path, directory: bool) -> None:
    src = _src(tmp_path, '{{ widget("optimade_table", base_url="/optimade/v1", columns=["nsites"]) }}')
    collision = src / "static" / collision_path
    if directory:
        collision.mkdir(parents=True)
    else:
        collision.parent.mkdir(parents=True, exist_ok=True)
        collision.write_text("collision", encoding="utf-8")
    (src / "static" / "other.txt").write_text("other", encoding="utf-8")
    out = tmp_path / "public"
    out.mkdir()
    sentinel = out / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="collides with site static output"):
        publish(src, out, "https://example.test")

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert sorted(path.name for path in out.iterdir()) == ["sentinel.txt"]


def test_static_publish_ignores_registered_but_unused_asset(tmp_path: Path) -> None:
    src = _src(tmp_path, "plain page")
    config = SiteConfig.from_srcdir(src, baseurl="https://example.test")
    with SiteEngine(config) as engine:
        engine.widget_assets.register(WidgetAsset("unused.css", b".unused{}", "text/css"))
        report = publish_site(engine=engine, outdir=tmp_path / "public")
    unused = tmp_path / "public" / "_httk" / "serve" / "assets" / "unused.css"
    assert not unused.exists()
    assert unused not in report.written_files


def test_optimade_table_shell_is_safe_and_interactive_in_both_modes(tmp_path: Path) -> None:
    src = _src(
        tmp_path,
        '{{ widget("optimade_table", base_url="/optimade/v1", columns=[{"key": "nsites", "label": "Count"}], caption="\\u003c/script><x>", filter="nsites >= 1", detail_route="details", detail_column="nsites") }}',
    )
    app = create_asgi_app(src)
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert 'data-httk-serve-optimade-table="1"' in response.text
        assert 'aria-busy="true"' in response.text
        assert "\\u003c/script\\u003e" in response.text
        assert _optimade_config(response.text)["filter"] == "nsites >= 1"
        assert client.get("/_httk/serve/assets/serve-optimade-table.mjs").status_code == 200
    publish(src, tmp_path / "public", "https://example.test")
    published = (tmp_path / "public" / "index.html").read_text(encoding="utf-8")
    assert "serve-optimade-table.mjs" in published
    assert "data-httk-serve-optimade-next disabled" in published


def test_optimade_table_canonicalizes_origins_and_detail_urls_by_page(tmp_path: Path) -> None:
    src = _src(tmp_path, "home")
    (src / "content" / "guide").mkdir()
    (src / "content" / "guide" / "index.md").write_text(
        '{{ widget("optimade_table", base_url="/optimade/v1", columns=["nsites"], allowed_origins=["HTTPS://EXAMPLE.TEST:443"], detail_route="details", detail_column="nsites") }}',
        encoding="utf-8",
    )
    (src / "content" / "details.md").write_text("details", encoding="utf-8")
    app = create_asgi_app(src, compatibility_mode=True)
    with TestClient(app) as client:
        served = client.get("/guide/index")
    served_config = _optimade_config(served.text)
    assert served_config["allowed_origins"] == ["https://example.test"]
    assert served_config["detail_route"] == "../details"

    publish(src, tmp_path / "public", "https://example.test", compatibility_mode=True)
    published = (tmp_path / "public" / "guide" / "index.html").read_text(encoding="utf-8")
    assert _optimade_config(published)["detail_route"] == "../details.html"


@pytest.mark.parametrize(
    "invocation",
    [
        '{{ widget("optimade_table", base_url="ftp://example.test", columns=["nsites"]) }}',
        '{{ widget("optimade_table", base_url="https://example.test/v1?x=1", columns=["nsites"]) }}',
        '{{ widget("optimade_table", base_url="/optimade path", columns=["nsites"]) }}',
        '{{ widget("optimade_table", base_url="https:///v1", columns=["nsites"]) }}',
        '{{ widget("optimade_table", base_url="http://:80/v1", columns=["nsites"]) }}',
        '{{ widget("optimade_table", base_url="https://example.test:abc/v1", columns=["nsites"]) }}',
        '{{ widget("optimade_table", base_url="https://example.test:65536/v1", columns=["nsites"]) }}',
        '{{ widget("optimade_table", base_url="/optimade", columns=[]) }}',
        '{{ widget("optimade_table", base_url="/optimade", columns=["nsites"], detail_route="details") }}',
        '{{ widget("optimade_table", base_url="/optimade", columns=["nsites"], filter="not valid %%") }}',
        '{{ widget("optimade_table", base_url="/optimade", columns=["nsites"], allowed_origins=["HTTPS://EXAMPLE.TEST:443", "https://example.test"]) }}',
        '{{ widget("optimade_table", base_url="/optimade", columns=["nsites"], allowed_origins=["https://faß.de"]) }}',
    ],
)
def test_optimade_table_invalid_props_are_source_aware(tmp_path: Path, invocation: str) -> None:
    app = create_asgi_app(_src(tmp_path, invocation))
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 500
    assert "Widget rendering error" in response.text
    assert "widget=optimade_table" in response.text


def _optimade_config(html: str) -> dict[str, object]:
    match = re.search(r'<script id="[^"]+" type="application/json">(.*?)</script>', html)
    assert match is not None
    return json.loads(match.group(1))
