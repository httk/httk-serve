from pathlib import Path

from httk.web.functions.python_module import PythonFunctionHandler


def test_handler_imports_helper_at_module_load(tmp_path: Path) -> None:
    (tmp_path / "helper.py").write_text(
        """def normalize(name):
    return name.upper()
""",
        encoding="utf-8",
    )
    (tmp_path / "hello.py").write_text(
        """from helper import normalize

def execute(name, global_data, **kwargs):
    return normalize(name)
""",
        encoding="utf-8",
    )

    handler = PythonFunctionHandler(tmp_path)
    result = handler.execute(function_name="hello", params={"name": "rick"}, global_data={})
    assert result == "RICK"


def test_handler_imports_helper_during_execute(tmp_path: Path) -> None:
    (tmp_path / "helper.py").write_text(
        """def normalize(name):
    return name.upper()
""",
        encoding="utf-8",
    )
    (tmp_path / "hello.py").write_text(
        """def execute(name, global_data, **kwargs):
    from helper import normalize
    return normalize(name)
""",
        encoding="utf-8",
    )

    handler = PythonFunctionHandler(tmp_path)
    result = handler.execute(function_name="hello", params={"name": "rick"}, global_data={})
    assert result == "RICK"
