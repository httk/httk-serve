"""The file-bytes route of ``httk serve combined``: registered ``files`` entries only."""

import os
import re
import stat
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


def create_files_app(store: EntryStore, files_dir: str | os.PathLike[str]) -> Starlette:
    """Create the application serving the bytes of registered stored files.

    Mounted at ``/data``, a request for ``<path>`` is answered only when some
    stored :class:`~httk.core.FileRecord` has the ``url`` ``data/<path>``
    (any revision); the bytes come from ``files_dir/<path>``, whose final
    component must be a regular file, not a symbolic link, of the recorded
    size. Every other request is answered with 404, so files under
    ``files_dir`` that no entry registers are never served. ``GET`` and
    ``HEAD`` with byte ranges are supported. The window between the
    symbolic-link check and opening the file is accepted: ``files_dir`` is
    operator-owned.

    :param store: Entry store holding the ``files`` entries, owned by the caller.
    :param files_dir: Directory that the leading ``data/`` of stored URLs names.
    :return: Application to mount at ``/data``.
    """

    root = Path(files_dir).resolve()

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
        target = root / path
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
