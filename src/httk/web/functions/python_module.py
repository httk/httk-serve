import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any


class PythonFunctionHandler:
    def __init__(self, functions_dir: Path) -> None:
        self.functions_dir = functions_dir
        self._module_cache: dict[Path, ModuleType] = {}

    def execute(self, *, function_name: str, query: dict[str, str], global_data: dict[str, object]) -> Any:
        module_path = self._resolve_function_path(function_name)
        module = self._load_module(module_path)

        execute_fn = getattr(module, "execute", None)
        if execute_fn is None or not callable(execute_fn):
            raise ValueError(f"Function module missing callable execute(): {module_path}")

        callargs: dict[str, object] = dict(query)
        callargs["global_data"] = global_data
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

        digest = hashlib.sha256(str(module_path).encode("utf-8")).hexdigest()[:16]
        module_name = f"httk_web_userfunc_{digest}"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module spec for {module_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._module_cache[module_path] = module
        return module
