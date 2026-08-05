"""The compat shim exists to be deleted. These tests pin its contract until then."""

import subprocess
import sys
import warnings

import pytest

# Every symbol casterisk-realtime imported from streamparse before the rename,
# gathered by grepping the repo. If this list shrinks, the shim can shrink too.
# Plus BatchingBolt, which casterisk never imported but upstream streamparse
# did export from its root -- since Task 4 keeps the class, the shim covers it
# so `streamparse.*` stays a straight alias rather than a narrowed one.
TOP_LEVEL = [
    "BatchingBolt",
    "Bolt",
    "TicklessBatchingBolt",
    "Spout",
    "ReliableSpout",
    "Topology",
    "Stream",
    "Grouping",
    "Tuple",
]


def _import_in_subprocess(statement):
    """Import in a clean interpreter: warnings fire once per process."""
    code = (
        "import warnings\n"
        "warnings.simplefilter('always')\n"
        "with warnings.catch_warnings(record=True) as w:\n"
        f"    {statement}\n"
        "print(len([x for x in w if issubclass(x.category, DeprecationWarning)]))\n"
        "print('\\n'.join(str(x.message) for x in w))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return int(out[0]), out[1:]


@pytest.mark.parametrize("name", TOP_LEVEL)
def test_top_level_symbol_is_importable_and_warns_once(name):
    count, messages = _import_in_subprocess(f"from streamparse import {name}")
    assert count == 1, f"expected exactly one DeprecationWarning, got {count}"
    assert f"streamparse.{name}" in messages[0]
    assert "pystorm_a8c" in messages[0]


@pytest.mark.parametrize(
    "statement,expected",
    [
        ("from streamparse.bolt import Bolt, TicklessBatchingBolt", "streamparse.bolt"),
        ("from streamparse.spout import ReliableSpout", "streamparse.spout"),
        (
            "from streamparse.util import get_env_config, get_storm_workers",
            "streamparse.util",
        ),
        ("import streamparse.util", "streamparse.util"),
        ("from streamparse.version import __version__", "streamparse.version"),
    ],
)
def test_submodule_imports_warn_once(statement, expected):
    count, messages = _import_in_subprocess(statement)
    assert count == 1, f"{statement!r} produced {count} warnings, expected 1"
    assert expected in messages[0]


@pytest.mark.parametrize("name", TOP_LEVEL)
def test_shim_returns_the_same_object_as_the_real_module(name):
    import pystorm_a8c

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import streamparse

        assert getattr(streamparse, name) is getattr(pystorm_a8c, name)


def test_unknown_attribute_still_raises_attribute_error():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import streamparse

        with pytest.raises(AttributeError, match="NoSuchThing"):
            streamparse.NoSuchThing


def test_the_new_import_path_does_not_warn():
    count, _ = _import_in_subprocess("from pystorm_a8c.util import get_env_config")
    assert count == 0, "the supported import path must be silent"


def test_shim_is_listed_for_deletion():
    """The TODO is part of the contract -- losing it is how shims become permanent."""
    import pathlib

    src = (
        pathlib.Path(__file__).resolve().parents[2] / "streamparse" / "__init__.py"
    ).read_text()
    assert "TODO" in src
    assert "remove" in src.lower() or "delete" in src.lower()


def test_nothing_inside_pystorm_a8c_imports_the_shim():
    """The shim is one-directional: pystorm_a8c must never depend on it.

    That property is what makes deleting `streamparse/` a safe, self-contained
    change rather than a refactor. Checked on the AST, not on the file text:
    most of `pystorm_a8c` was ported *from* streamparse, so provenance
    docstrings and comments naming it are expected and harmless. Only a real
    import is a defect.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "pystorm_a8c"
    sources = sorted(root.rglob("*.py"))
    assert sources, f"found no modules under {root} -- wrong path, test is vacuous"

    offenders = []
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name == "streamparse" or name.startswith("streamparse."):
                    rel = path.relative_to(root)
                    offenders.append(f"{rel}:{node.lineno}: {name}")

    assert offenders == [], f"pystorm_a8c must not import streamparse: {offenders}"
