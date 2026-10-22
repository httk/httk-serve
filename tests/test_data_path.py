"""Serving-time ``data_path`` presentation of stored ``files`` entry URLs."""

from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

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
        store.save(FileRecord(url=_STORED, name="out.json", sha256="a" * 64))
        store.save(FileRecord(url=_ELSEWHERE, name="x", sha256="b" * 64))
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


def test_data_path_percent_encodes_and_resolves_root_relative(tmp_path: Path) -> None:
    path = tmp_path / "odd.sqlite"
    stored = "data/ws/a dir/r#1 50%?é.txt"
    with SqliteStore(path, entry_records={FileEntry: FileRecord}, entry_ids=EntryIdScheme("httk.test", "1")) as store:
        store.save(FileRecord(url=stored, name="r.txt", sha256="c" * 64))
    with SqliteStore(path) as store:
        (url,) = _urls(TestClient(create_asgi_app(store, data_path="https://example.org/files"))).values()
        assert url == "https://example.org/files/ws/a%20dir/r%231%2050%25%3F%C3%A9.txt"
        assert unquote(urlsplit(url).path) == "/files/" + stored.removeprefix("data/")

        # A root-relative data_path is made absolute against each request's link origin.
        app = create_asgi_app(store, data_path="/files")
        (url,) = _urls(TestClient(app)).values()
        assert url == "http://testserver/files/ws/a%20dir/r%231%2050%25%3F%C3%A9.txt"
        (url,) = _urls(TestClient(app, base_url="https://other.example:8443")).values()
        assert url.startswith("https://other.example:8443/files/ws/")
        explicit = create_asgi_app(store, baseurl="https://public.example/optimade", data_path="/files")
        assert next(iter(_urls(TestClient(explicit)).values())).startswith("https://public.example/files/ws/")


def test_data_path_validation(tmp_path: Path) -> None:
    with SqliteStore(_files_store(tmp_path)) as store:
        adapter = adapter_from_store(store)
        with pytest.raises(TypeError, match="applies only when serving an EntryStore"):
            create_asgi_app(adapter, data_path="/srv/calcs")
        with pytest.raises(ValueError, match="data_path must name a location"):
            StoredBackendAdapter(adapter.federations, adapter.schema, "///")
        with pytest.raises(ValueError, match="not protocol-relative"):
            StoredBackendAdapter(adapter.federations, adapter.schema, "//cdn.example/files")


def test_cli_data_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = str(_files_store(tmp_path))
    context = CLIContext("httk", tmp_path)

    assert command(["optimade", "check", store, "--data-path", ""], context) == 2
    assert "argument --data-path" in capsys.readouterr().err
    assert command(["optimade", "check", store, "--data-path", "https://example.org/files"], context) == 0
    assert capsys.readouterr().out.split() == ["files"]
