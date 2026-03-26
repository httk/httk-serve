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
    compatibility_mode: bool = False

    @classmethod
    def from_srcdir(
        cls,
        srcdir: str | Path,
        *,
        baseurl: str | None = None,
        compatibility_mode: bool = False,
    ) -> Self:
        return cls(srcdir=Path(srcdir).resolve(), baseurl=baseurl, compatibility_mode=compatibility_mode)

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
        return self.srcdir / self.functions_subdir
