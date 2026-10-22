"""``httk serve combined serve|check STORE``."""

import json
import os
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from httk.core import FileEntry, FileRecord
from httk.core.cli import CLIContext
from httk.store import EntryIdScheme, SqliteStore
from starlette.testclient import TestClient

from httk.serve.cli import command
from httk.serve.combined import cli as combined_cli
from httk.serve.web.widgets.core import BUILTIN_WIDGETS

# The content grammar admits only one-segment names, so pages use the built-in aliases.
_WIDGETS = ("optimade_catalog", "optimade_browser", "optimade_entry")


def _store(tmp_path: Path, *, lost_only: bool = False) -> Path:
    (tmp_path / "files" / "ws").mkdir(parents=True, exist_ok=True)
    (tmp_path / "files" / "ws" / "out.txt").write_bytes(b"out\n")
    path = tmp_path / ("lost.sqlite" if lost_only else "collected.sqlite")
    with SqliteStore(path, entry_records={FileEntry: FileRecord}, entry_ids=EntryIdScheme("httk.test", "1")) as store:
        # A lost file (served as ni:) comes first; the files probe skips it.
        store.save(FileRecord(url=None, name="lost.txt", size=3, sha256="b" * 64))
        if not lost_only:
            store.save(FileRecord(url="data/ws/out.txt", name="out.txt", size=4, sha256="a" * 64))
    return path


def _site(tmp_path: Path) -> Path:
    """A widget-free custom site with only a home page."""
    site = tmp_path / "site"
    (site / "content").mkdir(parents=True)
    (site / "templates").mkdir()
    (site / "content" / "index.md").write_text("# index\n", encoding="utf-8")
    (site / "templates" / "base_default.html.j2").write_text("<html>{{ content }}</html>", encoding="utf-8")
    (site / "templates" / "default.html.j2").write_text("{{ content }}", encoding="utf-8")
    return site


def _run(argv: list[str], tmp_path: Path) -> int:
    return command(["combined", *argv], CLIContext("httk", tmp_path))


def _capture_uvicorn(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, dict[str, Any]]]:
    calls: list[tuple[Any, dict[str, Any]]] = []

    def run(app: Any, **options: Any) -> None:
        if isinstance(app, str):
            options["environment"] = os.environ[combined_cli._WORKER_ENVIRONMENT]
        else:
            options["files"] = TestClient(app).get("/optimade/v1/files").json()
        calls.append((app, options))

    monkeypatch.setattr(uvicorn, "run", run)
    return calls


def test_parser_mutual_exclusions(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = str(_store(tmp_path))
    files = str(tmp_path / "files")
    assert _run(["check", store, "--files-dir", files, "--data-path", "https://f.example"], tmp_path) == 2
    assert "not allowed with argument" in capsys.readouterr().err
    assert _run(["serve", store, "--port", "8000", "--uds", str(tmp_path / "s")], tmp_path) == 2
    assert "not allowed with argument" in capsys.readouterr().err
    assert _run(["serve", store, "--root-path", "sub"], tmp_path) == 2
    assert "argument --root-path" in capsys.readouterr().err
    assert _run(["check", store, "--public-base-url", "ftp://x.example"], tmp_path) == 2
    assert "public_base_url" in capsys.readouterr().err
    assert _run(["check", str(tmp_path / "missing.sqlite")], tmp_path) == 2
    assert "does not exist" in capsys.readouterr().err


def test_check_with_files_dir_and_custom_site(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store, site = str(_store(tmp_path)), str(_site(tmp_path))
    assert _run(["check", store, "--site", site, "--files-dir", str(tmp_path / "files")], tmp_path) == 0
    captured = capsys.readouterr()
    assert "/optimade/v1/files" in captured.out.split()
    assert "warning" not in captured.err

    # A registered file missing from the files directory is a warning, not a failure.
    (tmp_path / "files" / "ws" / "out.txt").unlink()
    assert _run(["check", store, "--site", site, "--files-dir", str(tmp_path / "files")], tmp_path) == 0
    assert "not downloadable (HTTP 404)" in capsys.readouterr().err

    # Stored data/ URLs without a files location are reported.
    assert _run(["check", store, "--site", site], tmp_path) == 0
    assert "neither --files-dir nor --data-path" in capsys.readouterr().err

    # The probe listing stays within the configured page limit.
    config = tmp_path / "optimade.toml"
    config.write_text("page_limit_max = 5\n", encoding="utf-8")
    files_dir = str(tmp_path / "files")
    assert _run(["check", store, "--site", site, "--files-dir", files_dir, "--config", str(config)], tmp_path) == 0
    err = capsys.readouterr().err
    assert "not downloadable (HTTP 404)" in err and "cannot list" not in err

    # Only lost files: nothing to probe.
    lost = str(_store(tmp_path, lost_only=True))
    assert _run(["check", lost, "--site", site, "--files-dir", str(tmp_path / "files")], tmp_path) == 0
    assert "no downloadable file entries to probe" in capsys.readouterr().err

    # A custom site without a home page fails the check.
    (Path(site) / "content" / "index.md").unlink()
    assert _run(["check", store, "--site", site], tmp_path) == 1
    assert "/: HTTP 404" in capsys.readouterr().err


def test_serve_prints_the_endpoints_and_runs_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store, site = str(_store(tmp_path)), str(_site(tmp_path))
    calls = _capture_uvicorn(monkeypatch)
    argv = ["serve", store, "--site", site, "--files-dir", str(tmp_path / "files"), "--root-path", "/db"]
    assert _run([*argv, "--port", "9000"], tmp_path) == 0
    ((_app, options),) = calls
    assert options["root_path"] == "/db" and options["port"] == 9000
    urls = [entry["attributes"]["url"] for entry in options["files"]["data"]]
    assert "http://testserver/db/data/ws/out.txt" in urls
    out = capsys.readouterr().out
    assert "Site: http://127.0.0.1:9000/db/\n" in out and "OPTIMADE: http://127.0.0.1:9000/db/optimade/\n" in out


def test_serve_workers_use_the_worker_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, site = _store(tmp_path), _site(tmp_path)
    calls = _capture_uvicorn(monkeypatch)
    monkeypatch.delenv(combined_cli._WORKER_ENVIRONMENT, raising=False)
    argv = ["serve", str(store), "--site", str(site), "--public-base-url", "https://db.example/x", "--workers", "2"]
    assert _run(argv, tmp_path) == 0
    ((target, options),) = calls
    assert (target, options["factory"], options["workers"]) == ("httk.serve.combined.cli:worker_app", True, 2)
    settings = json.loads(options["environment"])
    assert (settings["store"], settings["public_base_url"]) == (str(store), "https://db.example/x")

    data = TestClient(combined_cli.worker_app()).get("/optimade/v1/files").json()["data"]
    assert "data/ws/out.txt" in [entry["attributes"]["url"] for entry in data]
    monkeypatch.delenv(combined_cli._WORKER_ENVIRONMENT)
    with pytest.raises(RuntimeError, match="no store configuration"):
        combined_cli.worker_app()


@pytest.mark.skipif(
    any(BUILTIN_WIDGETS.resolve(name) is None for name in _WIDGETS), reason="the explorer widgets are not registered"
)
def test_check_default_site(tmp_path: Path) -> None:
    assert _run(["check", str(_store(tmp_path)), "--files-dir", str(tmp_path / "files")], tmp_path) == 0
