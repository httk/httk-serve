from pathlib import Path

from httk.web.engine.site_engine import SiteEngine
from httk.web.model.config import SiteConfig


def _make_src(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    (src / "content").mkdir(parents=True)
    (src / "static").mkdir(parents=True)
    (src / "templates").mkdir(parents=True)
    return src


def test_jinja_template_wraps_rendered_content(tmp_path: Path) -> None:
    src = _make_src(tmp_path)

    (src / "templates" / "default.html.j2").write_text("<main>{{ content }}</main>", encoding="utf-8")
    (src / "templates" / "base_default.html.j2").write_text("<html><body>{{ content }}</body></html>", encoding="utf-8")

    (src / "content" / "index.md").write_text(
        """---
template: default
base_template: base_default
---

# Hello
""",
        encoding="utf-8",
    )

    engine = SiteEngine(SiteConfig.from_srcdir(src))
    result = engine.render("index")
    html = result.body.decode("utf-8")

    assert "<html><body>" in html
    assert "<main>" in html
    assert "<h1>Hello</h1>" in html


def test_template_helpers_first_value_and_listdir(tmp_path: Path) -> None:
    src = _make_src(tmp_path)

    (src / "content" / "posts").mkdir(parents=True)
    (src / "content" / "posts" / "a.md").write_text("A", encoding="utf-8")
    (src / "content" / "posts" / "b.md").write_text("B", encoding="utf-8")

    (src / "templates" / "default.html.j2").write_text(
        "{{ first_value('', 'fallback') }}|{% for p in listdir('posts', '.md') %}{{ p }};{% endfor %}",
        encoding="utf-8",
    )
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")

    (src / "content" / "index.md").write_text("---\ntemplate: default\n---\n\nbody", encoding="utf-8")

    engine = SiteEngine(SiteConfig.from_srcdir(src))
    result = engine.render("index")
    html = result.body.decode("utf-8")

    assert html.startswith("fallback|")
    assert "posts/a.md" in html
    assert "posts/b.md" in html


def test_template_helper_pages_reads_other_page_metadata(tmp_path: Path) -> None:
    src = _make_src(tmp_path)

    (src / "templates" / "default.html.j2").write_text("{{ pages('about', 'title') }}", encoding="utf-8")
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")

    (src / "content" / "about.md").write_text("---\ntitle: About Page\n---\n\nhello", encoding="utf-8")
    (src / "content" / "index.md").write_text("---\ntemplate: default\n---\n\nhello", encoding="utf-8")

    engine = SiteEngine(SiteConfig.from_srcdir(src))
    result = engine.render("index")

    assert result.body.decode("utf-8") == "About Page"
