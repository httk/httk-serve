"""``create_combined_app``: OPTIMADE, the default site and the file-bytes route on one app."""

import json
import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from httk.core import FileEntry, FileRecord
from httk.core.data_records import DataRecordEntry, TotalEnergyRecord
from httk.core.provenance import Run, RunEdge, RunEntry
from httk.store import EntryIdScheme, SqliteStore
from starlette.testclient import TestClient

from httk.serve.combined import create_combined_app
from httk.serve.web.widgets.core import BUILTIN_WIDGETS

_ODD = "ws/jobs/a/out put#1 50%?é.txt"
_ODD_SERVED = "ws/jobs/a/out%20put%231%2050%25%3F%C3%A9.txt"
_BYTES = b"0123456789abcdef\n"
# The content grammar admits only one-segment names, so pages use the built-in aliases.
_WIDGETS = ("optimade_catalog", "optimade_browser", "optimade_entry")


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[SqliteStore, Path]]:
    """A collect-shaped store plus a files directory reaching the workspace through a symlink."""
    workspace = tmp_path / "workspace"
    (workspace / "jobs" / "a").mkdir(parents=True)
    (workspace / _ODD.removeprefix("ws/")).write_bytes(_BYTES)
    (workspace / "jobs" / "a" / "unregistered.txt").write_bytes(b"secret")
    (workspace / "jobs" / "a" / "changed.txt").write_bytes(b"short")
    (workspace / "jobs" / "a" / "page.html").write_bytes(b"<p>x</p>")
    (workspace / "jobs" / "a" / "link.txt").symlink_to(workspace / _ODD.removeprefix("ws/"))
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    (files_dir / "ws").symlink_to(workspace, target_is_directory=True)

    path = tmp_path / "collected.sqlite"
    with SqliteStore(
        path,
        entry_records={RunEntry: Run, FileEntry: FileRecord, DataRecordEntry: TotalEnergyRecord},
        entry_ids=EntryIdScheme("httk.test", "1"),
    ) as store:

        def save_file(url: str | None, name: str, size: int | None, sha: str, media_type: str = "text/plain") -> str:
            record = FileRecord(url=url, name=name, size=size, sha256=sha * 64, media_type=media_type)
            return store.fetch(FileRecord, store.save(record), eager=True).id

        output = save_file(f"data/{_ODD}", "out put#1 50%?é.txt", len(_BYTES), "a")
        save_file("data/ws/jobs/a/link.txt", "link.txt", len(_BYTES), "b")
        save_file("data/ws/jobs/a/changed.txt", "changed.txt", 999, "c")
        save_file("data/ws/jobs/a/page.html", "page.html", None, "d", media_type="text/html; charset=utf-8")
        save_file(None, "lost.txt", 3, "e")
        energy = store.fetch(TotalEnergyRecord, store.save(TotalEnergyRecord(total_energy=-1.5)), eager=True).id
        edges = (RunEdge("output", "files", output), RunEdge("energy", "records", energy))
        store.save(Run(inputs=(RunEdge("input", "files", output),), outputs=edges, source_id="ws:a"))
    with SqliteStore(path) as store:
        yield store, files_dir


def _urls(client: TestClient, prefix: str = "") -> dict[str, str]:
    data = client.get(f"{prefix}/optimade/v1/files", params={"page_limit": 50}).json()["data"]
    return {entry["attributes"]["name"]: entry["attributes"]["url"] for entry in data}


def test_combined_app_serves_optimade_and_the_byte_route(served: tuple[SqliteStore, Path]) -> None:
    store, files_dir = served
    client = TestClient(create_combined_app(store, files_dir=files_dir))

    assert client.get("/optimade/v1/info").status_code == 200
    redirect = client.get("/optimade", params={"x": "1"}, follow_redirects=False)
    assert (redirect.status_code, redirect.headers["location"]) == (308, "/optimade/?x=1")

    urls = _urls(client)
    # Root-relative resolution against the request origin, percent-encoded after data/.
    assert urls["out put#1 50%?é.txt"] == f"http://testserver/data/{_ODD_SERVED}"
    assert urls["lost.txt"].startswith("ni:")

    response = client.get(urls["out put#1 50%?é.txt"])
    assert response.status_code == 200 and response.content == _BYTES
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-disposition"].startswith("attachment; filename*=utf-8''out%20put%231")
    assert response.headers["x-content-type-options"] == "nosniff"
    head = client.head(urls["out put#1 50%?é.txt"])
    assert (head.status_code, head.content, head.headers["content-length"]) == (200, b"", str(len(_BYTES)))
    ranged = client.get(urls["out put#1 50%?é.txt"], headers={"Range": "bytes=2-5"})
    assert (ranged.status_code, ranged.content) == (206, b"2345")

    # A stored media type with parameters is not echoed.
    page = client.get(urls["page.html"])
    assert (page.status_code, page.headers["content-type"]) == (200, "application/octet-stream")

    for target in (
        "/data/ws/jobs/a/unregistered.txt",  # under DIR but not registered
        "/data/ws/jobs/a/link.txt",  # registered, but the final component is a symlink
        "/data/ws/jobs/a/changed.txt",  # registered size differs
        "/data/ws/%2e%2e/workspace/jobs/a/unregistered.txt",
        "/data/ws/jobs/a/%2E%2E/a/page.html",
        "/data/ws//jobs/a/page.html",
        "/data/ws/jobs/a/page.html%00",
        "/data/",
        "/data/lost.txt",
    ):
        assert client.get(target).status_code == 404, target


def test_combined_app_without_files_dir_serves_stored_urls(served: tuple[SqliteStore, Path]) -> None:
    store, _files_dir = served
    client = TestClient(create_combined_app(store))
    assert _urls(client)["out put#1 50%?é.txt"] == f"data/{_ODD}"
    assert client.get(f"/data/{_ODD_SERVED}").status_code == 404

    external = TestClient(create_combined_app(store, data_path="https://files.example/d"))
    assert _urls(external)["out put#1 50%?é.txt"] == f"https://files.example/d/{_ODD_SERVED}"
    assert external.get(f"/data/{_ODD_SERVED}").status_code == 404


def test_combined_app_public_base_url_and_root_path(served: tuple[SqliteStore, Path]) -> None:
    store, files_dir = served
    public = TestClient(create_combined_app(store, files_dir=files_dir, public_base_url="https://db.example/x/"))
    assert _urls(public)["out put#1 50%?é.txt"] == f"https://db.example/x/data/{_ODD_SERVED}"
    links = public.get("/optimade/v1/files", params={"page_limit": 1}).json()["links"]
    assert links["next"].startswith("https://db.example/x/optimade/v1/files?")

    app = create_combined_app(store, files_dir=files_dir, root_path="/sub")
    mounted = TestClient(app, root_path="/sub")
    url = _urls(mounted, "/sub")["out put#1 50%?é.txt"]
    assert url == f"http://testserver/sub/data/{_ODD_SERVED}"
    assert mounted.get(url).content == _BYTES
    redirect = mounted.get("/sub/optimade", follow_redirects=False)
    assert (redirect.status_code, redirect.headers["location"]) == (308, "/sub/optimade/")


def test_combined_app_validation(served: tuple[SqliteStore, Path], tmp_path: Path) -> None:
    store, files_dir = served
    with pytest.raises(ValueError, match="mutually exclusive"):
        create_combined_app(store, files_dir=files_dir, data_path="https://files.example/d")
    with pytest.raises(ValueError, match="not a directory"):
        create_combined_app(store, files_dir=tmp_path / "missing")
    for bad in ("ftp://db.example", "https://db.example/?q=1", "https://db.example/#f", "/relative", "https://"):
        with pytest.raises(ValueError, match="public_base_url"):
            create_combined_app(store, public_base_url=bad)
    with pytest.raises(ValueError, match="root_path"):
        create_combined_app(store, files_dir=files_dir, root_path="sub")


@pytest.mark.skipif(
    any(BUILTIN_WIDGETS.resolve(name) is None for name in _WIDGETS), reason="the explorer widgets are not registered"
)
def test_default_site_pages_render(served: tuple[SqliteStore, Path]) -> None:
    store, files_dir = served
    client = TestClient(create_combined_app(store, files_dir=files_dir))
    expected = {
        "/": {"base_url": "./optimade", "browse_route": "browse"},
        "/browse?type=files": {"base_url": "./optimade", "entry_type": "files"},
        "/entry?type=files&id=x": {"base_url": "./optimade", "entry_route": "entry", "browse_route": "browse"},
    }
    for target, config in expected.items():
        response = client.get(target)
        assert response.status_code == 200, (target, response.text)
        configs = [
            json.loads(text)
            for text in re.findall(r'<script [^>]*type="application/json">(.*?)</script>', response.text)
        ]
        # The widgets reach OPTIMADE through the site-root-relative base.
        assert any(config.items() <= served_config.items() for served_config in configs), (target, configs)
    assert client.get("/api").status_code == 200
    assert client.get("/site.css").status_code == 200


def test_download_filename_is_bounded() -> None:
    from httk.serve.combined.files import _bounded

    assert _bounded("short.txt") == "short.txt"
    long = _bounded("x" * 300 + ".json")
    assert len(long) == 255 and long.endswith("x.json")
    assert len(_bounded("x" * 300)) == 255


def test_default_site_is_packaged() -> None:
    from httk.serve.combined.api import _default_site

    site = _default_site()
    assert {"config.md", "content", "templates", "static"} <= set(os.listdir(site))
    pages = {path.name for path in (site / "content").iterdir()}
    assert pages == {"index.md", "browse.md", "entry.md", "api.md", "404.md"}
    for page, name in (("index.md", _WIDGETS[0]), ("browse.md", _WIDGETS[1]), ("entry.md", _WIDGETS[2])):
        assert f'widget("{name}"' in (site / "content" / page).read_text(encoding="utf-8")
