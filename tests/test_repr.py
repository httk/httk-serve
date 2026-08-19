"""Repr policy: serve service handles render an informative class-named repr."""

from types import SimpleNamespace

from httk.serve.optimade.client import OptimadeStore
from httk.serve.web.engine.site_engine import SiteEngine


def _assert_informative(text: str, class_name: str) -> None:
    assert text.startswith(f"{class_name}("), text
    assert " object at 0x" not in text, text


def test_site_engine_repr() -> None:
    # A live SiteEngine loads a site source tree; the repr reads only the config
    # srcdir, so exercise it directly.
    stub = SimpleNamespace(config=SimpleNamespace(srcdir="/srv/site"))
    _assert_informative(SiteEngine.__repr__(stub), "SiteEngine")  # type: ignore[arg-type]


def test_optimade_store_repr() -> None:
    stub = SimpleNamespace(base_url="https://example.org/optimade/v1", api_version="1.3.0")
    _assert_informative(OptimadeStore.__repr__(stub), "OptimadeStore")  # type: ignore[arg-type]
