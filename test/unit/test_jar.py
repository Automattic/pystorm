"""
Tests for the stdlib-zipfile topology JAR builder.
"""

import pathlib
import zipfile

import pytest

from pystorm_a8c.cli.jar import build_jar


@pytest.fixture
def project(tmp_path):
    """A project laid out like casterisk-realtime: src/<pkg> is a SYMLINK."""
    pkg = tmp_path / "casterisk"
    (pkg / "bolts").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "bolts" / "__init__.py").write_text("")
    (pkg / "bolts" / "indexer.py").write_text("class Indexer: pass\n")
    cache = pkg / "__pycache__"
    cache.mkdir()
    (cache / "indexer.cpython-39.pyc").write_bytes(b"\x00")
    src = tmp_path / "src"
    src.mkdir()
    (src / "casterisk").symlink_to(pkg, target_is_directory=True)
    return tmp_path


def test_jar_follows_the_src_symlink(project):
    """The whole payload lives behind a symlink; rglob would miss it."""
    out = build_jar(src_dir=project / "src", output_path=project / "topology.jar")
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert "resources/casterisk/__init__.py" in names
    assert "resources/casterisk/bolts/indexer.py" in names


def test_a_symlink_cycle_terminates_instead_of_recursing(project):
    """followlinks=True has no loop protection of its own."""
    (project / "casterisk" / "loop").symlink_to(project / "src", True)

    out = build_jar(src_dir=project / "src", output_path=project / "topology.jar")

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert "resources/casterisk/bolts/indexer.py" in names
    assert len(names) == len(set(names)), "cycle produced duplicate entries"


def test_two_symlinks_to_one_directory_are_both_packaged(project):
    """Not a cycle: each link is a distinct package name the worker imports.

    An earlier cycle guard pruned by "already visited" rather than "contains
    itself" and silently dropped the second -- a JAR that submits fine and
    then fails at worker start.
    """
    shared = project / "shared"
    shared.mkdir()
    (shared / "m.py").write_text("x = 1\n")
    (project / "src" / "alpha").symlink_to(shared, target_is_directory=True)
    (project / "src" / "beta").symlink_to(shared, target_is_directory=True)

    out = build_jar(src_dir=project / "src", output_path=project / "topology.jar")

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert "resources/alpha/m.py" in names
    assert "resources/beta/m.py" in names


def test_jar_is_never_empty(project):
    """An empty JAR submits fine and fails at worker start -- fail here instead."""
    out = build_jar(src_dir=project / "src", output_path=project / "topology.jar")
    with zipfile.ZipFile(out) as zf:
        assert len(zf.namelist()) > 0


def test_jar_raises_when_there_is_nothing_to_package(tmp_path):
    empty = tmp_path / "src"
    empty.mkdir()
    with pytest.raises(RuntimeError, match="no files"):
        build_jar(src_dir=empty, output_path=tmp_path / "topology.jar")


def test_jar_raises_when_src_is_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_jar(src_dir=tmp_path / "nope", output_path=tmp_path / "topology.jar")


def test_bytecode_and_junk_are_excluded(project):
    out = build_jar(src_dir=project / "src", output_path=project / "topology.jar")
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert not any(n.endswith(".pyc") for n in names)
    assert not any("__pycache__" in n for n in names)


def test_paths_are_posix_and_rooted_at_resources(project):
    """Storm reads these on Linux; a backslash from a Windows build breaks it."""
    out = build_jar(src_dir=project / "src", output_path=project / "topology.jar")
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert all(n.startswith("resources/") for n in names)
    assert not any("\\" in n for n in names)


def test_file_contents_survive_the_round_trip(project):
    out = build_jar(src_dir=project / "src", output_path=project / "topology.jar")
    with zipfile.ZipFile(out) as zf:
        body = zf.read("resources/casterisk/bolts/indexer.py")
    assert body == b"class Indexer: pass\n"


def test_output_is_a_valid_archive(project):
    out = build_jar(src_dir=project / "src", output_path=project / "topology.jar")
    with zipfile.ZipFile(out) as zf:
        assert zf.testzip() is None


def test_jar_is_deterministic(project):
    """Byte-identical rebuilds keep the blobstore digest stable."""
    a = build_jar(src_dir=project / "src", output_path=project / "a.jar")
    b = build_jar(src_dir=project / "src", output_path=project / "b.jar")
    assert pathlib.Path(a).read_bytes() == pathlib.Path(b).read_bytes()


def test_the_output_directory_is_created(tmp_path, project):
    out = build_jar(
        src_dir=project / "src", output_path=project / "nested" / "deep" / "t.jar"
    )
    assert pathlib.Path(out).is_file()


def test_jar_module_imports_only_the_stdlib_it_needs():
    """No subprocess means no lein and no javac -- checked structurally.

    Asserted over the parsed imports rather than the source text, so prose
    in a docstring cannot pass or fail it.
    """
    import ast
    import inspect

    from pystorm_a8c.cli import jar

    imported = set()
    for node in ast.walk(ast.parse(inspect.getsource(jar))):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert imported == {
        "os",
        "pathlib",
        "zipfile",
    }, f"jar.py must build the archive in-process; got imports {sorted(imported)}"
