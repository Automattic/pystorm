"""
Tests for the argparse root that `pystorm-a8c` dispatches through.
"""

import subprocess

import pytest


def subparsers_action(parser):
    actions = [
        a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"
    ]
    assert len(actions) == 1
    return actions[0]


def test_only_two_subcommands_are_registered():
    from pystorm_a8c.cli import build_parser

    assert sorted(subparsers_action(build_parser()).choices) == ["jar", "submit"]


def test_the_nine_dropped_subcommands_stay_dropped():
    """sparse auto-discovered its subcommands, which is how these persisted."""
    from pystorm_a8c.cli import build_parser

    choices = subparsers_action(build_parser()).choices
    for gone in (
        "run",
        "visualize",
        "quickstart",
        "tail",
        "remove_logs",
        "update_virtualenv",
        "list",
        "kill",
        "restart",
        "attach",
        "stats",
        "worker_uptime",
    ):
        assert gone not in choices


def test_a_bare_invocation_prints_help_and_fails(capsys):
    from pystorm_a8c.cli import main

    assert main([]) == 1
    assert "usage:" in capsys.readouterr().out


def test_version_flag_reports_the_package_version(capsys):
    from pystorm_a8c import __version__
    from pystorm_a8c.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_the_console_script_is_installed():
    """The name the Makefile and deploy script will call must be on PATH."""
    result = subprocess.run(["pystorm-a8c", "--help"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "submit" in result.stdout
    assert "jar" in result.stdout


def test_no_lein_root_guard_anywhere():
    """There is no JVM build step, so root is unremarkable."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "pystorm_a8c"
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if "LEIN_ROOT" in p.read_text() or "lein " in p.read_text()
    ]
    assert offenders == []
