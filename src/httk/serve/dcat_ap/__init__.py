"""Serve DCAT-AP minimal public catalogues."""

from .api import DCAT_AP_MINIMAL_DISCOVERY_PATH, DISCOVERY_VERSION, create_dcat_ap_app

__all__ = ["DCAT_AP_MINIMAL_DISCOVERY_PATH", "DISCOVERY_VERSION", "create_dcat_ap_app"]
