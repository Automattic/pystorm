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


def test_get_storm_workers_takes_exactly_one_argument():
    """casterisk-realtime's conftest.py replaces this function.

    A stand-in written against the documented one-argument shape raises
    TypeError the moment we call it with anything else, and that only shows up
    in the consumer's test suite.
    """
    import inspect

    from pystorm_a8c.util import get_storm_workers

    assert list(inspect.signature(get_storm_workers).parameters) == ["env_config"]


def test_a_one_argument_stand_in_survives_a_submit(monkeypatch):
    """The call site must not pass anything the documented shape rejects."""
    from types import SimpleNamespace

    import pystorm_a8c.util as util
    from pystorm_a8c.cli import submit

    monkeypatch.setattr(submit, "get_storm_workers", lambda env_config: ["w1", "w2"])
    monkeypatch.setattr(util, "get_storm_workers", lambda env_config: ["w1", "w2"])

    options = submit.resolve_options(None, {}, SimpleNamespace(config={}))

    # The list itself is not put in the conf -- nothing reads it. Only the
    # count survives, as the worker/acker default.
    assert "storm.workers.list" not in options
    assert options["topology.workers"] == 2


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
    assert len(calls) == 1


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
    with pytest.raises(ValueError, match="nope"):
        get_env_config("nope", config_file=cfg)


def test_get_env_config_rejects_ambiguous_env(tmp_path):
    from pystorm_a8c.util import get_env_config

    cfg = write_config(tmp_path, {"envs": {"a": {}, "b": {}}})
    with pytest.raises(ValueError, match="more than one environment"):
        get_env_config(None, config_file=cfg)


def test_each_call_reads_the_file_it_was_given(tmp_path):
    """Two calls with different files must not return the same config."""
    from pystorm_a8c.util import get_config

    first = write_config(tmp_path, {"envs": {"a": {"nimbus": "h1"}}})
    second = tmp_path / "other.json"
    second.write_text(json.dumps({"envs": {"b": {"nimbus": "h2"}}}))

    assert list(get_config(config_file=first)["envs"]) == ["a"]
    assert list(get_config(config_file=str(second))["envs"]) == ["b"]


def test_get_config_accepts_an_open_file(tmp_path):
    """The signature accepts a file object as well as a path."""
    from pystorm_a8c.util import get_config

    cfg = write_config(tmp_path, {"envs": {"a": {}}})
    with open(cfg) as fp:
        assert list(get_config(config_file=fp)["envs"]) == ["a"]


def test_an_open_file_can_be_read_more_than_once(tmp_path):
    """`submit_topology` resolves the config three times from one argument.

    A handle read once is exhausted, so `get_config` rewinds it; the second
    `json.load` would otherwise raise on an empty string.
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


def test_missing_config_json_raises(tmp_path, monkeypatch):
    from pystorm_a8c.util import get_config

    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
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

    with pytest.raises(ValueError, match="more than one topology definition"):
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

    `pystorm-a8c-run` has no `--serializer` flag, so prefixing the script with
    one would make argparse exit non-zero on every worker. JSON is the only
    protocol, so the script is left exactly as the spec built it.
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

    with pytest.raises(ValueError, match="msgpack"):
        set_topology_serializer({"serializer": "msgpack"}, {}, make_topology_class())


def test_env_serializer_overrides_the_project_serializer():
    from pystorm_a8c.util import set_topology_serializer

    with pytest.raises(ValueError, match="msgpack"):
        set_topology_serializer(
            {"serializer": "msgpack"}, {"serializer": "json"}, make_topology_class()
        )


def _leader_stub(host, port, opened):
    """A make_client stand-in that records every connection it opens."""

    def fake(service, host=None, port=None, **kwargs):
        opened.append((host, port))
        client = SimpleNamespace(closed=False)
        client.getLeader = lambda: SimpleNamespace(
            host=host_of_leader, port=port_of_leader, isLeader=True
        )
        client.close = lambda: setattr(client, "closed", True)
        return client

    host_of_leader, port_of_leader = host, port
    return fake


def test_nimbus_client_reconnects_to_the_leader(monkeypatch):
    """A follower serves reads and then throws on the first write.

    Storm reports that as `TApplicationException: Internal error processing
    killTopologyWithOpts`, which says nothing about leadership.
    """
    import pystorm_a8c.util as util

    opened = []
    monkeypatch.setattr(util, "make_client", _leader_stub("leader-host", 6627, opened))

    util.get_nimbus_client({"nimbus": "follower-host:6627"})

    assert opened == [("follower-host", 6627), ("leader-host", 6627)]


def test_nimbus_client_stays_put_when_it_is_already_the_leader(monkeypatch):
    import pystorm_a8c.util as util

    opened = []
    monkeypatch.setattr(util, "make_client", _leader_stub("h", 6627, opened))

    util.get_nimbus_client({"nimbus": "h:6627"})

    assert opened == [("h", 6627)]


def test_the_follower_connection_is_closed_on_redirect(monkeypatch):
    import pystorm_a8c.util as util

    clients = []

    def fake(service, host=None, port=None, **kwargs):
        client = SimpleNamespace(closed=False)
        client.getLeader = lambda: SimpleNamespace(
            host="leader-host", port=6627, isLeader=True
        )
        client.close = lambda: setattr(client, "closed", True)
        clients.append(client)
        return client

    monkeypatch.setattr(util, "make_client", fake)
    util.get_nimbus_client({"nimbus": "follower-host:6627"})

    assert clients[0].closed is True
    assert clients[1].closed is False
