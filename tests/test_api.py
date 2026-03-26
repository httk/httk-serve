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
