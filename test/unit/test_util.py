"""
Tests for config resolution and the direct Nimbus client.
"""

import json
import logging
import pathlib
from types import SimpleNamespace

import pytest


def write_config(tmp_path, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return str(p)


@pytest.fixture(autouse=True)
def clear_config_memo():
    """`get_config` memoizes the working-directory config.json process-wide."""
    import pystorm_a8c.util as util

    util._config = None
    util._storm_workers.clear()
    yield
    util._config = None
    util._storm_workers.clear()


# ---------------------------------------------------------------- Nimbus


def test_get_nimbus_host_port_splits_host_and_port():
    from pystorm_a8c.util import get_nimbus_host_port

    assert get_nimbus_host_port({"nimbus": "nimbus-host:6627"}) == (
        "nimbus-host",
        6627,
    )


def test_get_nimbus_host_port_defaults_to_6627():
    from pystorm_a8c.util import get_nimbus_host_port

    assert get_nimbus_host_port({"nimbus": "nimbus-host"}) == ("nimbus-host", 6627)


def test_env_var_overrides_config(monkeypatch):
    from pystorm_a8c.util import get_nimbus_host_port

    monkeypatch.setenv("PYSTORM_A8C_NIMBUS", "other:1234")
    assert get_nimbus_host_port({"nimbus": "ignored:6627"}) == ("other", 1234)


def test_missing_nimbus_is_an_error():
    from pystorm_a8c.util import get_nimbus_host_port

    with pytest.raises(ValueError):
        get_nimbus_host_port({})


def test_no_ssh_anywhere_in_the_package():
    """No SSH is a hard constraint, not a preference."""
    root = pathlib.Path(__file__).resolve().parents[2] / "pystorm_a8c"
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text()
        if "ssh_tunnel" in text or "paramiko" in text or "import fabric" in text:
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_use_ssh_for_nimbus_key_is_ignored_but_warns(caplog):
    """Legacy configs may still carry the key; it must not resurrect tunneling.

    Ignoring it silently would let a stale config look like it is tunneling
    when it is really connecting direct, so the connection is made loudly.
    """
    from pystorm_a8c.util import get_nimbus_host_port

    with caplog.at_level(logging.WARNING):
        host, port = get_nimbus_host_port(
            {"nimbus": "h:6627", "use_ssh_for_nimbus": True}
        )
    assert (host, port) == ("h", 6627)
    assert any("use_ssh_for_nimbus" in r.message for r in caplog.records)


def test_use_ssh_for_nimbus_absent_does_not_warn(caplog):
    from pystorm_a8c.util import get_nimbus_host_port

    with caplog.at_level(logging.WARNING):
        get_nimbus_host_port({"nimbus": "h:6627"})
    assert caplog.records == []


def test_get_storm_workers_prefers_configured_list():
    from pystorm_a8c.util import get_storm_workers

    assert get_storm_workers({"nimbus": "h:6627", "workers": ["a", "b"]}) == ["a", "b"]


def test_get_storm_workers_asks_nimbus_when_unconfigured(monkeypatch):
    import pystorm_a8c.util as util

    calls = []

    class FakeClient:
        def getClusterInfo(self):
            calls.append(1)
            return SimpleNamespace(
                supervisors=[SimpleNamespace(host="w1"), SimpleNamespace(host="w2")]
            )

    monkeypatch.setattr(util, "get_nimbus_client", lambda *a, **kw: FakeClient())
    assert util.get_storm_workers({"nimbus": "h:6627"}) == ["w1", "w2"]
    # Memoized per (host, port): a second call must not re-open an RPC client.
    assert util.get_storm_workers({"nimbus": "h:6627"}) == ["w1", "w2"]
    assert len(calls) == 1


def test_nimbus_storm_version_parses_tuple():
    from pystorm_a8c.util import nimbus_storm_version

    class FakeClient:
        def getVersion(self):
            return "1.2.3"  # what storm-ha and the local cluster both report

    assert nimbus_storm_version(FakeClient()) == (1, 2, 3)


def test_nimbus_storm_version_survives_old_nimbus():
    """Storm < 0.10.0 has no getVersion; callers compare, so return a tuple."""
    from pystorm_a8c.util import nimbus_storm_version

    class AncientClient:
        def getVersion(self):
            raise Exception("No such method")

    assert nimbus_storm_version(AncientClient()) < (1, 0, 0)


# ---------------------------------------------------------------- config


def test_get_env_config_returns_name_and_config(tmp_path):
    from pystorm_a8c.util import get_env_config

    cfg = write_config(tmp_path, {"envs": {"storm4": {"nimbus": "h:6627"}}})
    name, env = get_env_config("storm4", config_file=cfg)
    assert name == "storm4"
    assert env["nimbus"] == "h:6627"


def test_get_env_config_autoselects_sole_env(tmp_path):
    from pystorm_a8c.util import get_env_config

    cfg = write_config(tmp_path, {"envs": {"only": {"nimbus": "h:6627"}}})
    assert get_env_config(None, config_file=cfg)[0] == "only"


def test_get_env_config_rejects_unknown_env(tmp_path):
    from pystorm_a8c.util import get_env_config

    cfg = write_config(tmp_path, {"envs": {"a": {}, "b": {}}})
    with pytest.raises(SystemExit):
        get_env_config("nope", config_file=cfg)


def test_get_env_config_rejects_ambiguous_env(tmp_path):
    from pystorm_a8c.util import get_env_config

    cfg = write_config(tmp_path, {"envs": {"a": {}, "b": {}}})
    with pytest.raises(SystemExit):
        get_env_config(None, config_file=cfg)


def test_an_explicit_config_file_is_never_memoized(tmp_path):
    """The memo exists for the CLI's repeated config.json reads, nothing else.

    Upstream memoized unconditionally, so the second caller to pass an explicit
    file got the first caller's config back. That is invisible in a one-shot
    CLI process and lethal anywhere else -- including in this test suite, where
    it silently made every later config test assert against the first fixture.
    """
    from pystorm_a8c.util import get_config

    first = write_config(tmp_path, {"envs": {"a": {"nimbus": "h1"}}})
    second = tmp_path / "other.json"
    second.write_text(json.dumps({"envs": {"b": {"nimbus": "h2"}}}))

    assert list(get_config(config_file=first)["envs"]) == ["a"]
    assert list(get_config(config_file=str(second))["envs"]) == ["b"]


def test_get_config_accepts_an_open_file(tmp_path):
    """Upstream's signature took a file object; keep taking one."""
    from pystorm_a8c.util import get_config

    cfg = write_config(tmp_path, {"envs": {"a": {}}})
    with open(cfg) as fp:
        assert list(get_config(config_file=fp)["envs"]) == ["a"]


def test_an_open_file_can_be_read_more_than_once(tmp_path):
    """`submit_topology` resolves the config three times from one argument.

    Without the memo, a handle read once is exhausted; the second `json.load`
    would raise on an empty string.
    """
    from pystorm_a8c.util import get_config, get_env_config

    cfg = write_config(tmp_path, {"envs": {"a": {"nimbus": "h"}}})
    with open(cfg) as fp:
        assert list(get_config(config_file=fp)["envs"]) == ["a"]
        assert get_env_config(None, config_file=fp)[0] == "a"


def test_get_config_reads_the_working_directory_by_default(tmp_path, monkeypatch):
    from pystorm_a8c.util import get_config

    write_config(tmp_path, {"envs": {"cwd-env": {}}})
    monkeypatch.chdir(tmp_path)
    assert list(get_config()["envs"]) == ["cwd-env"]


def test_missing_config_json_dies(tmp_path, monkeypatch):
    from pystorm_a8c.util import get_config

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        get_config()


def test_get_topology_definition_finds_the_sole_spec(tmp_path, monkeypatch):
    from pystorm_a8c.util import get_topology_definition

    specs = tmp_path / "topologies"
    specs.mkdir()
    (specs / "wordcount.py").write_text("")
    cfg = write_config(tmp_path, {"topology_specs": "topologies"})
    monkeypatch.chdir(tmp_path)

    name, path = get_topology_definition(config_file=cfg)
    assert name == "wordcount"
    assert path.endswith("wordcount.py")


def test_get_topology_definition_rejects_ambiguity(tmp_path, monkeypatch):
    from pystorm_a8c.util import get_topology_definition

    specs = tmp_path / "topologies"
    specs.mkdir()
    (specs / "one.py").write_text("")
    (specs / "two.py").write_text("")
    cfg = write_config(tmp_path, {"topology_specs": "topologies"})
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit):
        get_topology_definition(config_file=cfg)


def test_get_topology_from_file_returns_the_topology_class(tmp_path, monkeypatch):
    from pystorm_a8c.util import get_topology_from_file

    src = tmp_path / "src"
    src.mkdir()
    (src / "tdefs_component.py").write_text(
        "from pystorm_a8c.bolt import Bolt\n"
        "from pystorm_a8c.spout import Spout\n"
        "class W(Spout):\n"
        "    outputs = ['word']\n"
        "class C(Bolt):\n"
        "    outputs = ['word', 'count']\n"
    )
    specs = tmp_path / "topologies"
    specs.mkdir()
    (specs / "tdef.py").write_text(
        "from pystorm_a8c.dsl.topology import Topology\n"
        "from tdefs_component import W, C\n"
        "class WordCount(Topology):\n"
        "    word_spout = W.spec()\n"
        "    count_bolt = C.spec(inputs=[word_spout])\n"
    )
    monkeypatch.setattr("sys.path", list(__import__("sys").path))

    topology_class = get_topology_from_file(str(specs / "tdef.py"))
    assert topology_class.__name__ == "WordCount"
    assert set(topology_class.thrift_bolts) == {"count_bolt"}


# ------------------------------------------------------- serializer knob


def make_topology_class(scripts=("mypkg.mod.MyBolt",)):
    """A stand-in exposing only what `set_topology_serializer` touches."""
    return SimpleNamespace(
        thrift_bolts={
            f"bolt{i}": SimpleNamespace(
                bolt_object=SimpleNamespace(shell=SimpleNamespace(script=s))
            )
            for i, s in enumerate(scripts)
        },
        thrift_spouts={
            "spout": SimpleNamespace(
                spout_object=SimpleNamespace(shell=SimpleNamespace(script="mypkg.S"))
            )
        },
    )


def test_json_serializer_does_not_touch_the_script():
    """casterisk's config.json says `"serializer": "json"`.

    Upstream prepended `-s json ` to every component's script. Task 8 removed
    the `--serializer` flag from `pystorm_a8c_run`, so that prefix now makes
    argparse exit non-zero on every worker -- a config that has been inert for
    years would start breaking deploys. JSON is the only protocol, so the
    script is left exactly as the spec built it.
    """
    from pystorm_a8c.util import set_topology_serializer

    topology_class = make_topology_class()
    set_topology_serializer({}, {"serializer": "json"}, topology_class)
    assert topology_class.thrift_bolts["bolt0"].bolt_object.shell.script == (
        "mypkg.mod.MyBolt"
    )
    assert topology_class.thrift_spouts["spout"].spout_object.shell.script == "mypkg.S"


def test_absent_serializer_does_not_touch_the_script():
    from pystorm_a8c.util import set_topology_serializer

    topology_class = make_topology_class()
    set_topology_serializer({}, {}, topology_class)
    assert topology_class.thrift_bolts["bolt0"].bolt_object.shell.script == (
        "mypkg.mod.MyBolt"
    )


def test_a_non_json_serializer_is_refused_not_ignored():
    """Silently downgrading to JSON would mis-decode every tuple on the wire."""
    from pystorm_a8c.util import set_topology_serializer

    with pytest.raises(SystemExit):
        set_topology_serializer(
            {"serializer": "msgpack"}, {}, make_topology_class()
        )


def test_env_serializer_overrides_the_project_serializer():
    from pystorm_a8c.util import set_topology_serializer

    with pytest.raises(SystemExit):
        set_topology_serializer(
            {"serializer": "msgpack"}, {"serializer": "json"}, make_topology_class()
        )
