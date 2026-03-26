from pathlib import Path

from httk.web.api import publish


def _make_src(tmp_path: Path) -> tuple[Path, Path]:
    src = tmp_path / "src"
    out = tmp_path / "public"
    (src / "content").mkdir(parents=True)
    (src / "static").mkdir(parents=True)
    (src / "templates").mkdir(parents=True)
    (src / "functions").mkdir(parents=True)
    return src, out


def test_publish_reports_warning_when_function_query_constraints_not_met(tmp_path: Path) -> None:
    src, out = _make_src(tmp_path)

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
greeting-function: hello:name:greeting_fragment
---

Body text
""",
        encoding="utf-8",
    )

    report = publish(src, out, "http://localhost/")
    assert any("input parameter constraints not satisfied" in warning for warning in report.warnings)


def test_publish_reports_warning_when_function_template_missing(tmp_path: Path) -> None:
    src, out = _make_src(tmp_path)

    (src / "functions" / "hello.py").write_text(
        """def execute(name='x', global_data=None, **kwargs):
    return name.upper()
""",
        encoding="utf-8",
    )

    (src / "templates" / "default.html.j2").write_text("{{ greeting }}|{{ content }}", encoding="utf-8")
    (src / "templates" / "base_default.html.j2").write_text("{{ content }}", encoding="utf-8")

    (src / "content" / "index.md").write_text(
        """---
template: default
greeting-function: hello::missing_fragment
---

Body text
""",
        encoding="utf-8",
    )

    report = publish(src, out, "http://localhost/")
    assert any("rendered without template" in warning for warning in report.warnings)
