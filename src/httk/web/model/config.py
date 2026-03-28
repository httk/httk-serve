from dataclasses import dataclass
from pathlib import Path
from typing import Self


@dataclass(frozen=True)
class SiteConfig:
    srcdir: Path
    content_subdir: str = "content"
    static_subdir: str = "static"
    template_subdir: str = "templates"
    functions_subdir: str = "functions"
    baseurl: str | None = None
    host_static: str | None = None
    compatibility_mode: bool = False
    config_name: str = "config"
    publish_use_urls_without_ext: bool = True

    @classmethod
    def from_srcdir(
        cls,
        srcdir: str | Path,
        *,
        baseurl: str | None = None,
        host_static: str | None = None,
        compatibility_mode: bool = False,
        config_name: str = "config",
        publish_use_urls_without_ext: bool = True,
    ) -> Self:
        return cls(
            srcdir=Path(srcdir).resolve(),
            baseurl=baseurl,
            host_static=host_static,
            compatibility_mode=compatibility_mode,
            config_name=config_name,
            publish_use_urls_without_ext=publish_use_urls_without_ext,
        )

    @property
    def content_dir(self) -> Path:
        return self.srcdir / self.content_subdir

    @property
    def static_dir(self) -> Path:
        return self.srcdir / self.static_subdir

    @property
    def template_dir(self) -> Path:
        return self.srcdir / self.template_subdir

    @property
    def functions_dir(self) -> Path:
        primary = self.srcdir / self.functions_subdir
        if primary.exists():
            return primary
        if self.compatibility_mode:
            legacy = self.srcdir / "_functions"
            if legacy.exists():
                return legacy
        return primary
