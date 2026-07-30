from pathlib import Path

import pytest
from httk.core.cli_context import CLIContext
from starlette.testclient import TestClient

from httk.web import api
from httk.web.api import create_asgi_app, publish
from httk.web.cli import command
from httk.web.engine import SiteEngine
from httk.web.model.config import SiteConfig
from httk.web.resources import SITE_RESOURCES_KEY, SiteResources


def _site(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    for name in ("content", "static", "templates", "functions"):
        (src / name).mkdir(parents=True)
    (src / "content" / "index.md").write_text("hello", encoding="utf-8")
    return src


def _startup_cleanup(src: Path, marker: Path, *, fail: bool = False, cleanup_fails: bool = False) -> None:
    failure = "    raise RuntimeError('startup failed')\n" if fail else ""
    cleanup_failure = "        raise RuntimeError('cleanup failed')\n" if cleanup_fails else ""
    (src / "functions" / "init.py").write_text(
        (
            "from pathlib import Path\n\n"
            "def execute(global_data, **kwargs):\n"
            f"    marker = Path({str(marker)!r})\n"
            "    def cleanup():\n"
            "        marker.write_text('closed', encoding='utf-8')\n"
            f"{cleanup_failure}"
            f"    global_data[{SITE_RESOURCES_KEY!r}].register(cleanup)\n"
            f"{failure}"
        ),
        encoding="utf-8",
    )


def test_site_resources_close_lifo_once_and_reject_late_registration() -> None:
    resources = SiteResources()
    events: list[str] = []
    resources.register(lambda: events.append("first"))
    resources.register(lambda: events.append("second"))

    resources.close()
    resources.close()

    assert events == ["second", "first"]
    assert resources.closed
    with pytest.raises(RuntimeError, match="after the engine has closed"):
        resources.register(lambda: None)


def test_site_resources_run_all_cleanups_before_reraising_a_failure() -> None:
    resources = SiteResources()
    events: list[str] = []

    def broken_cleanup() -> None:
        events.append("broken")
        raise RuntimeError("cleanup failed")

    resources.register(lambda: events.append("first"))
    resources.register(broken_cleanup)

    with pytest.raises(RuntimeError, match="cleanup failed"):
        resources.close()

    assert events == ["broken", "first"]


def test_engine_context_manager_closes_registered_resources(tmp_path: Path) -> None:
    src = _site(tmp_path)
    marker = tmp_path / "closed"
    _startup_cleanup(src, marker)

    with SiteEngine(SiteConfig.from_srcdir(src)) as engine:
        assert engine.global_data[SITE_RESOURCES_KEY] is engine.resources
        assert not marker.exists()

    assert marker.read_text(encoding="utf-8") == "closed"


def test_init_failure_closes_registered_resources_before_reraising(tmp_path: Path) -> None:
    src = _site(tmp_path)
    marker = tmp_path / "closed"
    _startup_cleanup(src, marker, fail=True)

    with pytest.raises(RuntimeError, match="startup failed"):
        SiteEngine(SiteConfig.from_srcdir(src))

    assert marker.read_text(encoding="utf-8") == "closed"


def test_asgi_lifespan_closes_engine_resources(tmp_path: Path) -> None:
    src = _site(tmp_path)
    marker = tmp_path / "closed"
    _startup_cleanup(src, marker)
    app = create_asgi_app(src)

    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert not marker.exists()

    assert marker.read_text(encoding="utf-8") == "closed"
    assert app.state.engine.resources.closed


def test_publish_closes_engine_resources(tmp_path: Path) -> None:
    src = _site(tmp_path)
    marker = tmp_path / "closed"
    _startup_cleanup(src, marker)

    publish(src, tmp_path / "public", "https://example.test/")

    assert marker.read_text(encoding="utf-8") == "closed"


def test_serve_closes_engine_if_server_runner_returns_without_lifespan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _site(tmp_path)
    marker = tmp_path / "closed"
    _startup_cleanup(src, marker)
    monkeypatch.setattr(api, "run_dev_server", lambda **kwargs: None)

    api.serve(src)

    assert marker.read_text(encoding="utf-8") == "closed"


def test_check_command_closes_engine_resources(tmp_path: Path) -> None:
    src = _site(tmp_path)
    marker = tmp_path / "closed"
    _startup_cleanup(src, marker)

    assert command(["check", str(src)], CLIContext("httk", tmp_path)) == 0

    assert marker.read_text(encoding="utf-8") == "closed"


def test_publish_surfaces_a_cleanup_failure_after_a_successful_publish(tmp_path: Path) -> None:
    src = _site(tmp_path)
    marker = tmp_path / "closed"
    _startup_cleanup(src, marker, cleanup_fails=True)

    with pytest.raises(RuntimeError, match="cleanup failed"):
        publish(src, tmp_path / "public", "https://example.test/")

    assert marker.read_text(encoding="utf-8") == "closed"


def test_check_command_surfaces_a_cleanup_failure(tmp_path: Path) -> None:
    src = _site(tmp_path)
    marker = tmp_path / "closed"
    _startup_cleanup(src, marker, cleanup_fails=True)

    with pytest.raises(RuntimeError, match="cleanup failed"):
        command(["check", str(src)], CLIContext("httk", tmp_path))

    assert marker.read_text(encoding="utf-8") == "closed"
