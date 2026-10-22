"""The file-bytes route of ``httk serve combined``: registered ``files`` entries only."""

import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from httk.core import FileRecord
from httk.store import EntryStore
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.routing import Route

# A plain ``type/subtype`` media type of RFC 6838 token characters, no parameters.
_MEDIA_TYPE = re.compile(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+")


def _not_found(detail: str = "Not Found") -> Response:
    return PlainTextResponse(detail, status_code=404, headers={"X-Content-Type-Options": "nosniff"})


def _bounded(name: str, limit: int = 255) -> str:
    """Shorten a download file name to ``limit`` characters, keeping a short extension."""
    if len(name) <= limit:
        return name
    suffix = PurePosixPath(name).suffix
    suffix = suffix if len(suffix) <= 16 else ""
    return name[: limit - len(suffix)] + suffix


def _directory(value: str | os.PathLike[str], name: str | None = None) -> Path:
    where = "data_path" if name is None else f"data_path for workspace {name!r}"
    if not os.fspath(value):
        raise ValueError(f"{where} must name a directory")
    directory = Path(value).resolve()
    if not directory.is_dir():
        raise ValueError(f"{where} is not a directory: {value}")
    return directory


def create_files_app(
    store: EntryStore, data_path: str | os.PathLike[str] | Mapping[str, str | os.PathLike[str]]
) -> Starlette:
    """Create the application serving the bytes of registered stored files.

    Mounted at ``/data``, a request for ``<name>/<rest>`` is answered only
    when some stored :class:`~httk.core.FileRecord` has the ``url``
    ``data/<name>/<rest>`` (any revision), where ``<name>`` is the workspace
    name. The bytes come from ``data_path/<name>/<rest>``, or, when
    ``data_path`` maps workspace names to directories, from
    ``data_path[<name>]/<rest>`` (an unmapped name is not served).
    Intermediate symbolic links are followed, since workspace placements may
    live elsewhere; the final component must be a regular file, not a
    symbolic link, and only registered URLs whose recorded size matches are
    served. Every other request is answered with 404, so files that no entry
    registers are never served. ``GET`` and ``HEAD`` with byte ranges are
    supported. The window between the symbolic-link check and opening the
    file is accepted: the directories are operator-owned.

    :param store: Entry store holding the ``files`` entries, owned by the caller.
    :param data_path: Directory holding one subdirectory per workspace name,
        or a mapping from workspace names to their directories.
    :return: Application to mount at ``/data``.
    :raises ValueError: If a directory is empty or does not exist, or a
        mapping key is not a single path component.
    """

    base: Path | None = None
    roots: dict[str, Path] = {}
    if isinstance(data_path, Mapping):
        for name in data_path:
            if not isinstance(name, str) or name in ("", ".", "..") or any(c in name for c in "/\\\x00"):
                raise ValueError(f"data_path workspace name must be a single path component: {name!r}")
        roots = {name: _directory(directory, name) for name, directory in data_path.items()}
    else:
        base = _directory(data_path)

    def locate(path: str) -> Path | None:
        if base is not None:
            return base / path
        name, _separator, rest = path.partition("/")
        root = roots.get(name)
        return root / rest if root is not None and rest else None

    # The store lookup runs synchronously in the event loop, as the OPTIMADE
    # application's queries do.
    async def serve_file(request: Request) -> Response:
        path: str = request.path_params["path"]
        if "\\" in path or "\x00" in path or any(segment in ("", ".", "..") for segment in path.split("/")):
            return _not_found()
        search = store.searcher()
        record = search.variable(FileRecord)
        search.add(record.url == "data/" + path)
        records = [row.file for row in search.results(file=record)]
        if not records:
            return _not_found()
        target = locate(path)
        if target is None:
            return _not_found()
        try:
            if stat.S_ISLNK(os.lstat(target).st_mode):
                return _not_found("file changed or missing")
            status = os.stat(target)
        except OSError:
            return _not_found("file changed or missing")
        if not stat.S_ISREG(status.st_mode):
            return _not_found("file changed or missing")
        found = next((item for item in records if item.size is None or item.size == status.st_size), None)
        if found is None:
            return _not_found("file changed or missing")
        media_type = found.media_type if _MEDIA_TYPE.fullmatch(found.media_type or "") else "application/octet-stream"
        return FileResponse(
            target,
            media_type=media_type,
            filename=_bounded(found.name or PurePosixPath(path).name),
            stat_result=status,
            content_disposition_type="attachment",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    return Starlette(routes=[Route("/{path:path}", serve_file, methods=["GET", "HEAD"])])
