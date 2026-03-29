import hashlib
import importlib.util
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any


class PythonFunctionHandler:
    def __init__(self, functions_dir: Path) -> None:
        self.functions_dir = functions_dir
        self._module_cache: dict[Path, ModuleType] = {}
        self._cache_lock = threading.Lock()
        self._sys_path_lock = threading.Lock()
        self._sys_path_refcount: dict[str, int] = {}

    def execute(self, *, function_name: str, params: dict[str, str], global_data: dict[str, object]) -> Any:
        module_path = self._resolve_function_path(function_name)
        module = self._load_module(module_path)

        execute_fn = getattr(module, "execute", None)
        if execute_fn is None or not callable(execute_fn):
            raise ValueError(f"Function module missing callable execute(): {module_path}")

        callargs: dict[str, object] = dict(params)
        callargs["global_data"] = global_data
        with self._function_import_paths(module_path):
            return execute_fn(**callargs)

    def _resolve_function_path(self, function_name: str) -> Path:
        candidate = function_name.strip()
        if not candidate:
            raise ValueError("Function name cannot be empty")

        rel = Path(candidate)
        if rel.is_absolute():
            raise ValueError(f"Function path must be relative: {function_name}")

        with_suffix = rel if rel.suffix == ".py" else rel.with_suffix(".py")
        resolved = (self.functions_dir / with_suffix).resolve(strict=False)

        try:
            resolved.relative_to(self.functions_dir.resolve(strict=False))
        except ValueError as exc:
            raise ValueError(f"Function path escapes functions directory: {function_name}") from exc

        if not resolved.exists() or not resolved.is_file():
            raise FileNotFoundError(f"Function module not found: {resolved}")

        return resolved

    def _load_module(self, module_path: Path) -> ModuleType:
        cached = self._module_cache.get(module_path)
        if cached is not None:
            return cached

        with self._cache_lock:
            cached = self._module_cache.get(module_path)
            if cached is not None:
                return cached

            digest = hashlib.sha256(str(module_path).encode("utf-8")).hexdigest()[:16]
            module_name = f"httk_web_userfunc_{digest}"
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot load module spec for {module_path}")

            module = importlib.util.module_from_spec(spec)
            with self._function_import_paths(module_path):
                spec.loader.exec_module(module)
            self._module_cache[module_path] = module
            return module

    @contextmanager
    def _function_import_paths(self, module_path: Path):
        paths: list[str] = []
        for path in (self.functions_dir.resolve(strict=False), module_path.parent.resolve(strict=False)):
            path_str = str(path)
            if path_str in paths:
                continue
            paths.append(path_str)

        for path in paths:
            self._acquire_sys_path(path)
        try:
            yield
        finally:
            for path in reversed(paths):
                self._release_sys_path(path)

    def _acquire_sys_path(self, path: str) -> None:
        with self._sys_path_lock:
            current = self._sys_path_refcount.get(path, 0)
            if current == 0:
                sys.path.insert(0, path)
            self._sys_path_refcount[path] = current + 1

    def _release_sys_path(self, path: str) -> None:
        with self._sys_path_lock:
            current = self._sys_path_refcount.get(path, 0)
            if current <= 1:
                self._sys_path_refcount.pop(path, None)
                try:
                    sys.path.remove(path)
                except ValueError:
                    pass
                return
            self._sys_path_refcount[path] = current - 1
