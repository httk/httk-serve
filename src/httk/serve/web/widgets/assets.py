"""Engine-owned registration for trusted widget assets."""

from threading import RLock

from .core import WidgetAsset


class WidgetAssetConflictError(ValueError):
    """Report conflicting declarations for one widget asset path.

    :param path: Conflicting deployment-relative asset path.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"conflicting widget asset declaration for {path!r}")


class WidgetAssetRegistry:
    """Register immutable widget assets for one engine instance.

    Registration is atomic across a batch and rejects conflicting content before
    changing the registry.
    """

    def __init__(self) -> None:
        self._assets: dict[str, WidgetAsset] = {}
        self._lock = RLock()

    def register(self, asset: WidgetAsset) -> WidgetAsset:
        """Register one widget asset.

        :param asset: Asset declaration to register.
        :return: The registered asset.
        :raises WidgetAssetConflictError: If the path has different content.
        """
        return self.register_many((asset,))[0]

    def register_many(self, assets: tuple[WidgetAsset, ...]) -> tuple[WidgetAsset, ...]:
        """Register a batch after preflighting every conflict.

        :param assets: Asset declarations to register.
        :return: Registered assets in input order.
        :raises WidgetAssetConflictError: If any path has different content.
        """

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
        """Look up an asset by its deployment-relative path.

        :param path: Registered asset path.
        :return: Registered asset, or ``None`` when absent.
        """
        with self._lock:
            return self._assets.get(path)
