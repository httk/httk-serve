# Explicit file maps

Use `create_file_map_app` when an application needs to expose a few known files
without a website source tree:

```python
from pathlib import Path
from httk.serve.http import create_file_map_app

app = create_file_map_app({
    "/dataset.csv": Path("dataset.csv"),
    "/metadata.json": Path("metadata.json"),
})
```

Only the exact declared URL paths are routed. Canonical root-relative paths are
required, so traversal declarations are rejected. Starlette `FileResponse`
provides GET/HEAD, ranges, ETag, modification time, content length, conditional
responses, and normal MIME inference. A new response is built for each request:
replaced files and current metadata are visible immediately, while missing
files return 404.

The helper has no DSP metadata or access-control behavior. Mount it alongside
a DSP application when the publication declarations and file paths have been
aligned by the host application.
