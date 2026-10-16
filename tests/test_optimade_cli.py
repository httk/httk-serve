import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from httk.atomistic import Species, StructureEntry, UnitcellStructure, UnitcellStructureRecord
from httk.core.cli import CLIContext
from httk.store import EntryIdScheme, SqliteStore
from starlette.testclient import TestClient

from httk.serve.cli import command
from httk.serve.optimade import cli as optimade_cli


def _sqlite_store(tmp_path: Path) -> Path:
    path = tmp_path / "materials.sqlite"
    with SqliteStore(
        path,
        entry_records={StructureEntry: UnitcellStructureRecord},
        entry_ids=EntryIdScheme("httk.example", "1"),
    ) as store:
        store.save(
            UnitcellStructure(
                [[5, 0, 0], [0, 5, 0], [0, 0, 5]],
                [[0, 0, 0]],
                [Species("Si", ("Si",), (1,))],
                ["Si"],
                chemical_formula_descriptive="Si",
            )
        )
    return path


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "optimade.toml"
    path.write_text(
        """
page_limit_max = 7
cors_origins = ["https://browser.example"]

[provider]
name = "Example materials"
description = "An example provider"
prefix = "exmpl"
""",
        encoding="utf-8",
    )
    return path


def _run(argv: list[str], tmp_path: Path) -> int:
    return command(["optimade", *argv], CLIContext("httk", tmp_path))


def _capture_uvicorn(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, dict[str, Any]]]:
    calls: list[tuple[Any, dict[str, Any]]] = []

    def run(app: Any, **options: Any) -> None:
        if isinstance(app, str):
            # Workers import the factory with the environment the parent set.
            options["environment"] = os.environ[optimade_cli._WORKER_ENVIRONMENT]
        else:
            response = TestClient(app).get("/v1/structures?page_limit=0")
            options["served"] = response.json()
        calls.append((app, options))

    monkeypatch.setattr(uvicorn, "run", run)
    return calls


def test_check_lists_served_entries(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _sqlite_store(tmp_path)

    assert _run(["check", str(store), "--config", str(_config(tmp_path))], tmp_path) == 0
    assert capsys.readouterr().out.split() == ["structures"]


def test_check_serves_duckdb_read_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pytest.importorskip("duckdb_engine")
    from httk.store import DuckdbStore

    path = tmp_path / "materials.duckdb"
    with DuckdbStore(
        path,
        entry_records={StructureEntry: UnitcellStructureRecord},
        entry_ids=EntryIdScheme("httk.example", "1"),
    ):
        pass
    first = optimade_cli.open_store(path)
    second = optimade_cli.open_store(path)
    first.close()
    second.close()

    assert _run(["check", str(path)], tmp_path) == 0
    assert capsys.readouterr().out.split() == ["structures"]


def test_check_refuses_missing_and_foreign_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "missing.sqlite"
    assert _run(["check", str(missing)], tmp_path) == 2
    assert "does not exist" in capsys.readouterr().err
    assert not missing.exists()

    text = tmp_path / "notes.sqlite"
    text.write_text("not a database", encoding="utf-8")
    assert _run(["check", str(text)], tmp_path) == 2
    assert "not an SQLite or DuckDB store file" in capsys.readouterr().err

    foreign = tmp_path / "foreign.sqlite"
    connection = sqlite3.connect(foreign)
    connection.execute("CREATE TABLE notes (text)")
    connection.commit()
    connection.close()
    assert _run(["check", str(foreign)], tmp_path) == 2
    assert "httk serve optimade check:" in capsys.readouterr().err
    connection = sqlite3.connect(foreign)
    assert connection.execute("SELECT name FROM sqlite_master").fetchall() == [("notes",)]
    connection.close()


def test_config_rejects_unknown_keys_and_invalid_toml(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _sqlite_store(tmp_path)
    unknown = tmp_path / "unknown.toml"
    unknown.write_text('baseurl = "https://example.org"\n', encoding="utf-8")
    assert _run(["check", str(store), "--config", str(unknown)], tmp_path) == 2
    assert "unknown OPTIMADE configuration keys: baseurl" in capsys.readouterr().err

    invalid = tmp_path / "invalid.toml"
    invalid.write_text("provider = [\n", encoding="utf-8")
    assert _run(["check", str(store), "--config", str(invalid)], tmp_path) == 2
    assert "invalid.toml" in capsys.readouterr().err

    config = optimade_cli.load_config(_config(tmp_path))
    assert config.provider["prefix"] == "exmpl"
    assert config.cors_origins == ("https://browser.example",)
    assert config.page_limit_max == 7


def test_serve_runs_the_store_application(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _sqlite_store(tmp_path)
    calls = _capture_uvicorn(monkeypatch)

    argv = ["serve", str(store), "--config", str(_config(tmp_path)), "--baseurl", "https://example.org/optimade"]
    assert _run([*argv, "--port", "8001", "--root-path", "/optimade", "--no-access-log"], tmp_path) == 0
    assert _run([*argv, "--uds", str(tmp_path / "optimade.sock")], tmp_path) == 0

    (_app, tcp), (_app, uds) = calls
    assert {key: tcp[key] for key in ("host", "port", "root_path", "access_log")} == {
        "host": "127.0.0.1",
        "port": 8001,
        "root_path": "/optimade",
        "access_log": False,
    }
    assert uds["uds"] == str(tmp_path / "optimade.sock")
    assert "host" not in uds and "port" not in uds
    served = tcp["served"]
    assert served["meta"]["data_returned"] == 1
    assert served["meta"]["provider"]["prefix"] == "exmpl"
    assert served["links"]["next"].startswith("https://example.org/optimade/v1/structures?")


def test_serve_workers_use_the_worker_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _sqlite_store(tmp_path)
    config = _config(tmp_path)
    calls = _capture_uvicorn(monkeypatch)
    monkeypatch.delenv(optimade_cli._WORKER_ENVIRONMENT, raising=False)

    assert _run(["serve", str(store), "--config", str(config), "--workers", "3"], tmp_path) == 0

    ((target, options),) = calls
    assert target == "httk.serve.optimade.cli:worker_app"
    assert options["factory"] is True
    assert options["workers"] == 3
    assert json.loads(options["environment"]) == {"store": str(store), "config": str(config), "baseurl": None}
    app = optimade_cli.worker_app()
    response = TestClient(app).get("/v1/structures")
    assert response.json()["meta"]["data_returned"] == 1
    assert response.json()["meta"]["provider"]["prefix"] == "exmpl"


def test_serve_refuses_a_missing_store_before_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _capture_uvicorn(monkeypatch)

    assert _run(["serve", str(tmp_path / "missing.sqlite"), "--workers", "2"], tmp_path) == 2
    assert calls == []
    assert "does not exist" in capsys.readouterr().err


def test_umbrella_cli_dispatches_optimade_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from httk.core.cli import main

    assert main(["serve", "optimade", "check", str(_sqlite_store(tmp_path))]) == 0
    assert "structures" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("toml", "message"),
    [
        ('provider = "Example"\n', "provider must be a table with string name, description and prefix"),
        ('[provider]\nname = "Example"\n', "provider must be a table with string name, description and prefix"),
        ("implementation = [1]\n", "implementation must be a table"),
        ("links = [1, 2]\n", "links must be tables"),
        ('cors_origins = "https://a.example"\n', "cors_origins must be a list of strings"),
        ('request_delay = "slow"\n', "request_delay must be a number"),
        ("[database]\nupdated = 2026-01-01T00:00:00Z\n", "database contains a TOML date or time"),
    ],
)
def test_config_rejects_malformed_values(tmp_path: Path, toml: str, message: str) -> None:
    path = tmp_path / "optimade.toml"
    path.write_text(toml, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        optimade_cli.load_config(path)


def test_serve_validates_bind_and_root_path_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _sqlite_store(tmp_path)
    calls = _capture_uvicorn(monkeypatch)

    assert _run(["serve", str(store), "--root-path", "optimade"], tmp_path) == 2
    assert "argument --root-path" in capsys.readouterr().err
    assert _run(["serve", str(store), "--port", "8001", "--uds", str(tmp_path / "s.sock")], tmp_path) == 2
    assert "not allowed with argument --port" in capsys.readouterr().err
    assert calls == []


def test_endpoint_self_test_failures_stop_check_and_serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _sqlite_store(tmp_path)
    calls = _capture_uvicorn(monkeypatch)
    real_get = optimade_cli._get

    async def failing_links(app: Any, target: str) -> tuple[int, bytes]:
        if target == "/v1/links":
            return 500, b'{"errors": [{"detail": "broken links"}]}'
        return await real_get(app, target)

    monkeypatch.setattr(optimade_cli, "_get", failing_links)

    assert _run(["check", str(store)], tmp_path) == 1
    assert "/v1/links: HTTP 500: broken links" in capsys.readouterr().err
    assert _run(["serve", str(store)], tmp_path) == 2
    assert "/v1/links: HTTP 500: broken links" in capsys.readouterr().err
    assert calls == []


def test_worker_that_cannot_open_the_store_reports_startup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        optimade_cli._WORKER_ENVIRONMENT,
        json.dumps({"store": str(tmp_path / "removed.sqlite"), "config": None, "baseurl": None}),
    )

    with pytest.raises(SystemExit) as raised:
        optimade_cli.worker_app()
    assert raised.value.code == uvicorn.config.STARTUP_FAILURE
