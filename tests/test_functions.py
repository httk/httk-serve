from pathlib import Path

from starlette.testclient import TestClient

from httk.web.api import create_asgi_app


def _make_src(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    (src / "content").mkdir(parents=True)
    (src / "static").mkdir(parents=True)
    (src / "templates").mkdir(parents=True)
    (src / "functions").mkdir(parents=True)
    return src


def test_function_injection_executes_with_required_query_arg(tmp_path: Path) -> None:
    src = _make_src(tmp_path)

    (src / "functions" / "hello.py").write_text(
        """def execute(name, global_data, **kwargs):
    return name.upper()
""",
        encoding="utf-8",
    )

    (src / "templates" / "default.html.j2").write_text("{{ greeting }}|{{ content }}", encoding="utf-8")
    (src / "templates" / "base_default.html.j2").write_text("<html><body>{{ content }}</body></html>", encoding="utf-8")
    (src / "templates" / "greeting_fragment.html.j2").write_text("Hello {{ result }}!", encoding="utf-8")

    (src / "content" / "index.md").write_text(
        """---
template: default
base_template: base_default
greeting-function: hello:name:greeting_fragment
---

Body text
""",
        encoding="utf-8",
    )

    app = create_asgi_app(src)
    with TestClient(app) as client:
        response = client.get("/?name=Rick")

    assert response.status_code == 200
    assert "Hello RICK!" in response.text


def test_function_injection_skips_when_query_constraints_not_met(tmp_path: Path) -> None:
    src = _make_src(tmp_path)

    (src / "functions" / "hello.py").write_text(
        """def execute(name='x', global_data=None, **kwargs):
    return name.upper()
""",
        encoding="utf-8",
    )

    (src / "templates" / "default.html.j2").write_text("{{ greeting }}|{{ content }}", encoding="utf-8")
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")
    (src / "templates" / "greeting_fragment.html.j2").write_text("Hello {{ result }}!", encoding="utf-8")

    (src / "content" / "index.md").write_text(
        """---
template: default
greeting-function: hello:name,!blocked:greeting_fragment
---

Body text
""",
        encoding="utf-8",
    )

    app = create_asgi_app(src)
    with TestClient(app) as client:
        missing_required = client.get("/")
        blocked_present = client.get("/?name=Rick&blocked=yes")

    assert missing_required.status_code == 200
    assert blocked_present.status_code == 200

    assert "Hello" not in missing_required.text
    assert "Hello" not in blocked_present.text


def test_function_injection_accepts_post_form_params(tmp_path: Path) -> None:
    src = _make_src(tmp_path)

    (src / "functions" / "hello.py").write_text(
        """def execute(name, global_data, **kwargs):
    return f"FROM-POST:{name}"
""",
        encoding="utf-8",
    )

    (src / "templates" / "default.html.j2").write_text("{{ greeting }}|{{ content }}", encoding="utf-8")
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")
    (src / "templates" / "greeting_fragment.html.j2").write_text("Hello {{ result }}!", encoding="utf-8")

    (src / "content" / "index.md").write_text(
        """---
template: default
greeting-function: hello:name:greeting_fragment
---

Body text
""",
        encoding="utf-8",
    )

    app = create_asgi_app(src)
    with TestClient(app) as client:
        response = client.post("/", data={"name": "Rick"})

    assert response.status_code == 200
    assert "Hello FROM-POST:Rick!" in response.text


def test_function_injection_accepts_post_json_params(tmp_path: Path) -> None:
    src = _make_src(tmp_path)

    (src / "functions" / "hello.py").write_text(
        """def execute(name, global_data, **kwargs):
    return f"FROM-JSON:{name}"
""",
        encoding="utf-8",
    )

    (src / "templates" / "default.html.j2").write_text("{{ greeting }}|{{ content }}", encoding="utf-8")
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")
    (src / "templates" / "greeting_fragment.html.j2").write_text("Hello {{ result }}!", encoding="utf-8")

    (src / "content" / "index.md").write_text(
        """---
template: default
greeting-function: hello:name:greeting_fragment
---

Body text
""",
        encoding="utf-8",
    )

    app = create_asgi_app(src)
    with TestClient(app) as client:
        response = client.post("/", json={"name": "Rick"})

    assert response.status_code == 200
    assert "Hello FROM-JSON:Rick!" in response.text


def test_function_injection_accepts_post_multipart_and_maps_upload_filename(tmp_path: Path) -> None:
    src = _make_src(tmp_path)

    (src / "functions" / "hello.py").write_text(
        """def execute(name, upload, global_data, **kwargs):
    return f"{name}:{upload}"
""",
        encoding="utf-8",
    )

    (src / "templates" / "default.html.j2").write_text("{{ greeting }}|{{ content }}", encoding="utf-8")
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")
    (src / "templates" / "greeting_fragment.html.j2").write_text("Hello {{ result }}!", encoding="utf-8")

    (src / "content" / "index.md").write_text(
        """---
template: default
greeting-function: hello:name,upload:greeting_fragment
---

Body text
""",
        encoding="utf-8",
    )

    app = create_asgi_app(src)
    with TestClient(app) as client:
        response = client.post("/", data={"name": "Rick"}, files={"upload": ("example.txt", b"abc", "text/plain")})

    assert response.status_code == 200
    assert "Hello Rick:example.txt!" in response.text


def test_invalid_json_post_does_not_crash_and_skips_required_function(tmp_path: Path) -> None:
    src = _make_src(tmp_path)

    (src / "functions" / "hello.py").write_text(
        """def execute(name, global_data, **kwargs):
    return name.upper()
""",
        encoding="utf-8",
    )

    (src / "templates" / "default.html.j2").write_text("{{ greeting }}|{{ content }}", encoding="utf-8")
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")
    (src / "templates" / "greeting_fragment.html.j2").write_text("Hello {{ result }}!", encoding="utf-8")

    (src / "content" / "index.md").write_text(
        """---
template: default
greeting-function: hello:name:greeting_fragment
---

Body text
""",
        encoding="utf-8",
    )

    app = create_asgi_app(src)
    with TestClient(app) as client:
        response = client.post("/", content="{bad json", headers={"content-type": "application/json"})

    assert response.status_code == 200
    assert "Hello" not in response.text


def test_oversized_json_post_returns_413(tmp_path: Path) -> None:
    src = _make_src(tmp_path)

    (src / "templates" / "default.html.j2").write_text("{{ content }}", encoding="utf-8")
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")
    (src / "content" / "index.md").write_text("---\ntemplate: default\n---\n\nBody text", encoding="utf-8")

    app = create_asgi_app(src)
    payload = "x" * 1_100_000
    with TestClient(app) as client:
        response = client.post("/", content=payload, headers={"content-type": "application/json"})

    assert response.status_code == 413


def test_invalid_function_spec_returns_controlled_500(tmp_path: Path) -> None:
    src = _make_src(tmp_path)

    (src / "templates" / "default.html.j2").write_text("{{ content }}", encoding="utf-8")
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")

    (src / "content" / "index.md").write_text(
        """---
template: default
broken-function: not:enough
---

Body text
""",
        encoding="utf-8",
    )

    app = create_asgi_app(src)
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 500
    assert "Failed processing function metadata" in response.text


def test_missing_function_module_returns_controlled_500(tmp_path: Path) -> None:
    src = _make_src(tmp_path)

    (src / "templates" / "default.html.j2").write_text("{{ content }}", encoding="utf-8")
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")

    (src / "content" / "index.md").write_text(
        """---
template: default
greeting-function: does_not_exist:name:greeting_fragment
---

Body text
""",
        encoding="utf-8",
    )

    app = create_asgi_app(src)
    with TestClient(app) as client:
        response = client.get("/?name=Rick")

    assert response.status_code == 500
    assert "Failed processing function metadata" in response.text


def test_function_module_can_import_sibling_helper(tmp_path: Path) -> None:
    src = _make_src(tmp_path)

    (src / "functions" / "helper.py").write_text(
        """def normalize(name):
    return name.strip().upper()
""",
        encoding="utf-8",
    )
    (src / "functions" / "hello.py").write_text(
        """from helper import normalize

def execute(name, global_data, **kwargs):
    return normalize(name)
""",
        encoding="utf-8",
    )

    (src / "templates" / "default.html.j2").write_text("{{ greeting }}|{{ content }}", encoding="utf-8")
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")
    (src / "templates" / "greeting_fragment.html.j2").write_text("Hello {{ result }}!", encoding="utf-8")

    (src / "content" / "index.md").write_text(
        """---
template: default
greeting-function: hello:name:greeting_fragment
---

Body text
""",
        encoding="utf-8",
    )

    app = create_asgi_app(src)
    with TestClient(app) as client:
        response = client.get("/?name=Rick")

    assert response.status_code == 200
    assert "Hello RICK!" in response.text
