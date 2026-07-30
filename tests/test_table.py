import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from httk.web.api import create_asgi_app, publish
from httk.web.providers import ProviderContext, TablePage
from httk.web.widgets.table import TableContinuationError, TableTokenSigner


def _src(tmp_path: Path, *, content: str | None = None, provider: str | None = None) -> Path:
    src = tmp_path / "src"
    for name in ("content", "static", "templates", "functions"):
        (src / name).mkdir(parents=True)
    (src / "templates" / "default.html.j2").write_text("{{ content }}", encoding="utf-8")
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")
    (src / "content" / "index.md").write_text(
        content or '{{ widget("table", provider="materials", page_size=2) }}', encoding="utf-8"
    )
    (src / "functions" / "materials.py").write_text(provider or _provider_source(), encoding="utf-8")
    return src


def _provider_source() -> str:
    return '''from httk.web import TablePage

calls = []

def provide(context, request, **provider_args):
    calls.append((dict(context.query), request.page_size, request.cursor, dict(provider_args)))
    start = int(request.cursor or "0")
    rows = [{"name": f"{context.query.get('q', 'all')}-{number}", "value": number} for number in range(start, min(start + request.page_size, 5))]
    return TablePage.from_rows(
        rows,
        columns=["name", {"key": "value", "label": "Value", "align": "end"}],
        next_cursor=str(start + request.page_size) if start + request.page_size < 5 else None,
        previous_cursor=str(max(0, start - request.page_size)) if start else None,
        total=5,
    )
'''


def _token(html: str, direction: str = "next") -> str:
    match = re.search(rf'data-httk-table-{direction} data-token="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def _provider_module(app, name: str = "materials"):
    for path, module in app.state.engine.function_handler._module_cache.items():
        if path.stem == name:
            return module
    raise AssertionError(f"provider module {name!r} was not loaded")


def test_table_default_renderer_escapes_values_and_simple_sequences(tmp_path: Path) -> None:
    provider = '''from httk.web import TablePage

def provide(context, request, **provider_args):
    return TablePage.from_rows([{"name": "<script>alert(1)</script>", "value": ["<b>x</b>", 2]}], columns=["name", "value"])
'''
    app = create_asgi_app(_src(tmp_path, provider=provider), table_token_secret="s" * 32)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "&lt;b&gt;x&lt;/b&gt;, 2" in response.text
    assert "<script>alert(1)</script>" not in response.text


def test_table_row_template_has_the_same_context_after_pagination(tmp_path: Path) -> None:
    content = '{{ widget("table", provider="materials", page_size=1, row_template="table_row") }}'
    src = _src(tmp_path, content=content)
    (src / "templates" / "table_row.html.j2").write_text(
        '<tr><td>{{ page.relbaseurl }}|{{ query.q }}|{{ table.route }}|{{ table.widget_id }}|{{ row.name }}</td></tr>',
        encoding="utf-8",
    )
    app = create_asgi_app(src, table_token_secret="s" * 32)

    with TestClient(app) as client:
        initial = client.get("/?q=filtered")
        widget_id = re.search(r'data-widget-id="([^"]+)"', initial.text)
        assert widget_id is not None
        token = _token(initial.text)
        paged = client.post(
            "/_httk/table/page", json={"token": token, "route": "index", "widget_id": widget_id.group(1)}
        )

    assert initial.status_code == 200
    assert ".|filtered|index|" in initial.text
    assert paged.status_code == 200
    assert f".|filtered|index|{widget_id.group(1)}|filtered-1" in paged.json()["tbody"]


def test_table_paginates_only_requested_bounded_pages_and_preserves_query(tmp_path: Path) -> None:
    app = create_asgi_app(_src(tmp_path), table_token_secret="s" * 32)

    with TestClient(app) as client:
        first = client.get("/?q=needle")
        widget_id = re.search(r'data-widget-id="([^"]+)"', first.text)
        assert widget_id is not None
        next_page = client.post(
            "/_httk/table/page",
            json={"token": _token(first.text), "route": "index", "widget_id": widget_id.group(1)},
        )
        previous_page = client.post(
            "/_httk/table/page",
            json={"token": next_page.json()["previous"], "route": "index", "widget_id": widget_id.group(1)},
        )

    calls = _provider_module(app).calls
    assert first.status_code == 200
    assert next_page.status_code == 200
    assert previous_page.status_code == 200
    assert calls == [
        ({"q": "needle"}, 2, None, {}),
        ({"q": "needle"}, 2, "2", {}),
        ({"q": "needle"}, 2, "0", {}),
    ]
    assert "needle-2" in next_page.json()["tbody"]
    assert "needle-0" in previous_page.json()["tbody"]


def test_table_rejects_tampered_expired_and_cross_widget_tokens_before_provider_dispatch(tmp_path: Path) -> None:
    content = (
        '{{ widget("table", id="first", provider="materials", page_size=1) }}\n\n'
        '{{ widget("table", id="second", provider="materials", page_size=1) }}'
    )
    app = create_asgi_app(_src(tmp_path, content=content), table_token_secret="s" * 32)
    runtime = app.state.engine.table_runtime
    runtime._clock = lambda: 100

    with TestClient(app) as client:
        initial = client.get("/")
        first_token = _token(initial.text)
        tampered = f"{first_token[:-1]}{'A' if first_token[-1] != 'A' else 'B'}"
        assert (
            client.post(
                "/_httk/table/page", json={"token": tampered, "route": "index", "widget_id": "first"}
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/_httk/table/page", json={"token": first_token, "route": "other", "widget_id": "first"}
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/_httk/table/page", json={"token": first_token, "route": "index", "widget_id": "second"}
            ).status_code
            == 400
        )
        runtime._clock = lambda: 2_000
        assert (
            client.post(
                "/_httk/table/page", json={"token": first_token, "route": "index", "widget_id": "first"}
            ).status_code
            == 410
        )

    assert len(_provider_module(app).calls) == 2


def test_table_endpoint_limits_methods_content_and_provider_errors(tmp_path: Path) -> None:
    provider = '''from httk.web import TablePage

def provide(context, request, **provider_args):
    if request.cursor is not None:
        raise RuntimeError("/secret/path should never reach the browser")
    return TablePage.from_rows([{"name": "one"}], columns=["name"], next_cursor="later")
'''
    app = create_asgi_app(_src(tmp_path, provider=provider), table_token_secret="s" * 32)

    with TestClient(app) as client:
        initial = client.get("/")
        token = _token(initial.text)
        assert client.get("/_httk/table/page").status_code == 405
        assert client.get("/_httk/unrecognised").status_code == 404
        assert client.post("/_httk/table/page", content="{}").status_code == 415
        assert (
            client.post("/_httk/table/page", content="{bad", headers={"content-type": "application/json"}).status_code
            == 400
        )
        assert (
            client.post(
                "/_httk/table/page", content="x" * 70_000, headers={"content-type": "application/json"}
            ).status_code
            == 413
        )
        failed = client.post(
            "/_httk/table/page",
            json={
                "token": token,
                "route": "index",
                "widget_id": re.search(r'data-widget-id="([^"]+)"', initial.text).group(1),
            },
        )

    assert failed.status_code == 500
    assert "/secret/path" not in failed.text
    assert failed.headers["cache-control"] == "no-store"
    assert failed.headers["x-content-type-options"] == "nosniff"


def test_initial_table_provider_error_does_not_leak_author_diagnostics(tmp_path: Path) -> None:
    provider = """def provide(context, request, **provider_args):
    raise ValueError("/secret/provider/path")
"""
    app = create_asgi_app(_src(tmp_path, provider=provider), table_token_secret="s" * 32)

    with TestClient(app) as client:
        failed = client.get("/")

    assert failed.status_code == 500
    assert failed.text == "Table provider could not load this page."
    assert "/secret/provider/path" not in failed.text
    assert str(tmp_path) not in failed.text


def test_table_revision_mismatch_resets_without_dispatching_a_new_page(tmp_path: Path) -> None:
    provider = '''from httk.web import TablePage

def provide(context, request, **provider_args):
    return TablePage.from_rows(
        [{"name": "one"}], columns=["name"], next_cursor="later", revision="first" if request.cursor is None else "second"
    )
'''
    app = create_asgi_app(_src(tmp_path, provider=provider), table_token_secret="s" * 32)

    with TestClient(app) as client:
        initial = client.get("/")
        response = client.post(
            "/_httk/table/page",
            json={
                "token": _token(initial.text),
                "route": "index",
                "widget_id": re.search(r'data-widget-id="([^"]+)"', initial.text).group(1),
            },
        )

    assert response.status_code == 409
    assert "Reload the page" in response.text


@pytest.mark.parametrize(
    "provider",
    [
        "def provide(context):\n    return None\n",
        "def provide(context, request, **provider_args):\n    return {'rows': [], 'columns': []}\n",
    ],
)
def test_table_provider_signature_and_result_errors_are_controlled(tmp_path: Path, provider: str) -> None:
    app = create_asgi_app(_src(tmp_path, provider=provider), table_token_secret="s" * 32)
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 500
    assert "Traceback" not in response.text


def test_table_multiple_widgets_assets_and_nested_deployment_relative_urls(tmp_path: Path) -> None:
    content = (
        '{{ widget("table", id="one", provider="materials", page_size=1) }}\n\n'
        '{{ widget("table", id="two", provider="materials", page_size=1) }}'
    )
    src = _src(tmp_path, content=content)
    (src / "content" / "guide").mkdir()
    (src / "content" / "guide" / "index.md").write_text(
        '{{ widget("table", provider="materials", page_size=1) }}', encoding="utf-8"
    )
    app = create_asgi_app(src, table_token_secret="s" * 32)

    with TestClient(app) as client:
        response = client.get("/")
        nested = client.get("/guide/index")
        js = client.get("/_httk/assets/table.js")
        css = client.get("/_httk/assets/table.css")

    assert response.status_code == 200
    assert response.text.count("data-httk-table=\"1\"") == 2
    assert 'data-widget-id="one"' in response.text and 'data-widget-id="two"' in response.text
    assert nested.status_code == 200
    assert 'data-endpoint="../_httk/table/page"' in nested.text
    assert 'src="../_httk/assets/table.js"' in nested.text
    assert js.status_code == 200 and "httk:table-updated" in js.text
    assert css.status_code == 200 and ".httk-table" in css.text
    assert '"assets/*.js"' in (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")


def test_static_publish_renders_first_page_and_disables_live_pagination(tmp_path: Path) -> None:
    src = _src(tmp_path)
    report = publish(src, tmp_path / "public", "https://example.test")
    rendered = (tmp_path / "public" / "index.html").read_text(encoding="utf-8")

    assert report.written_files
    assert "all-0" in rendered
    assert "Pagination is available on the live site." in rendered
    assert "data-httk-table-next data-token=\"\" disabled" in rendered
    assert "table.js" not in rendered


def test_provider_contracts_reject_hidden_key_coercion_and_canonical_aliases() -> None:
    with pytest.raises(TypeError, match="string keys"):
        ProviderContext(route="index", widget_id="table", query={}, page={1: "bad"}, global_data={})
    with pytest.raises(TypeError, match="row keys"):
        TablePage.from_rows([{1: "bad"}], columns=["name"])
    with pytest.raises(TypeError, match="JSON-like"):
        TablePage.from_rows([{"name": object()}], columns=["name"])
    signer = TableTokenSigner(b"s" * 32)
    token = signer.sign({"value": "x"})
    payload, signature = token.split(".")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    alternate_signature = signature[:-1] + alphabet[(alphabet.index(signature[-1]) & 0b111100) | 1]
    with pytest.raises(TableContinuationError):
        signer.verify(f"{payload}.{alternate_signature}")


def test_provider_url_builder_and_large_opaque_cursor(tmp_path: Path) -> None:
    cursor = "c" * 12_000
    provider = f'''from httk.web import TablePage

def provide(context, request, **provider_args):
    row = {{"name": context.url_for("details", query={{"id": "mp/1"}})}}
    return TablePage.from_rows([row], columns=["name"], next_cursor={cursor!r} if request.cursor is None else None)
'''
    app = create_asgi_app(_src(tmp_path, provider=provider), table_token_secret="s" * 32)

    with TestClient(app) as client:
        initial = client.get("/")
        token = _token(initial.text)
        response = client.post(
            "/_httk/table/page",
            json={
                "token": token,
                "route": "index",
                "widget_id": re.search(r'data-widget-id="([^"]+)"', initial.text).group(1),
            },
        )

    assert "details?id=mp%2F1" in initial.text
    assert response.status_code == 200


def test_continuation_accepts_rendered_pages_larger_than_token_string_fields(tmp_path: Path) -> None:
    provider = """from httk.web import TablePage

def provide(context, request, **provider_args):
    rows = [{"name": f"{index}-" + ("x" * 400)} for index in range(request.page_size)]
    return TablePage.from_rows(
        rows,
        columns=["name"],
        next_cursor="second" if request.cursor is None else None,
    )
"""
    content = '{{ widget("table", provider="materials", page_size=50) }}'
    app = create_asgi_app(_src(tmp_path, content=content, provider=provider), table_token_secret="s" * 32)

    with TestClient(app) as client:
        initial = client.get("/")
        response = client.post(
            "/_httk/table/page",
            json={
                "token": _token(initial.text),
                "route": "index",
                "widget_id": re.search(r'data-widget-id="([^"]+)"', initial.text).group(1),
            },
        )

    assert response.status_code == 200
    assert len(response.json()["tbody"].encode("utf-8")) > 16_384


def test_provider_url_builder_rejects_query_fragment_and_backslash_routes(tmp_path: Path) -> None:
    provider = """from httk.web import TablePage

def provide(context, request, **provider_args):
    for route in ("details?evil=1", "details#fragment", r"details\\escape"):
        try:
            context.url_for(route, query={"ok": "yes"})
        except ValueError:
            continue
        raise AssertionError(f"unsafe provider route accepted: {route}")
    return TablePage.from_rows([{"name": "safe"}], columns=["name"])
"""
    app = create_asgi_app(_src(tmp_path, provider=provider), table_token_secret="s" * 32)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "safe" in response.text
