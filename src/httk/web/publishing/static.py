import shutil
from pathlib import Path

from httk.web.engine.site_engine import SiteEngine
from httk.web.model.page import PublishReport

CONTENT_EXTENSIONS: set[str] = {".md", ".rst", ".html", ".httkweb"}


def publish_site(*, engine: SiteEngine, outdir: str | Path) -> PublishReport:
    output_root = Path(outdir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    written_files: list[Path] = []
    warnings: list[str] = []

    static_dir = engine.config.static_dir
    if static_dir.exists():
        for static_file in static_dir.rglob("*"):
            if not static_file.is_file():
                continue
            rel = static_file.relative_to(static_dir)
            target = output_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(static_file, target)
            written_files.append(target)

    content_dir = engine.config.content_dir
    if content_dir.exists():
        for content_file in content_dir.rglob("*"):
            if not content_file.is_file() or content_file.suffix.lower() not in CONTENT_EXTENSIONS:
                continue

            rel = content_file.relative_to(content_dir)
            route = str(rel.with_suffix(""))
            result = engine.render(route)
            warnings.extend(result.warnings)

            target = output_root / rel.with_suffix(".html")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(result.body)
            written_files.append(target)

    return PublishReport(written_files=written_files, warnings=warnings)
