import shutil
from pathlib import Path

from httk.web.engine.site_engine import SiteEngine
from httk.web.model.page import PageResult, PublishReport
from httk.web.widgets import WidgetAsset

CONTENT_EXTENSIONS: set[str] = {".md", ".rst", ".html", ".httkweb"}


def publish_site(*, engine: SiteEngine, outdir: str | Path) -> PublishReport:
    output_root = Path(outdir).resolve()
    warnings: list[str] = []
    static_files: list[tuple[Path, Path]] = []
    static_file_paths: set[Path] = set()
    static_directory_paths: set[Path] = set()
    pages: list[tuple[Path, PageResult]] = []
    assets: dict[str, WidgetAsset] = {}

    static_dir = engine.config.static_dir
    if static_dir.exists():
        for static_path in sorted(static_dir.rglob("*")):
            rel = static_path.relative_to(static_dir)
            if static_path.is_dir():
                static_directory_paths.add(rel)
            elif static_path.is_file():
                static_files.append((static_path, rel))
                static_file_paths.add(rel)
                static_directory_paths.update(parent for parent in rel.parents if parent != Path("."))

    content_dir = engine.config.content_dir
    if content_dir.exists():
        for content_file in sorted(content_dir.rglob("*")):
            if not content_file.is_file() or content_file.suffix.lower() not in CONTENT_EXTENSIONS:
                continue

            rel = content_file.relative_to(content_dir)
            route = str(rel.with_suffix(""))
            result = engine.render(route)
            warnings.extend(result.warnings)
            pages.append((rel.with_suffix(".html"), result))
            for asset in result.assets:
                assets[asset.path] = asset

    for asset in assets.values():
        asset_rel = Path("_httk") / "assets" / asset.path
        if (
            asset_rel in static_file_paths
            or asset_rel in static_directory_paths
            or any(parent in static_file_paths for parent in asset_rel.parents if parent != Path("."))
        ):
            raise ValueError(f"widget asset collides with site static output: {asset.path}")

    output_root.mkdir(parents=True, exist_ok=True)
    written_files: list[Path] = []
    for source, rel in static_files:
        target = output_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        written_files.append(target)
    for rel, result in pages:
        target = output_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(result.body)
        written_files.append(target)
    for asset in assets.values():
        target = output_root / "_httk" / "assets" / asset.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(asset.content)
        written_files.append(target)

    return PublishReport(written_files=written_files, warnings=warnings)
