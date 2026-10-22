"""Serve an entry store over OPTIMADE together with a browsing web site and its file bytes."""

from .api import create_combined_app

__all__ = ["create_combined_app"]
