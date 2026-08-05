"""
Enforces the one-runtime-dependency constraint structurally.
"""

import pathlib
import shutil

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9 / 3.10
    import tomli as tomllib


def load_pyproject():
    root = pathlib.Path(__file__).resolve().parents[1]
    return tomllib.loads((root / "pyproject.toml").read_text())


def test_exactly_one_runtime_dependency():
    deps = load_pyproject()["project"]["dependencies"]
    assert len(deps) == 1, f"expected only thriftpy2, got {deps}"
    assert deps[0].startswith("thriftpy2")


def test_build_backend_is_uv_build():
    # The whole toolchain is one binary; a stray hatchling/setuptools
    # reintroduction would add a build-time dependency tree.
    assert load_pyproject()["build-system"]["build-backend"] == "uv_build"


def test_version_is_static_not_dynamic():
    # uv_build rejects dynamic versions; __init__.py derives from metadata.
    data = load_pyproject()
    assert "version" in data["project"]
    assert "version" not in data["project"].get("dynamic", [])


def test_the_shipped_module_is_declared():
    """A module missing from `module-name` is simply absent from the wheel.

    One entry, not two: the deprecated `streamparse` shim was deleted, so
    `import streamparse` now raises ImportError rather than warning.
    """
    module_names = load_pyproject()["tool"]["uv"]["build-backend"]["module-name"]
    assert sorted(module_names) == ["pystorm_a8c"]


def test_streamparse_is_not_importable():
    """The shim is gone; `import streamparse` must fail, not warn.

    Worth asserting rather than assuming. `git rm` deletes tracked files but
    leaves the untracked `__pycache__` beside them, and a directory holding
    only `__pycache__` is still an implicit namespace package -- so
    `streamparse` stayed importable from the repo root after the source files
    were deleted, with `__file__` of None. Anyone who checks out a commit that
    predates the removal, runs the suite, and comes back recreates exactly
    that husk.
    """
    import importlib

    try:
        module = importlib.import_module("streamparse")
    except ImportError:
        return
    raise AssertionError(
        f"streamparse is importable from {list(getattr(module, '__path__', []))!r} "
        f"(__file__={getattr(module, '__file__', None)!r}). If __file__ is None "
        f"this is a leftover directory acting as a namespace package -- delete it."
    )


def test_the_built_wheel_ships_exactly_the_declared_modules():
    """Build a wheel and look inside, rather than trusting pyproject.toml.

    `module-name` says what *should* ship; only the artifact says what does.
    The build backend floats across a whole pre-1.0 range
    (``uv_build>=0.9,<1``) and this project does not use the default layout --
    ``module-root = ""`` with a list-valued ``module-name`` -- so a backend
    change could alter the top-level set with every other test still green.
    """
    import subprocess
    import tempfile
    import zipfile

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH; it is what builds the wheel")

    repo = pathlib.Path(__file__).resolve().parents[1]
    declared = set(load_pyproject()["tool"]["uv"]["build-backend"]["module-name"])

    with tempfile.TemporaryDirectory() as out:
        subprocess.run(
            [uv, "build", "--wheel", "--out-dir", out],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        wheels = list(pathlib.Path(out).glob("*.whl"))
        assert len(wheels) == 1, f"expected one wheel, got {wheels}"
        with zipfile.ZipFile(wheels[0]) as zf:
            tops = {name.split("/")[0] for name in zf.namelist()}

    shipped = {t for t in tops if not t.endswith(".dist-info")}
    assert shipped == declared, (
        f"wheel ships {sorted(shipped)} but module-name declares " f"{sorted(declared)}"
    )


def test_no_banned_imports_anywhere():
    banned = [
        "six",
        "simplejson",
        "fabric",
        "paramiko",
        "ruamel",
        "texttable",
        "jinja2",
        "requests",
        "pkg_resources",
        "msgpack",
    ]
    repo = pathlib.Path(__file__).resolve().parents[1]
    sources = list((repo / "pystorm_a8c").rglob("*.py"))
    assert sources, "found no modules to scan -- wrong path, test is vacuous"

    offenders = []
    for path in sources:
        text = path.read_text()
        for name in banned:
            if f"import {name}" in text or f"from {name}" in text:
                offenders.append(f"{path.relative_to(repo)}: {name}")
    assert offenders == [], f"banned imports found: {offenders}"
