import json
import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from httk.serve.web.api import create_asgi_app, publish
from httk.serve.web.engine.site_engine import SiteEngine
from httk.serve.web.model.config import SiteConfig
from httk.serve.web.publishing.static import publish_site
from httk.serve.web.widgets import MAX_WIDGET_ASSET_BYTES, WidgetAsset, WidgetRenderResult
from httk.serve.web.widgets.assets import WidgetAssetRegistry


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
