"""Serving-time ``data_url`` presentation of stored ``files`` entry URLs and the ``--data-path`` byte route."""

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import pytest
import uvicorn
from httk.core import FileEntry, FileRecord
from httk.core.cli import CLIContext
from httk.store import EntryIdScheme, SqliteStore
from starlette.testclient import TestClient

from httk.serve.cli import command
from httk.serve.optimade import adapter_from_store, create_asgi_app
from httk.serve.optimade import cli as optimade_cli
from httk.serve.optimade.backend.stores import StoredBackendAdapter

_STORED = "data/ws/jobs/a/out.json"
_SERVED = "https://example.org/files/ws/jobs/a/out.json"
_ELSEWHERE = "https://elsewhere/x"


def _files_store(tmp_path: Path) -> Path:
    path = tmp_path / "files.sqlite"
    with SqliteStore(path, entry_records={FileEntry: FileRecord}, entry_ids=EntryIdScheme("httk.test", "1")) as store:
        store.save(FileRecord(url=_STORED, name="out.json", sha256="a" * 64))
        store.save(FileRecord(url=_ELSEWHERE, name="x", sha256="b" * 64))
    return path


def _urls(client: TestClient) -> dict[str, str]:
    data = client.get("/v1/files").json()["data"]
    return {entry["id"]: entry["attributes"]["url"] for entry in data}


def test_data_url_rewrites_served_urls_only(tmp_path: Path) -> None:
    with SqliteStore(_files_store(tmp_path)) as store:
        plain_client = TestClient(create_asgi_app(store))
        assert sorted(_urls(plain_client).values()) == sorted([_STORED, _ELSEWHERE])

        client = TestClient(create_asgi_app(store, data_url="https://example.org/files/"))
        served = _urls(client)
        assert sorted(served.values()) == sorted([_SERVED, _ELSEWHERE])
        (first,) = (entry_id for entry_id, url in served.items() if url == _SERVED)
        single = client.get(f"/v1/files/{quote(first, safe='')}").json()["data"]
        assert single["attributes"]["url"] == _SERVED
        assert adapter_from_store(store, data_url="/srv/calcs//").data_url == "/srv/calcs"

        # url is not filterable under the default file projections; data_url adds no filter semantics.
        target = "/v1/files?filter=" + quote(f'url="{_SERVED}"')
        plain, mapped = plain_client.get(target), client.get(target)
        assert plain.status_code == mapped.status_code == 501
        assert plain.json()["errors"] == mapped.json()["errors"]
        assert "not implemented" in mapped.json()["errors"][0]["detail"]


def test_data_url_percent_encodes_and_resolves_root_relative(tmp_path: Path) -> None:
    path = tmp_path / "odd.sqlite"
    stored = "data/ws/a dir/r#1 50%?é.txt"
    with SqliteStore(path, entry_records={FileEntry: FileRecord}, entry_ids=EntryIdScheme("httk.test", "1")) as store:
        store.save(FileRecord(url=stored, name="r.txt", sha256="c" * 64))
    with SqliteStore(path) as store:
        (url,) = _urls(TestClient(create_asgi_app(store, data_url="https://example.org/files"))).values()
        assert url == "https://example.org/files/ws/a%20dir/r%231%2050%25%3F%C3%A9.txt"
        assert unquote(urlsplit(url).path) == "/files/" + stored.removeprefix("data/")

        # A root-relative data_url is made absolute against each request's link origin.
        app = create_asgi_app(store, data_url="/files")
        (url,) = _urls(TestClient(app)).values()
        assert url == "http://testserver/files/ws/a%20dir/r%231%2050%25%3F%C3%A9.txt"
        (url,) = _urls(TestClient(app, base_url="https://other.example:8443")).values()
        assert url.startswith("https://other.example:8443/files/ws/")
        explicit = create_asgi_app(store, baseurl="https://public.example/optimade", data_url="/files")
        assert next(iter(_urls(TestClient(explicit)).values())).startswith("https://public.example/files/ws/")


def test_data_url_validation(tmp_path: Path) -> None:
    with SqliteStore(_files_store(tmp_path)) as store:
        adapter = adapter_from_store(store)
        with pytest.raises(TypeError, match="applies only when serving an EntryStore"):
            create_asgi_app(adapter, data_url="/srv/calcs")
        with pytest.raises(ValueError, match="data_url must name a location"):
            StoredBackendAdapter(adapter.federations, adapter.schema, "///")
        with pytest.raises(ValueError, match="not protocol-relative"):
            StoredBackendAdapter(adapter.federations, adapter.schema, "//cdn.example/files")


def test_cli_data_url(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = str(_files_store(tmp_path))
    context = CLIContext("httk", tmp_path)

    assert command(["optimade", "check", store, "--data-url", ""], context) == 2
    assert "argument --data-url" in capsys.readouterr().err
    assert command(["optimade", "check", store, "--data-url", "https://example.org/files"], context) == 0
    assert capsys.readouterr().out.split() == ["files"]


def _workspace(tmp_path: Path) -> Path:
    """The workspace directory holding the bytes of ``_STORED`` (``data/ws/...``)."""
    target = tmp_path / "files" / _STORED.removeprefix("data/")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"{}\n")
    return tmp_path / "files" / "ws"


@pytest.mark.parametrize("group", ["optimade", "combined"])
def test_cli_data_path_parsing(tmp_path: Path, group: str, capsys: pytest.CaptureFixture[str]) -> None:
    store = str(_files_store(tmp_path))
    workspace = _workspace(tmp_path)
    context = CLIContext("httk", tmp_path)
    files, other = str(tmp_path / "files"), str(tmp_path)
    for argv in (
        ["--data-path", files],
        ["--data-path", f"ws={workspace}"],
        ["--data-path", f"ws={workspace}", "--data-path", f"b={other}"],
    ):
        assert command([group, "check", store, *argv], context) == 0, argv
        assert "warning" not in capsys.readouterr().err, argv
    for argv, message in (
        (["--data-path", files, "--data-path", f"ws={workspace}"], "not both or two PATHs"),
        (["--data-path", f"ws={workspace}", "--data-path", files], "not both or two PATHs"),
        (["--data-path", files, "--data-path", other], "not both or two PATHs"),
        (["--data-path", f"ws={workspace}", "--data-path", f"ws={other}"], "'ws' is given twice"),
        (["--data-path", files, "--data-url", "https://example.org/files"], "not allowed with argument"),
        (["--data-path", str(tmp_path / "missing")], "not a directory"),
        (["--data-path", f"ws={tmp_path / 'missing'}"], "not a directory"),
        (["--data-path", ""], "must name a directory"),
        (["--data-path", "ws="], "must name a directory"),
        (["--data-path", f"w\\s={workspace}"], "argument --data-path: not a directory"),
    ):
        assert command([group, "check", store, *argv], context) == 2, argv
        assert message in capsys.readouterr().err, argv
    assert command([group, "check", store, "--data-path", f"..={workspace}"], context) == 2
    assert "single path component" in capsys.readouterr().err

    # An = after a path separator belongs to a plain PATH.
    (tmp_path / "dir=with").symlink_to(tmp_path / "files", target_is_directory=True)
    assert command([group, "check", store, "--data-path", str(tmp_path / "dir=with")], context) == 0
    assert "warning" not in capsys.readouterr().err


def test_optimade_data_path_serves_bytes(tmp_path: Path) -> None:
    path, workspace = _files_store(tmp_path), _workspace(tmp_path)
    served = _STORED.removeprefix("data/")
    for data_path in (str(tmp_path / "files"), {"ws": str(workspace)}):
        store, app = optimade_cli._store_app(path, None, None, [], tmp_path, None, data_path)
        with store:
            client = TestClient(app)
            url = next(url for url in _urls(client).values() if url != _ELSEWHERE)
            assert url == f"http://testserver/data/{served}"
            assert client.get(url).content == b"{}\n"
            assert client.get("/data/ws/jobs/a/other.json").status_code == 404
            assert client.get("/v1/info").status_code == 200

    store, app = optimade_cli._store_app(path, None, "https://x/api", [], tmp_path, None, {"ws": str(workspace)})
    with store:
        assert sorted(_urls(TestClient(app)).values()) == sorted([f"https://x/api/data/{served}", _ELSEWHERE])
    store, app = optimade_cli._store_app(path, None, None, [], tmp_path, None, str(tmp_path / "files"), "/sub")
    with store:
        client = TestClient(app, root_path="/sub")
        data = client.get("/sub/v1/files").json()["data"]
        url = next(entry["attributes"]["url"] for entry in data if entry["attributes"]["url"] != _ELSEWHERE)
        assert url == f"http://testserver/sub/data/{served}"
        assert client.get(url).content == b"{}\n"


def test_optimade_check_probes_and_warns(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = str(_files_store(tmp_path))
    workspace = _workspace(tmp_path)
    context = CLIContext("httk", tmp_path)
    assert command(["optimade", "check", store, "--data-path", f"ws={workspace}"], context) == 0
    captured = capsys.readouterr()
    assert captured.out.split() == ["files"] and "warning" not in captured.err

    assert command(["optimade", "check", store], context) == 0
    assert "neither --data-path nor --data-url" in capsys.readouterr().err

    # Only workspaces that --data-path serves are probed.
    assert command(["optimade", "check", store, "--data-path", f"other={tmp_path}"], context) == 0
    err = capsys.readouterr().err
    assert "no downloadable file entries to probe" in err and "not downloadable (HTTP" not in err

    (workspace / "jobs" / "a" / "out.json").unlink()
    assert command(["optimade", "check", store, "--data-path", f"ws={workspace}"], context) == 0
    assert "not downloadable (HTTP 404)" in capsys.readouterr().err


def test_optimade_serve_workers_build_the_composition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, workspace = str(_files_store(tmp_path)), _workspace(tmp_path)
    environments: list[str] = []
    monkeypatch.setattr(uvicorn, "run", lambda *_args, **_options: environments.append(os.environ[name]))
    name = optimade_cli._WORKER_ENVIRONMENT
    monkeypatch.delenv(name, raising=False)
    argv = ["optimade", "serve", store, "--data-path", f"ws={workspace}", "--root-path", "/sub", "--workers", "2"]
    assert command(argv, CLIContext("httk", tmp_path)) == 0
    monkeypatch.setenv(name, environments[0])
    client = TestClient(optimade_cli.worker_app(), root_path="/sub")
    data = client.get("/sub/v1/files").json()["data"]
    url = next(entry["attributes"]["url"] for entry in data if entry["attributes"]["url"] != _ELSEWHERE)
    assert url == "http://testserver/sub/" + _STORED
    assert client.get(url).content == b"{}\n"


def test_optimade_data_path_refuses_an_entry_type_named_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from httk.serve.optimade import api

    original = api.create_asgi_app

    def create(*args: Any, **options: Any) -> Any:
        app = original(*args, **options)
        app.state.schema = SimpleNamespace(all_entries=("files", "data"))
        return app

    monkeypatch.setattr(api, "create_asgi_app", create)
    with pytest.raises(ValueError, match="entry type named 'data'"):
        optimade_cli._store_app(_files_store(tmp_path), None, None, [], tmp_path, None, str(_workspace(tmp_path)))
