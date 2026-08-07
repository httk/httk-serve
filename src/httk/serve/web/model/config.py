"""Define immutable configuration for a web site source tree."""

from dataclasses import dataclass
from pathlib import Path
from typing import Self


@dataclass(frozen=True)
class SiteConfig:
    """Describe the directories and URL policy used by a site engine.

    :param srcdir: Absolute or relative site source directory.
    :param content_subdir: Content directory name relative to ``srcdir``.
    :param static_subdir: Static directory name relative to ``srcdir``.
    :param template_subdir: Template directory name relative to ``srcdir``.
    :param functions_subdir: Function directory name relative to ``srcdir``.
    :param widgets_subdir: Widget directory name relative to ``srcdir``.
    :param baseurl: Optional site base URL.
    :param host_static: Optional host URL for static assets.
    :param compatibility_mode: Whether to use legacy site conventions.
    :param config_name: Configuration module name.
    :param publish_use_urls_without_ext: Whether published page links omit extensions.
    """

    srcdir: Path
    content_subdir: str = "content"
    static_subdir: str = "static"
    template_subdir: str = "templates"
    functions_subdir: str = "functions"
    widgets_subdir: str = "widgets"
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
        """Build site configuration from a source directory.

        :param srcdir: Site source directory.
        :param baseurl: Optional site base URL.
        :param host_static: Optional host URL for static assets.
        :param compatibility_mode: Whether to use legacy site conventions.
        :param config_name: Configuration module name.
        :param publish_use_urls_without_ext: Whether published page links omit extensions.
        :return: Immutable site configuration.
        """
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
        """Return the configured content directory."""

        return self.srcdir / self.content_subdir

    @property
    def static_dir(self) -> Path:
        """Return the configured static directory."""

        return self.srcdir / self.static_subdir

    @property
    def template_dir(self) -> Path:
        """Return the configured template directory."""

        return self.srcdir / self.template_subdir

    @property
    def functions_dir(self) -> Path:
        """Return the active site function directory."""

        primary = self.srcdir / self.functions_subdir
        if primary.exists():
            return primary
        if self.compatibility_mode:
            legacy = self.srcdir / "_functions"
            if legacy.exists():
                return legacy
        return primary

    @property
    def widgets_dir(self) -> Path:
        """Return the configured widget directory."""

        return self.srcdir / self.widgets_subdir
