"""Engine-owned registration for trusted widget assets."""

from threading import RLock

from .core import WidgetAsset


class WidgetAssetConflictError(ValueError):
    """A path was declared with different asset content or content type."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"conflicting widget asset declaration for {path!r}")


class WidgetAssetRegistry:
    """Thread-safe, per-engine registry with deterministic conflict handling."""

    def __init__(self) -> None:
        self._assets: dict[str, WidgetAsset] = {}
        self._lock = RLock()

    def register(self, asset: WidgetAsset) -> WidgetAsset:
        return self.register_many((asset,))[0]

    def register_many(self, assets: tuple[WidgetAsset, ...]) -> tuple[WidgetAsset, ...]:
        """Atomically register declarations after preflighting every conflict."""

        with self._lock:
            declarations: dict[str, WidgetAsset] = {}
            for asset in assets:
                local = declarations.get(asset.path)
                if local is not None and local != asset:
                    raise WidgetAssetConflictError(asset.path)
                declarations[asset.path] = asset
            for path, asset in declarations.items():
                existing = self._assets.get(path)
                if existing is not None and existing != asset:
                    raise WidgetAssetConflictError(path)
            self._assets.update({path: asset for path, asset in declarations.items() if path not in self._assets})
            return tuple(self._assets[asset.path] for asset in assets)

    def get(self, path: str) -> WidgetAsset | None:
        with self._lock:
            return self._assets.get(path)
