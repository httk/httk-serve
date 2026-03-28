from pathlib import Path

from starlette.testclient import TestClient

from httk.web.api import create_asgi_app, publish


def test_create_asgi_app_serves_content(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "content").mkdir(parents=True)
    (src / "static").mkdir(parents=True)
    (src / "templates").mkdir(parents=True)
    (src / "content" / "index.md").write_text("hello", encoding="utf-8")

    app = create_asgi_app(src)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "hello" in response.text


def test_publish_writes_html_output(tmp_path: Path) -> None:
    src = tmp_path / "src"
    out = tmp_path / "public"
    (src / "content").mkdir(parents=True)
    (src / "static").mkdir(parents=True)
    (src / "templates").mkdir(parents=True)
    (src / "content" / "index.md").write_text("hello", encoding="utf-8")

    report = publish(src, out, "http://localhost/")

    index_out = out / "index.html"
    assert index_out.exists()
    assert any(path == index_out for path in report.written_files)


def test_publish_uses_extensionless_links_by_default_for_modern_mode(tmp_path: Path) -> None:
    src = tmp_path / "src"
    out = tmp_path / "public"
    (src / "content").mkdir(parents=True)
    (src / "static").mkdir(parents=True)
    (src / "templates").mkdir(parents=True)

    (src / "content" / "about.md").write_text("---\ntitle: About\n---\n\nabout", encoding="utf-8")
    (src / "content" / "index.md").write_text("---\ntemplate: default\n---\n\nhome", encoding="utf-8")
    (src / "templates" / "default.html.j2").write_text(
        "<a href='{{ pages(\"about\", \"relurl\") }}'>about</a>|{{ page.relurl }}",
        encoding="utf-8",
    )
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")

    publish(src, out, "http://localhost/")

    rendered = (out / "index.html").read_text(encoding="utf-8")
    assert "href='about'" in rendered
    assert "|index" in rendered


def test_publish_uses_html_suffix_links_by_default_for_compatibility_mode(tmp_path: Path) -> None:
    src = tmp_path / "src"
    out = tmp_path / "public"
    (src / "content").mkdir(parents=True)
    (src / "static").mkdir(parents=True)
    (src / "templates").mkdir(parents=True)

    (src / "content" / "about.md").write_text("---\ntitle: About\n---\n\nabout", encoding="utf-8")
    (src / "content" / "index.md").write_text("---\ntemplate: default\n---\n\nhome", encoding="utf-8")
    (src / "templates" / "default.html.j2").write_text(
        "<a href='{{ pages(\"about\", \"relurl\") }}'>about</a>|{{ page.relurl }}",
        encoding="utf-8",
    )
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")

    publish(src, out, "http://localhost/", compatibility_mode=True)

    rendered = (out / "index.html").read_text(encoding="utf-8")
    assert "href='about.html'" in rendered
    assert "|index.html" in rendered


def test_publish_can_add_html_suffix_to_links(tmp_path: Path) -> None:
    src = tmp_path / "src"
    out = tmp_path / "public"
    (src / "content").mkdir(parents=True)
    (src / "static").mkdir(parents=True)
    (src / "templates").mkdir(parents=True)

    (src / "content" / "about.md").write_text("---\ntitle: About\n---\n\nabout", encoding="utf-8")
    (src / "content" / "index.md").write_text("---\ntemplate: default\n---\n\nhome", encoding="utf-8")
    (src / "templates" / "default.html.j2").write_text(
        "<a href='{{ pages(\"about\", \"relurl\") }}'>about</a>|{{ page.relurl }}",
        encoding="utf-8",
    )
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")

    publish(src, out, "http://localhost/", use_urls_without_ext=False)

    rendered = (out / "index.html").read_text(encoding="utf-8")
    assert "href='about.html'" in rendered
    assert "|index.html" in rendered


def test_publish_split_hosting_routes_links_by_page_dynamism(tmp_path: Path) -> None:
    src = tmp_path / "src"
    out = tmp_path / "public"
    (src / "content").mkdir(parents=True)
    (src / "static").mkdir(parents=True)
    (src / "templates").mkdir(parents=True)
    (src / "functions").mkdir(parents=True)

    (src / "functions" / "dynamic.py").write_text(
        "def execute(global_data=None, **kwargs):\n"
        "    return 'ok'\n",
        encoding="utf-8",
    )
    (src / "templates" / "dynamic_fragment.html.j2").write_text("{{ result }}", encoding="utf-8")

    (src / "content" / "about.md").write_text("---\ntitle: About\n---\n\nabout", encoding="utf-8")
    (src / "content" / "search.md").write_text(
        "---\ntitle: Search\nresult-function: dynamic::dynamic_fragment\n---\n\nsearch",
        encoding="utf-8",
    )
    (src / "content" / "index.md").write_text(
        "---\ntemplate: default\n---\n\n[About](about)\n[Search](search)\n",
        encoding="utf-8",
    )
    (src / "templates" / "default.html.j2").write_text(
        (
            "about={{ pages(\"about\", \"relurl\") }}"
            "|search={{ pages(\"search\", \"relurl\") }}"
            "|abs={{ page.absurl }}"
            "|{{ content }}"
        ),
        encoding="utf-8",
    )
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")

    publish(
        src,
        out,
        "https://dynamic.example",
        host_static="https://static.example",
        use_urls_without_ext=False,
    )

    rendered = (out / "index.html").read_text(encoding="utf-8")
    assert "about=about.html" in rendered
    assert "search=https://dynamic.example/search.html" in rendered
    assert "abs=https://static.example/index.html" in rendered
    assert 'href="about.html"' in rendered
    assert 'href="https://dynamic.example/search.html"' in rendered


def test_publish_split_hosting_frontmatter_overrides(tmp_path: Path) -> None:
    src = tmp_path / "src"
    out = tmp_path / "public"
    (src / "content").mkdir(parents=True)
    (src / "static").mkdir(parents=True)
    (src / "templates").mkdir(parents=True)
    (src / "functions").mkdir(parents=True)

    (src / "functions" / "dynamic.py").write_text(
        "def execute(global_data=None, **kwargs):\n"
        "    return 'ok'\n",
        encoding="utf-8",
    )
    (src / "templates" / "dynamic_fragment.html.j2").write_text("{{ result }}", encoding="utf-8")

    (src / "content" / "precomputed.md").write_text(
        (
            "---\n"
            "title: Precomputed\n"
            "hosting: static\n"
            "result-function: dynamic::dynamic_fragment\n"
            "---\n\nprecomputed\n"
        ),
        encoding="utf-8",
    )
    (src / "content" / "docs.md").write_text(
        "---\ntitle: Docs\nhosting: dynamic\n---\n\ndocs",
        encoding="utf-8",
    )
    (src / "content" / "index.md").write_text("---\ntemplate: default\n---\n\nhome", encoding="utf-8")
    (src / "templates" / "default.html.j2").write_text(
        "pre={{ pages(\"precomputed\", \"relurl\") }}|docs={{ pages(\"docs\", \"relurl\") }}|abs={{ page.absurl }}|{{ content }}",
        encoding="utf-8",
    )
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")

    publish(
        src,
        out,
        "https://dynamic.example",
        host_static="https://static.example",
        use_urls_without_ext=False,
    )

    index_rendered = (out / "index.html").read_text(encoding="utf-8")
    assert "pre=precomputed.html" in index_rendered
    assert "docs=https://dynamic.example/docs.html" in index_rendered
    assert "abs=https://static.example/index.html" in index_rendered

    docs_rendered = (out / "docs.html").read_text(encoding="utf-8")
    assert "abs=https://dynamic.example/docs.html" in docs_rendered


def test_publish_rewrites_markdown_internal_links_with_html_suffix(tmp_path: Path) -> None:
    src = tmp_path / "src"
    out = tmp_path / "public"
    (src / "content").mkdir(parents=True)
    (src / "static").mkdir(parents=True)
    (src / "templates").mkdir(parents=True)

    (src / "content" / "about.md").write_text("---\ntitle: About\n---\n\nabout", encoding="utf-8")
    (src / "content" / "guide.rst").write_text("Guide\n=====\n\nGuide body.\n", encoding="utf-8")
    (src / "content" / "index.md").write_text(
        (
            "---\ntemplate: default\n---\n\n"
            "[About](about)\n"
            "[AboutMd](about.md)\n"
            "[GuideRst](guide.rst)\n"
            "[Query](about?x=1#top)\n"
            "[External](https://example.com)\n"
        ),
        encoding="utf-8",
    )
    (src / "templates" / "default.html.j2").write_text("{{ content }}", encoding="utf-8")
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")

    publish(src, out, "http://localhost/", use_urls_without_ext=False)

    rendered = (out / "index.html").read_text(encoding="utf-8")
    assert 'href="about.html"' in rendered
    assert 'href="guide.html"' in rendered
    assert 'href="about.html?x=1#top"' in rendered
    assert 'href="https://example.com"' in rendered


def test_publish_link_rewrite_skips_data_attrs_and_script_text(tmp_path: Path) -> None:
    src = tmp_path / "src"
    out = tmp_path / "public"
    (src / "content").mkdir(parents=True)
    (src / "static").mkdir(parents=True)
    (src / "templates").mkdir(parents=True)

    (src / "content" / "about.md").write_text("---\ntitle: About\n---\n\nabout", encoding="utf-8")
    (src / "content" / "index.md").write_text("---\ntemplate: default\n---\n\nhome", encoding="utf-8")
    (src / "templates" / "default.html.j2").write_text(
        (
            "<a title='a>b' href='about'>about</a>"
            "<button onclick=\"if (x > 0) { console.log('href=about'); }\">x</button>"
            "<div data-href='about'>data</div>"
            "<script>const x = \"href='about'\";</script>"
            "<img src='about'/>"
        ),
        encoding="utf-8",
    )
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")

    publish(src, out, "http://localhost/", use_urls_without_ext=False)

    rendered = (out / "index.html").read_text(encoding="utf-8")
    assert "href='about.html'" in rendered
    assert "title='a>b'" in rendered
    assert "src='about.html'" in rendered
    assert "data-href='about'" in rendered
    assert "onclick=\"if (x > 0) { console.log('href=about'); }\"" in rendered
    assert "href='about'\";</script>" in rendered


def test_create_asgi_app_compatibility_mode_prefers_httkweb_templates(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "content").mkdir(parents=True)
    (src / "static").mkdir(parents=True)
    (src / "templates").mkdir(parents=True)

    (src / "content" / "index.md").write_text("---\ntemplate: default\n---\n\nhello", encoding="utf-8")
    (src / "templates" / "default.html.j2").write_text("modern={{ content }}", encoding="utf-8")
    (src / "templates" / "default.httkweb.html").write_text("legacy={{ content }}", encoding="utf-8")

    app = create_asgi_app(src, compatibility_mode=True)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "legacy=" in response.text
    assert "modern=" not in response.text


def test_publish_compatibility_mode_prefers_httkweb_templates(tmp_path: Path) -> None:
    src = tmp_path / "src"
    out = tmp_path / "public"
    (src / "content").mkdir(parents=True)
    (src / "static").mkdir(parents=True)
    (src / "templates").mkdir(parents=True)

    (src / "content" / "index.md").write_text("---\ntemplate: default\n---\n\nhello", encoding="utf-8")
    (src / "templates" / "default.html.j2").write_text("modern={{ content }}", encoding="utf-8")
    (src / "templates" / "default.httkweb.html").write_text("legacy={{ content }}", encoding="utf-8")

    publish(src, out, "http://localhost/", compatibility_mode=True)

    rendered = (out / "index.html").read_text(encoding="utf-8")
    assert "legacy=" in rendered
    assert "modern=" not in rendered


def test_create_asgi_app_compatibility_mode_supports_legacy_repeat(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "content").mkdir(parents=True)
    (src / "static").mkdir(parents=True)
    (src / "templates").mkdir(parents=True)

    (src / "content" / "index.md").write_text(
        "---\n"
        "template: default\n"
        "menuitems:\n"
        "  - alpha\n"
        "  - beta\n"
        "---\n\n"
        "hello",
        encoding="utf-8",
    )
    (src / "templates" / "default.httkweb.html").write_text(
        "<ul>{menuitems:repeat::<li>{{item}}</li>}</ul>{content}",
        encoding="utf-8",
    )

    app = create_asgi_app(src, compatibility_mode=True)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "<li>alpha</li>" in response.text
    assert "<li>beta</li>" in response.text
    assert "hello" in response.text


def test_create_asgi_app_compatibility_mode_supports_legacy_pages_call(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "content").mkdir(parents=True)
    (src / "static").mkdir(parents=True)
    (src / "templates").mkdir(parents=True)

    (src / "content" / "about.md").write_text("---\ntitle: About Page\n---\n\nAbout body", encoding="utf-8")
    (src / "content" / "index.md").write_text(
        "---\n"
        "template: default\n"
        "menuitems:\n"
        "  - about\n"
        "---\n\n"
        "home",
        encoding="utf-8",
    )
    (src / "templates" / "default.httkweb.html").write_text(
        "{menuitems:repeat::<a>{{pages:call:{{item}}:title}}</a>}{content}",
        encoding="utf-8",
    )

    app = create_asgi_app(src, compatibility_mode=True)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "<a>About Page</a>" in response.text


def test_create_asgi_app_compatibility_mode_loads_config_frontmatter(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "content").mkdir(parents=True)
    (src / "static").mkdir(parents=True)
    (src / "templates").mkdir(parents=True)

    (src / "config.httkweb").write_text("---\nmenuitems-list: index, contact, bare\n---\n", encoding="utf-8")
    (src / "content" / "index.md").write_text("---\ntemplate: default\n---\n\nhello", encoding="utf-8")
    (src / "templates" / "default.httkweb.html").write_text(
        "<ul>{menuitems:repeat::<li>{{item}}</li>}</ul>{content}",
        encoding="utf-8",
    )

    app = create_asgi_app(src, compatibility_mode=True)
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "<li>index</li>" in response.text
    assert "<li>contact</li>" in response.text
    assert "<li>bare</li>" in response.text


def test_create_asgi_app_compatibility_mode_runs_init_function(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "content").mkdir(parents=True)
    (src / "static").mkdir(parents=True)
    (src / "templates").mkdir(parents=True)
    (src / "functions").mkdir(parents=True)

    (src / "functions" / "init.py").write_text(
        "def execute(global_data, **kwargs):\n"
        "    global_data['website_name'] = 'Legacy Site'\n",
        encoding="utf-8",
    )
    (src / "content" / "index.md").write_text("---\ntemplate: default\n---\n\nhello", encoding="utf-8")
    (src / "templates" / "default.httkweb.html").write_text("{website_name}:{content}", encoding="utf-8")

    app = create_asgi_app(src, compatibility_mode=True)
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Legacy Site:" in response.text
