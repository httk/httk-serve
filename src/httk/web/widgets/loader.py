"""Safe loading and deterministic discovery of trusted site-local widgets."""

import hashlib
import importlib.util
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import cast

from .core import FunctionWidget, Widget, WidgetRenderer


class SiteWidgetLoader:
    """Load ``src/widgets/<name>.py`` without allowing paths to escape it."""

    def __init__(self, widgets_dir: Path) -> None:
        self.widgets_dir = widgets_dir
        self._cache: dict[Path, Widget] = {}
        self._cache_lock = threading.Lock()
        self._sys_path_lock = threading.Lock()

    def available(self) -> list[tuple[str, str]]:
        if not self.widgets_dir.exists():
            return []
        items: list[tuple[str, str]] = []
        for path in sorted(self.widgets_dir.glob("*.py")):
            if path.name == "__init__.py":
                continue
            if self._valid_name(path.stem):
                items.append((f"site.{path.stem}", str(path)))
        return items

    def resolve(self, name: str) -> Widget | None:
        if not name.startswith("site."):
            return None
        local_name = name.removeprefix("site.")
        if not self._valid_name(local_name):
            return None
        path = self._resolve_path(local_name)
        if not path.exists() or not path.is_file():
            return None
        return self._load(path, name)

    def _resolve_path(self, name: str) -> Path:
        path = (self.widgets_dir / f"{name}.py").resolve(strict=False)
        try:
            path.relative_to(self.widgets_dir.resolve(strict=False))
        except ValueError as exc:
            raise ValueError(f"Widget path escapes widgets directory: {name}") from exc
        return path

    def _load(self, path: Path, name: str) -> Widget:
        cached = self._cache.get(path)
        if cached is not None:
            return cached
        with self._cache_lock:
            cached = self._cache.get(path)
            if cached is not None:
                return cached
            module = self._load_module(path)
            definition = getattr(module, "widget", None)
            if definition is None:
                render = getattr(module, "render", None)
                if not callable(render):
                    raise ValueError(f"Widget module needs callable render(context, **props): {path}")
                definition = FunctionWidget(name=name, render_function=cast(WidgetRenderer, render), source=str(path))
            if not hasattr(definition, "render") or not callable(definition.render):
                raise ValueError(f"Widget definition has no callable render(): {path}")
            declared_name = getattr(definition, "name", name)
            if declared_name in {"", None}:
                definition = FunctionWidget(
                    name=name, render_function=cast(WidgetRenderer, definition.render), source=str(path)
                )
            elif declared_name != name:
                raise ValueError(f"Site widget name must be {name!r}, not {declared_name!r}: {path}")
            widget = cast(Widget, definition)
            self._cache[path] = widget
            return widget

    def _load_module(self, path: Path) -> ModuleType:
        digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
        spec = importlib.util.spec_from_file_location(f"httk_web_widget_{digest}", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load widget module: {path}")
        module = importlib.util.module_from_spec(spec)
        with self._import_path(path):
            spec.loader.exec_module(module)
        return module

    @contextmanager
    def _import_path(self, path: Path):
        paths = [str(self.widgets_dir.resolve()), str(path.parent.resolve())]
        with self._sys_path_lock:
            added: list[str] = []
            for item in reversed(paths):
                if item not in sys.path:
                    sys.path.insert(0, item)
                    added.append(item)
            try:
                yield
            finally:
                for item in added:
                    sys.path.remove(item)

    @staticmethod
    def _valid_name(name: str) -> bool:
        return (
            bool(name)
            and all(c.isascii() and (c.islower() or c.isdigit() or c in {"_", "-"}) for c in name)
            and name[0].islower()
        )
