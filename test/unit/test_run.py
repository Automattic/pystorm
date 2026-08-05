"""
Tests for the pystorm_a8c_run worker entry point.
"""

import os
import subprocess
import sys

import pytest

from pystorm_a8c import run

# Writes a sentinel next to the CWD so the test can prove `run()` was reached
# through the real import path, not just that the module was importable.
#
# Built from a list rather than a dedented triple-quoted literal: black
# reformats `textwrap.dedent('''...''')` differently depending on the
# interpreter it runs under, so that spelling made `make lint` pass on 3.9 and
# fail on 3.12+, or the reverse, with nothing in the repo having changed.
TARGET_SRC = "\n".join(
    [
        '"""Stand-in for a component module shipped inside a topology JAR."""',
        "",
        "import pathlib",
        "",
        "",
        "class RunTarget:",
        "    def run(self):",
        '        pathlib.Path("ran.txt").write_text("ok")',
        "",
    ]
)


@pytest.fixture
def worker_dir(tmp_path, monkeypatch):
    """A directory laid out the way Storm unpacks a topology JAR."""
    pkg = tmp_path / "resources" / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "target.py").write_text(TARGET_SRC)
    monkeypatch.chdir(tmp_path)
    # sys.path and the module cache are process-global; main() appends to both.
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "mypkg", raising=False)
    monkeypatch.delitem(sys.modules, "mypkg.target", raising=False)
    return tmp_path


def test_runs_a_component_from_the_resources_directory(worker_dir, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pystorm_a8c_run", "mypkg.target.RunTarget"])
    run.main()
    assert (worker_dir / "ran.txt").read_text() == "ok"


def test_storm_sends_the_whole_command_as_one_argument(worker_dir, monkeypatch):
    # Storm passes execution_command and script as a single string, so argv
    # arrives as one blob that main() has to split itself.
    monkeypatch.setattr(sys, "argv", ["pystorm_a8c_run", "mypkg.target.RunTarget "])
    run.main()
    assert (worker_dir / "ran.txt").read_text() == "ok"


def test_the_worker_directory_and_its_resources_are_both_added(worker_dir, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pystorm_a8c_run", "mypkg.target.RunTarget"])
    before = list(sys.path)
    run.main()
    added = [p for p in sys.path if p not in before]
    assert added == [str(worker_dir), os.path.join(str(worker_dir), "resources")]


@pytest.fixture
def resources_cwd(tmp_path, monkeypatch):
    """The layout a real Storm 1.2.3 supervisor produces.

    Storm starts the multi-lang subprocess with its working directory set to
    ``stormdist/<topology-id>/resources`` -- the component packages are *in*
    the cwd, not in a ``resources`` subdirectory of it. Verified against a
    live 1.2.3 cluster.
    """
    pkg = tmp_path / "resources" / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "target.py").write_text(TARGET_SRC)
    monkeypatch.chdir(tmp_path / "resources")
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "mypkg", raising=False)
    monkeypatch.delitem(sys.modules, "mypkg.target", raising=False)
    return tmp_path / "resources"


def test_runs_a_component_when_storm_starts_us_inside_resources(
    resources_cwd, monkeypatch
):
    """Regression: appending only ``cwd/resources`` finds nothing here.

    This is the layout every 1.2.3 worker actually gets, and it is the one
    that has to work -- a bad path here is a ModuleNotFoundError at startup
    on every component of every topology.
    """
    monkeypatch.setattr(sys, "argv", ["pystorm_a8c_run", "mypkg.target.RunTarget"])
    run.main()
    assert (resources_cwd / "ran.txt").read_text() == "ok"


def test_a_nonexistent_resources_subdirectory_is_not_added(resources_cwd, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pystorm_a8c_run", "mypkg.target.RunTarget"])
    before = list(sys.path)
    run.main()
    added = [p for p in sys.path if p not in before]
    assert added == [str(resources_cwd)]


def test_missing_component_module_fails_loudly(worker_dir, monkeypatch):
    # A typo in a spec's script must not be swallowed into a silent no-op.
    monkeypatch.setattr(sys, "argv", ["pystorm_a8c_run", "mypkg.nope.RunTarget"])
    with pytest.raises(ModuleNotFoundError):
        run.main()


def test_serializer_option_is_gone(worker_dir, monkeypatch):
    # JSON is the only protocol; the flag must not silently accept and ignore.
    monkeypatch.setattr(
        sys,
        "argv",
        ["pystorm_a8c_run", "mypkg.target.RunTarget --serializer=msgpack"],
    )
    with pytest.raises(SystemExit):
        run.main()


def test_console_script_is_installed_and_runnable():
    """The name Storm invokes on the worker must exist on PATH."""
    result = subprocess.run(
        ["pystorm_a8c_run", "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "pystorm_a8c_run" in result.stdout
