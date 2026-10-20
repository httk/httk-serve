"""Serving-time ``data_path`` presentation of stored ``files`` entry URLs."""

from pathlib import Path
from urllib.parse import quote

import pytest
from httk.core import FileEntry, FileRecord
from httk.core.cli import CLIContext
from httk.store import EntryIdScheme, SqliteStore
from starlette.testclient import TestClient

from httk.serve.cli import command
from httk.serve.optimade import adapter_from_store, create_asgi_app
from httk.serve.optimade.backend.stores import StoredBackendAdapter

_STORED = "data/ws/jobs/a/out.json"
_SERVED = "https://example.org/files/ws/jobs/a/out.json"
_ELSEWHERE = "https://elsewhere/x"


def _files_store(tmp_path: Path) -> Path:
    path = tmp_path / "files.sqlite"
    with SqliteStore(path, entry_records={FileEntry: FileRecord}, entry_ids=EntryIdScheme("httk.test", "1")) as store:
        store.save(FileRecord(url=_STORED, name="out.json"))
        store.save(FileRecord(url=_ELSEWHERE, name="x"))
    return path


def _urls(client: TestClient) -> dict[str, str]:
    data = client.get("/v1/files").json()["data"]
    return {entry["id"]: entry["attributes"]["url"] for entry in data}


def test_data_path_rewrites_served_urls_only(tmp_path: Path) -> None:
    with SqliteStore(_files_store(tmp_path)) as store:
        plain_client = TestClient(create_asgi_app(store))
        assert sorted(_urls(plain_client).values()) == sorted([_STORED, _ELSEWHERE])

        client = TestClient(create_asgi_app(store, data_path="https://example.org/files/"))
        served = _urls(client)
        assert sorted(served.values()) == sorted([_SERVED, _ELSEWHERE])
        (first,) = (entry_id for entry_id, url in served.items() if url == _SERVED)
        single = client.get(f"/v1/files/{quote(first, safe='')}").json()["data"]
        assert single["attributes"]["url"] == _SERVED
        assert adapter_from_store(store, data_path="/srv/calcs//").data_path == "/srv/calcs"

        # url is not filterable under the default file projections; data_path adds no filter semantics.
        target = "/v1/files?filter=" + quote(f'url="{_SERVED}"')
        plain, mapped = plain_client.get(target), client.get(target)
        assert plain.status_code == mapped.status_code == 501
        assert plain.json()["errors"] == mapped.json()["errors"]
        assert "not implemented" in mapped.json()["errors"][0]["detail"]


def test_data_path_validation(tmp_path: Path) -> None:
    with SqliteStore(_files_store(tmp_path)) as store:
        adapter = adapter_from_store(store)
        with pytest.raises(TypeError, match="applies only when serving an EntryStore"):
            create_asgi_app(adapter, data_path="/srv/calcs")
        with pytest.raises(ValueError, match="data_path must name a location"):
            StoredBackendAdapter(adapter.federations, adapter.schema, "///")


def test_cli_data_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = str(_files_store(tmp_path))
    context = CLIContext("httk", tmp_path)

    assert command(["optimade", "check", store, "--data-path", ""], context) == 2
    assert "argument --data-path" in capsys.readouterr().err
    assert command(["optimade", "check", store, "--data-path", "https://example.org/files"], context) == 0
    assert capsys.readouterr().out.split() == ["files"]
