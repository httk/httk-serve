from pathlib import Path

from httk.core.cli import CLIContext

from httk.serve.web.cli import command


def _src(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    for name in ("content", "static", "templates", "widgets"):
        (src / name).mkdir(parents=True)
    (src / "content" / "index.md").write_text('{{ widget("site.hello", name="Ada") }}', encoding="utf-8")
    (src / "widgets" / "hello.py").write_text("def render(context, name):\n    return name\n", encoding="utf-8")
    return src


def test_check_and_list_commands(tmp_path: Path, capsys) -> None:
    src = _src(tmp_path)
    context = CLIContext("httk", tmp_path)
    assert command(["check", str(src)], context) == 0
    assert "valid" in capsys.readouterr().out
    assert command(["list", str(src)], context) == 0
    output = capsys.readouterr().out
    assert "httk.text" in output
    assert "httk.serve.table" in output
    assert "site.hello" in output


def test_check_rejects_reserved_runtime_route_collisions(tmp_path: Path, capsys) -> None:
    src = _src(tmp_path)
    (src / "static" / "_httk").mkdir()
    context = CLIContext("httk", tmp_path)

    assert command(["check", str(src)], context) == 1
    assert "Reserved httk-serve route collision" in capsys.readouterr().err


def test_web_registry_registers_lazy_umbrella_command() -> None:
    from httk.core.register import cli_command

    import httk.registry.cli.serve  # noqa: F401

    registered = cli_command("serve")
    assert registered is not None
    assert registered.handler == "httk.serve.web.cli:command"


def test_umbrella_cli_dispatches_web_list(tmp_path: Path, capsys) -> None:
    from httk.core.cli import main

    assert main(["serve", "web", "list", str(_src(tmp_path))]) == 0
    assert "site.hello" in capsys.readouterr().out
