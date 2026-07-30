from pathlib import Path

import pytest

from httk.serve.web.engine.site_engine import SiteEngine
from httk.serve.web.model.config import SiteConfig
from httk.serve.web.model.errors import WidgetError


def _src(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    for name in ("content", "static", "templates", "functions", "widgets"):
        (src / name).mkdir(parents=True)
    (src / "templates" / "default.html.j2").write_text("{{ content }}", encoding="utf-8")
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")
    return src


def test_local_widget_escapes_plain_text_and_allows_explicit_markup(tmp_path: Path) -> None:
    src = _src(tmp_path)
    (src / "widgets" / "hello.py").write_text(
        "from httk.serve.web.widgets import trusted_html\n"
        "def render(context, name, trusted=False):\n"
        "    return trusted_html(f'<b>{name}</b>') if trusted else f'<b>{name}</b>'\n",
        encoding="utf-8",
    )
    (src / "content" / "index.md").write_text('{{ widget("site.hello", name="Ada") }}', encoding="utf-8")
    (src / "content" / "trusted.md").write_text(
        '{{ widget("site.hello", name="Ada", trusted=True) }}', encoding="utf-8"
    )
    engine = SiteEngine(SiteConfig.from_srcdir(src))
    assert "&lt;b&gt;Ada&lt;/b&gt;" in engine.render("index").body.decode()
    assert "<b>Ada</b>" in engine.render("trusted").body.decode()


@pytest.mark.parametrize(
    ("suffix", "source"),
    [
        (
            ".md",
            "```\n{{ widget(\"text\", text=\"bad\") }}\n```\n\n    {{ widget(\"text\", text=\"bad\") }}\n\n\\{{ widget(\"text\", text=\"escaped\") }}",
        ),
        (
            ".rst",
            "::\n\n   {{ widget(\"text\", text=\"bad\") }}\n\n.. code-block:: text\n\n   {{ widget(\"text\", text=\"bad\") }}\n\n\\{{ widget(\"text\", text=\"escaped\") }}",
        ),
        (
            ".html",
            "<pre>{{ widget(\"text\", text=\"bad\") }}</pre><script>{{ widget(\"text\", text=\"bad\") }}</script><code>{{ widget(\"text\", text=\"bad\") }}</code><p>\\{{ widget(\"text\", text=\"escaped\") }}</p>",
        ),
        (".httkweb", "::\n\n   {{ widget(\"text\", text=\"bad\") }}\n\n\\{{ widget(\"text\", text=\"escaped\") }}"),
    ],
)
def test_widget_examples_in_code_contexts_are_literal(tmp_path: Path, suffix: str, source: str) -> None:
    src = _src(tmp_path)
    (src / "content" / f"index{suffix}").write_text(source, encoding="utf-8")
    output = SiteEngine(SiteConfig.from_srcdir(src)).render("index").body.decode()
    assert "widget" in output
    assert "{{ widget" in output
    assert "bad" in output
    assert "escaped" in output


@pytest.mark.parametrize(
    "source",
    [
        (
            "````markdown\n{{ widget(\"text\", text=\"inside-long-fence\") }}\n```\n"
            "{{ widget(\"text\", text=\"still-inside-long-fence\") }}\n````\n\n"
            "{{ widget(\"text\", text=\"after-true-close\") }}"
        ),
        (
            "```markdown\n{{ widget(\"text\", text=\"inside-fence\") }}\n``` trailing\n"
            "{{ widget(\"text\", text=\"still-inside-fence\") }}\n```\n\n"
            "{{ widget(\"text\", text=\"after-true-close\") }}"
        ),
    ],
)
def test_markdown_fence_closers_require_matching_length_and_whitespace_tail(tmp_path: Path, source: str) -> None:
    src = _src(tmp_path)
    (src / "content" / "index.md").write_text(source, encoding="utf-8")
    engine = SiteEngine(SiteConfig.from_srcdir(src))
    rendered_content = engine._render_content_without_templates(engine.resolve("index"))

    output = engine.render("index").body.decode()

    assert len(rendered_content.widgets) == 1
    assert rendered_content.widgets[0].name == "text"
    assert rendered_content.widgets[0].props == {"text": "after-true-close"}
    assert "{{ widget(&quot;text&quot;" in output
    assert "inside" in output
    assert "after-true-close" in output


@pytest.mark.parametrize("tag", ["pre", "code", "script", "style", "textarea"])
def test_markdown_raw_html_code_containers_with_blank_lines_are_literal(tmp_path: Path, tag: str) -> None:
    src = _src(tmp_path)
    (src / "content" / "index.md").write_text(
        f'<{tag}>\n\n{{{{ widget("text", text="bad") }}}}\n\n</{tag}>', encoding="utf-8"
    )

    output = SiteEngine(SiteConfig.from_srcdir(src)).render("index").body.decode()

    assert "{{ widget" in output
    assert "bad" in output


def test_markdown_ordinary_raw_html_container_keeps_standalone_widget(tmp_path: Path) -> None:
    src = _src(tmp_path)
    (src / "content" / "index.md").write_text('<div>\n\n{{ widget("text", text="works") }}\n\n</div>', encoding="utf-8")

    output = SiteEngine(SiteConfig.from_srcdir(src)).render("index").body.decode()

    assert "works" in output
    assert "HTTK_WIDGET_" not in output


def test_widget_diagnostics_include_source_span_and_nearby_name(tmp_path: Path) -> None:
    src = _src(tmp_path)
    (src / "content" / "index.md").write_text('\n{{ widget("site.missing", value=1) }}', encoding="utf-8")
    with pytest.raises(WidgetError) as error:
        SiteEngine(SiteConfig.from_srcdir(src)).render("index")
    message = str(error.value)
    assert "index.md:2:1" in message
    assert "nearby names" in message
    assert "Fix:" in message


def test_duplicate_widget_ids_are_rejected(tmp_path: Path) -> None:
    src = _src(tmp_path)
    (src / "content" / "index.md").write_text(
        '{{ widget("text", id="same", text="one") }}\n\n{{ widget("text", id="same", text="two") }}', encoding="utf-8"
    )
    with pytest.raises(WidgetError, match="duplicate widget id"):
        SiteEngine(SiteConfig.from_srcdir(src)).render("index")


def test_widget_output_is_not_reparsed_for_later_placeholders(tmp_path: Path) -> None:
    src = _src(tmp_path)
    page_path = src / "content" / "index.md"
    page_path.write_text('{{ widget("site.first") }}\n\n{{ widget("site.second") }}', encoding="utf-8")
    engine = SiteEngine(SiteConfig.from_srcdir(src))
    placements = engine._render_content_without_templates(engine.resolve("index")).widgets
    assert len(placements) == 2
    later_placeholder = placements[1].placeholder
    (src / "widgets" / "first.py").write_text(
        f"def render(context):\n    return {later_placeholder!r}\n", encoding="utf-8"
    )
    (src / "widgets" / "second.py").write_text("def render(context):\n    return 'SECOND'\n", encoding="utf-8")

    output = engine.render("index").body.decode()

    assert output.count(later_placeholder) == 1
    assert output.count("SECOND") == 1


def test_literal_placeholder_collision_is_source_aware_error(tmp_path: Path) -> None:
    src = _src(tmp_path)
    page_path = src / "content" / "index.md"
    page_path.write_text('{{ widget("text", text="one") }}', encoding="utf-8")
    engine = SiteEngine(SiteConfig.from_srcdir(src))
    placement = engine._render_content_without_templates(engine.resolve("index")).widgets[0]
    page_path.write_text(f'{{{{ widget("text", text="one") }}}}\n\n{placement.placeholder}', encoding="utf-8")

    with pytest.raises(WidgetError, match="placeholder must occur exactly once") as error:
        engine.render("index")
    assert "index.md:1:1" in str(error.value)


@pytest.mark.parametrize(
    "expression",
    [
        '{{ widget("text", value=name) }}',
        '{{ widget("text", *[1]) }}',
        '{{ widget("text", **{"x": 1}) }}',
        '{{ widget("text", x=1, x=2) }}',
        '{{ widget("text", x=other()) }}',
    ],
)
def test_widget_parser_rejects_non_literals(tmp_path: Path, expression: str) -> None:
    src = _src(tmp_path)
    (src / "content" / "index.md").write_text(expression, encoding="utf-8")
    with pytest.raises(WidgetError, match="Widget parse error"):
        SiteEngine(SiteConfig.from_srcdir(src)).render("index")
