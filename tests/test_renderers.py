from pathlib import Path

from httk.web.engine.site_engine import SiteEngine
from httk.web.model.config import SiteConfig


def _make_src(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    (src / "content").mkdir(parents=True)
    (src / "static").mkdir(parents=True)
    (src / "templates").mkdir(parents=True)
    return src


def test_markdown_frontmatter_metadata(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    (src / "content" / "index.md").write_text(
        """---
title: Test title
menuitems-list:
  - index
  - about
---

# Hello
""",
        encoding="utf-8",
    )

    engine = SiteEngine(SiteConfig.from_srcdir(src))
    page = engine.render("index")

    assert page.status_code == 200
    assert "<h1>Hello</h1>" in page.body.decode("utf-8")
    assert page.metadata["title"] == "Test title"
    assert page.metadata["menuitems"] == ["index", "about"]


def test_rst_field_list_metadata(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    (src / "content" / "index.rst").write_text(
        """:title: Hello world
:menuitems-list:
  - index
  - contact

Page Title
==========
""",
        encoding="utf-8",
    )

    engine = SiteEngine(SiteConfig.from_srcdir(src))
    page = engine.render("index")

    assert page.status_code == 200
    assert "Page Title" in page.body.decode("utf-8")
    assert page.metadata["title"] == "Hello world"
    assert page.metadata["menuitems"] == ["index", "contact"]


def test_httkweb_compat_uses_frontmatter_and_rst_body(tmp_path: Path) -> None:
    src = _make_src(tmp_path)
    (src / "content" / "index.httkweb").write_text(
        """---
Title: Front page
Template: default
---

Front page
==========
""",
        encoding="utf-8",
    )

    engine = SiteEngine(SiteConfig.from_srcdir(src))
    page = engine.render("index")

    assert page.status_code == 200
    html = page.body.decode("utf-8")
    assert "Front page" in html
    assert page.metadata["Title"] == "Front page"
