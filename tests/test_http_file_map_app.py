"""Coverage for the explicit lightweight file-map application."""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from httk.serve.http import create_file_map_app


def test_file_map_is_allowlisted_and_supports_file_response_semantics(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    path.write_bytes(b"abcdef")
    app = create_file_map_app({"/data.csv": path})

    with TestClient(app) as client:
        response = client.get("/data.csv")
        assert response.status_code == 200
        assert response.content == b"abcdef"
        assert response.headers["content-length"] == "6"
        assert response.headers["content-type"].startswith("text/csv")
        assert "etag" in response.headers
        assert "last-modified" in response.headers

        head = client.head("/data.csv")
        assert head.status_code == 200
        assert head.content == b""
        assert head.headers["content-length"] == "6"

        partial = client.get("/data.csv", headers={"Range": "bytes=1-3"})
        assert partial.status_code == 206
        assert partial.content == b"bcd"
        assert partial.headers["content-range"] == "bytes 1-3/6"

        assert client.get("/other.csv").status_code == 404
        assert client.get("/%2e%2e/data.csv").status_code == 404


def test_file_map_observes_replacement_and_missing_files(tmp_path: Path) -> None:
    path = tmp_path / "live.json"
    path.write_text('{"old":true}', encoding="utf-8")
    app = create_file_map_app({"/live.json": path})

    with TestClient(app) as client:
        first = client.get("/live.json")
        path.write_text('{"replacement":true}', encoding="utf-8")
        second = client.get("/live.json")
        path.unlink()
        missing = client.get("/live.json")

    assert first.content != second.content
    assert second.json() == {"replacement": True}
    assert missing.status_code == 404


@pytest.mark.parametrize("path", ["data.csv", "/", "/a/../b", "//host/file", "/a//b", "/a/"])
def test_file_map_rejects_noncanonical_routes(path: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="canonical"):
        create_file_map_app({path: tmp_path / "data"})
