from pathlib import Path

from httk.serve.web.engine.discovery import normalize_route
from httk.serve.web.engine.site_engine import SiteEngine
from httk.serve.web.model.config import SiteConfig


def test_normalize_route_defaults_to_index() -> None:
    assert normalize_route("") == "index"
    assert normalize_route("/") == "index"


def test_engine_resolves_content_without_extension(tmp_path: Path) -> None:
    src = tmp_path / "src"
    content = src / "content"
    static = src / "static"
    templates = src / "templates"
    content.mkdir(parents=True)
    static.mkdir(parents=True)
    templates.mkdir(parents=True)

    (content / "index.md").write_text("hello", encoding="utf-8")
    (static / "site.css").write_text("body{}", encoding="utf-8")

    engine = SiteEngine(SiteConfig.from_srcdir(src))

    content_resolved = engine.resolve("index")
    static_resolved = engine.resolve("site.css")

    assert content_resolved.kind == "content"
    assert static_resolved.kind == "static"


def test_engine_returns_missing_for_unknown_route(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "content").mkdir(parents=True)
    (src / "static").mkdir(parents=True)
    (src / "templates").mkdir(parents=True)

    engine = SiteEngine(SiteConfig.from_srcdir(src))
    resolved = engine.resolve("does-not-exist")
    assert resolved.kind == "missing"
