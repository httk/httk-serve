import argparse
from collections.abc import Callable
from pathlib import Path

import pytest
from httk.core.cli import CLIContext

from httk.serve import cli
from httk.serve.cli import command


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
    assert command(["--site", str(src), "--check"], context) == 0
    assert "valid" in capsys.readouterr().out
    assert command(["--site", str(src), "--list-widgets"], context) == 0
    output = capsys.readouterr().out
    assert "httk.text" in output
    assert "httk.serve.table" in output
    assert "site.hello" in output


def test_check_rejects_reserved_runtime_route_collisions(tmp_path: Path, capsys) -> None:
    src = _src(tmp_path)
    (src / "static" / "_httk").mkdir()
    context = CLIContext("httk", tmp_path)

    assert command(["--site", str(src), "--check"], context) == 1
    assert "Reserved httk-serve route collision" in capsys.readouterr().err


def test_web_registry_registers_lazy_umbrella_command() -> None:
    from httk.core.register import cli_command

    import httk.registry.cli.serve  # noqa: F401

    registered = cli_command("serve")
    assert registered is not None
    assert registered.handler == "httk.serve.cli:command"


def test_umbrella_cli_dispatches_web_list(tmp_path: Path, capsys) -> None:
    from httk.core.cli import main

    assert main(["serve", "--site", str(_src(tmp_path)), "--list-widgets"]) == 0
    assert "site.hello" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("argv", "handler"),
    [
        (["db.sqlite"], "combined._serve"),
        (["db.sqlite", "--site", "site"], "combined._serve"),
        (["db.sqlite", "--no-site"], "optimade._serve"),
        (["--site", "site"], "web._serve"),
        (["db.sqlite", "--check"], "combined._check"),
        (["db.sqlite", "--no-site", "--check"], "optimade._check"),
        (["--site", "site", "--check"], "web._check"),
        (["--site", "site", "--list-widgets"], "web._list"),
    ],
)
def test_arguments_select_the_mode_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], handler: str
) -> None:
    called: list[tuple[str, argparse.Namespace]] = []

    def fake(label: str) -> Callable[[argparse.Namespace], int]:
        return lambda arguments: called.append((label, arguments)) or 0

    for module in (cli.combined_cli, cli.optimade_cli, cli.web_cli):
        for name in ("_serve", "_check", "_list"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, fake(f"{module.__name__.split('.')[-2]}.{name}"))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "site").mkdir()

    assert command(argv, CLIContext("httk", tmp_path)) == 0
    ((label, arguments),) = called
    assert label == handler
    # Each handler sees the attributes and defaults of its former subcommand.
    assert (arguments.host, arguments.port, arguments.root_path, arguments.workers) == ("127.0.0.1", 8080, "", 1)
    if handler.startswith("web."):
        assert arguments.srcdir == Path("site") and arguments.config_name == "config"


def test_site_only_serve_passes_site_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[argparse.Namespace] = []
    monkeypatch.setattr(cli.web_cli, "_serve", lambda arguments: called.append(arguments) or 0)
    site = _src(tmp_path)
    argv = ["--site", str(site), "--reload", "--site-config", "alt", "--compatibility-mode", "--port", "9001"]

    assert command(argv, CLIContext("httk", tmp_path)) == 0
    (arguments,) = called
    assert (arguments.srcdir, arguments.reload, arguments.config_name) == (site, True, "alt")
    assert (arguments.compatibility_mode, arguments.host, arguments.port) == (True, "127.0.0.1", 9001)


@pytest.mark.parametrize("mode", [[], ["--check"], ["--list-widgets"], ["db.sqlite"], ["db.sqlite", "--check"]])
def test_site_must_be_a_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str], mode: list[str]) -> None:
    missing = tmp_path / "missing"
    assert command(["--site", str(missing), *mode], CLIContext("httk", tmp_path)) == 2
    assert f"argument --site: not a directory: {missing}" in capsys.readouterr().err


def test_missing_target_is_a_parser_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert command([], CLIContext("httk", tmp_path)) == 2
    assert "STORE" in capsys.readouterr().err
    assert command(["--check"], CLIContext("httk", tmp_path)) == 2


@pytest.mark.parametrize(
    ("argv", "flag"),
    [
        (["db.sqlite", "--reload"], "--reload"),
        (["--site", "site", "--workers", "2"], "--workers"),
        (["--site", "site", "--optimade-config", "o.toml"], "--optimade-config"),
        (["db.sqlite", "--no-site", "--site", "site"], "--site"),
        (["db.sqlite", "--no-site", "--check", "--public-url", "https://x.example"], "--public-url"),
        (["db.sqlite", "--list-widgets"], "--list-widgets"),
    ],
)
def test_options_foreign_to_the_mode_are_parser_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], argv: list[str], flag: str
) -> None:
    assert command(argv, CLIContext("httk", tmp_path)) == 2
    assert f"argument {flag}: not used in" in capsys.readouterr().err
