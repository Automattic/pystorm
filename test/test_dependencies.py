"""
Enforces the one-runtime-dependency constraint structurally.
"""

import pathlib

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
