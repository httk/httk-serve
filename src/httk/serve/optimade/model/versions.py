"""Expose the supported and default OPTIMADE API versions."""

from typing import Final

optimade_supported_versions: Final[dict[str, str]] = {'v1': '1.3.0', 'v1.3': '1.3.0', 'v1.3.0': '1.3.0'}
optimade_default_version: Final[str] = '1.3.0'
# The major-only versioned base URL segment that unversioned API requests are
# redirected to; the /versions CSV is derived from it.
optimade_default_url_version: Final[str] = 'v1'
