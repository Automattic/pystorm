import pathlib


def find_facade_bypasses(root):
    """Return modules under `root` that reach past the storm facade.

    The facade is allowed to import an IDL module, and each IDL module names
    its own thriftpy2 module `storm_thrift` -- so both are exempt. Everything
    else, including other modules inside `storm/`, is still checked.
    """
    exempt = {root / "storm" / "__init__.py"}
    exempt |= set((root / "storm").glob("thrift_idl_*.py"))
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if path in exempt:
            continue
        text = path.read_text()
        if "thrift_idl_" in text or "storm_thrift" in text:
            offenders.append(str(path.relative_to(root)))
    return offenders


def test_facade_exports_every_name_the_package_uses():
    from pystorm_a8c import storm

    required = [
        "StormTopology",
        "ThriftBolt",
        "SpoutSpec",
        "ComponentCommon",
        "ComponentObject",
        "ShellComponent",
        "GlobalStreamId",
        "ThriftGrouping",
        "StreamInfo",
        "NullStruct",
        "JavaObject",
        "JavaObjectArg",
        "SubmitOptions",
        "TopologyInitialStatus",
        "KillOptions",
        "Nimbus",
        "AuthorizationException",
        "NotAliveException",
        "AlreadyAliveException",
        "InvalidTopologyException",
    ]
    for name in required:
        assert hasattr(storm, name), f"facade is missing {name}"


def test_global_stream_id_is_hashable_and_stable():
    from pystorm_a8c.storm import GlobalStreamId

    a = GlobalStreamId(componentId="x", streamId="default")
    b = GlobalStreamId(componentId="x", streamId="default")
    assert hash(a) == hash(b)
    assert len({a, b}) == 1


def test_only_the_facade_imports_the_versioned_idl():
    """Storm 2.x must be a one-file swap. Nothing outside the facade may
    reach past it into a version-specific IDL module."""
    root = pathlib.Path(__file__).resolve().parents[2] / "pystorm_a8c"
    assert list((root / "storm").glob("thrift_idl_*.py")), "no versioned IDL found"
    offenders = find_facade_bypasses(root)
    assert offenders == [], f"these modules bypass the storm facade: {offenders}"


def test_the_bypass_scan_catches_a_sibling_in_the_storm_package(tmp_path):
    """Guard the guard: an over-broad exemption would pass vacuously."""
    (tmp_path / "storm").mkdir()
    (tmp_path / "storm" / "__init__.py").write_text("from x.thrift_idl_1x import y\n")
    (tmp_path / "storm" / "thrift_idl_1x.py").write_text("storm_thrift = 1\n")
    # A sibling inside storm/ and a module elsewhere must both be caught.
    (tmp_path / "storm" / "sneaky.py").write_text("from .thrift_idl_1x import z\n")
    (tmp_path / "util.py").write_text("import storm_thrift\n")
    (tmp_path / "innocent.py").write_text("import json\n")

    assert find_facade_bypasses(tmp_path) == ["storm/sneaky.py", "util.py"]
