"""
Tests for the submit command.
"""

import inspect
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pystorm_a8c.storm import ShellComponent


def shell_bolt(execution_command="pystorm-a8c-run", script="mypkg.mod.MyBolt"):
    shell = ShellComponent(execution_command=execution_command, script=script)
    return SimpleNamespace(bolt_object=SimpleNamespace(shell=shell)), shell


def shell_spout(execution_command="pystorm-a8c-run", script="mypkg.mod.MySpout"):
    shell = ShellComponent(execution_command=execution_command, script=script)
    return SimpleNamespace(spout_object=SimpleNamespace(shell=shell)), shell


def test_execution_command_is_rewritten_to_the_venv_path():
    """The blobstore venv path is the a8c deploy mechanism -- guard it."""
    from pystorm_a8c.cli.submit import rewrite_execution_commands

    bolt, shell = shell_bolt()
    topology_class = SimpleNamespace(thrift_bolts={"b": bolt}, thrift_spouts={})

    rewrite_execution_commands(topology_class)

    assert shell.execution_command == "../venv/bin/pystorm-a8c-run"


def test_spouts_are_rewritten_too():
    from pystorm_a8c.cli.submit import rewrite_execution_commands

    spout, shell = shell_spout()
    topology_class = SimpleNamespace(thrift_bolts={}, thrift_spouts={"s": spout})

    rewrite_execution_commands(topology_class)

    assert shell.execution_command == "../venv/bin/pystorm-a8c-run"


def test_the_rewritten_path_matches_what_the_blobstore_delivers():
    """The execution command, the localname and the PATH are one path.

    They used to come from three places that could disagree; a mismatch is a
    worker that never starts, with nothing in the submit output to say why.
    """
    from pystorm_a8c.cli.submit import (
        blobstore_options,
        rewrite_execution_commands,
    )

    bolt, shell = shell_bolt()
    topology_class = SimpleNamespace(thrift_bolts={"b": bolt}, thrift_spouts={})

    rewrite_execution_commands(topology_class)
    opts = blobstore_options("myproject-venv-abc123_tar_gz")

    localname = opts["topology.blobstore.map"]["myproject-venv-abc123_tar_gz"][
        "localname"
    ]
    assert shell.execution_command == f"../{localname}/bin/pystorm-a8c-run"
    assert opts["topology.environment"]["PATH"].startswith(f"../{localname}/bin:")


def test_the_script_is_left_alone_when_rewriting():
    """Only the interpreter moves into the venv; the dotted target must not."""
    from pystorm_a8c.cli.submit import rewrite_execution_commands

    bolt, shell = shell_bolt()
    topology_class = SimpleNamespace(thrift_bolts={"b": bolt}, thrift_spouts={})

    rewrite_execution_commands(topology_class)

    assert shell.script == "mypkg.mod.MyBolt"


def test_a_foreign_execution_command_is_not_rewritten():
    from pystorm_a8c.cli.submit import rewrite_execution_commands

    bolt, shell = shell_bolt(execution_command="/usr/bin/node")
    topology_class = SimpleNamespace(thrift_bolts={"b": bolt}, thrift_spouts={})

    rewrite_execution_commands(topology_class)

    assert shell.execution_command == "/usr/bin/node"


def test_parallelism_hint_dict_is_resolved_per_env():
    from pystorm_a8c.cli.submit import resolve_parallelism

    comp = MagicMock()
    comp.common.parallelism_hint = {"storm4": 8, "local": 1}
    topology_class = MagicMock()
    topology_class.thrift_bolts = {"b": comp}
    topology_class.thrift_spouts = {}

    resolve_parallelism(topology_class, "storm4")
    assert comp.common.parallelism_hint == 8


def test_a_plain_parallelism_hint_is_left_alone():
    from pystorm_a8c.cli.submit import resolve_parallelism

    comp = MagicMock()
    comp.common.parallelism_hint = 4
    topology_class = MagicMock()
    topology_class.thrift_bolts = {"b": comp}
    topology_class.thrift_spouts = {}

    resolve_parallelism(topology_class, "storm4")
    assert comp.common.parallelism_hint == 4


def test_a_par_dict_missing_this_env_is_refused():
    """Upstream sent None, which Nimbus silently reads as parallelism 1."""
    from pystorm_a8c.cli.submit import resolve_parallelism

    comp = MagicMock()
    comp.common.parallelism_hint = {"storm3": 150, "local": 1}
    topology_class = MagicMock()
    topology_class.thrift_bolts = {"views_reader": comp}
    topology_class.thrift_spouts = {}

    with pytest.raises(ValueError) as exc:
        resolve_parallelism(topology_class, "storm4")
    assert "views_reader" in str(exc.value)
    assert "storm4" in str(exc.value)


def test_the_error_names_every_offending_component_at_once():
    """One submit should not have to be re-run once per missing key."""
    from pystorm_a8c.cli.submit import resolve_parallelism

    bolt, spout = MagicMock(), MagicMock()
    bolt.common.parallelism_hint = {"storm3": 4}
    spout.common.parallelism_hint = {"storm3": 8}
    topology_class = MagicMock()
    topology_class.thrift_bolts = {"counter": bolt}
    topology_class.thrift_spouts = {"reader": spout}

    with pytest.raises(ValueError) as exc:
        resolve_parallelism(topology_class, "storm4")
    message = str(exc.value)
    assert "counter" in message and "reader" in message


def test_a_par_dict_is_not_mutated_when_the_submit_is_refused():
    """A failed resolve must leave the topology re-submittable as-is."""
    from pystorm_a8c.cli.submit import resolve_parallelism

    good, bad = MagicMock(), MagicMock()
    good.common.parallelism_hint = {"storm4": 8}
    bad.common.parallelism_hint = {"storm3": 4}
    topology_class = MagicMock()
    topology_class.thrift_bolts = {"good": good, "bad": bad}
    topology_class.thrift_spouts = {}

    with pytest.raises(ValueError):
        resolve_parallelism(topology_class, "storm4")
    assert bad.common.parallelism_hint == {"storm3": 4}


def test_submit_does_not_open_an_ssh_tunnel():
    from pystorm_a8c.cli import submit

    assert "ssh_tunnel" not in inspect.getsource(submit)


def test_submit_has_no_user_hooks():
    """`tasks.py` / `fabfile.py` in the cwd are no longer imported and called."""
    from pystorm_a8c.cli import submit

    source = inspect.getsource(submit)
    assert "pre_submit" not in source
    assert "fabfile" not in source


def test_a_retired_option_is_refused_with_its_reason():
    """Refused, not ignored. casterisk used to pass `-o install_virtualenv=0`."""
    from pystorm_a8c.cli.submit import check_options_are_consumed

    with pytest.raises(ValueError) as exc:
        check_options_are_consumed({"install_virtualenv": 0}, "-o options")

    assert "install_virtualenv" in str(exc.value)
    assert "blobstore" in str(exc.value)


def test_a_derived_option_cannot_be_set_by_hand():
    from pystorm_a8c.cli.submit import check_options_are_consumed

    with pytest.raises(ValueError) as exc:
        check_options_are_consumed({"topology.blobstore.map": {}}, "-o options")

    assert "--venv-blobstore-key" in str(exc.value)


def test_an_unknown_bare_option_is_refused():
    """A bare word is addressed to us, and we have nothing left it could mean."""
    from pystorm_a8c.cli.submit import check_options_are_consumed

    with pytest.raises(ValueError) as exc:
        check_options_are_consumed({"virtualenv_flgas": "typo"}, "-o options")

    assert "virtualenv_flgas" in str(exc.value)


def test_dotted_storm_conf_keys_pass_through():
    from pystorm_a8c.cli.submit import check_options_are_consumed

    check_options_are_consumed(
        {"topology.workers": 4, "storm.zookeeper.port": 2181, "pystorm.log.level": "x"},
        "-o options",
    )


def test_every_offending_option_is_named_at_once():
    from pystorm_a8c.cli.submit import check_options_are_consumed

    with pytest.raises(ValueError) as exc:
        check_options_are_consumed(
            {"use_virtualenv": 1, "virtualenv_flags": "-p x"}, "-o options"
        )

    assert "use_virtualenv" in str(exc.value)
    assert "virtualenv_flags" in str(exc.value)


def test_upload_jar_chunks_the_whole_file(tmp_path):
    from pystorm_a8c.cli.submit import THRIFT_CHUNK_SIZE, _upload_jar

    jar = tmp_path / "topology.jar"
    payload = b"x" * (THRIFT_CHUNK_SIZE + 17)
    jar.write_bytes(payload)

    chunks = []
    client = MagicMock()
    client.beginFileUpload.return_value = "/upload/loc"
    client.uploadChunk.side_effect = lambda loc, chunk: chunks.append(chunk)

    assert _upload_jar(client, str(jar)) == "/upload/loc"
    assert b"".join(chunks) == payload
    assert [len(c) for c in chunks] == [THRIFT_CHUNK_SIZE, 17]
    client.finishFileUpload.assert_called_once_with("/upload/loc")


def test_name_check_is_skipped_on_storm_below_1_1(monkeypatch):
    """isTopologyNameAllowed does not exist before 1.1.0; calling it errors."""
    from pystorm_a8c.cli import submit

    client = MagicMock()
    client.getVersion.return_value = "1.0.3"
    submit._submit_topology(
        "raws", MagicMock(), "/remote.jar", {}, {}, client, options={}
    )
    client.isTopologyNameAllowed.assert_not_called()


def test_a_name_nimbus_rejects_aborts_the_submit():
    from pystorm_a8c.cli import submit

    client = MagicMock()
    client.getVersion.return_value = "1.2.3"
    client.isTopologyNameAllowed.return_value = False
    with pytest.raises(ValueError):
        submit._submit_topology(
            "bad name", MagicMock(), "/remote.jar", {}, {}, client, options={}
        )
    client.submitTopologyWithOpts.assert_not_called()


def test_inactive_submits_with_inactive_initial_status():
    from pystorm_a8c.storm import TopologyInitialStatus
    from pystorm_a8c.cli import submit

    client = MagicMock()
    client.getVersion.return_value = "1.2.3"
    client.isTopologyNameAllowed.return_value = True
    submit._submit_topology(
        "raws", MagicMock(), "/remote.jar", {}, {}, client, options={}, active=False
    )
    opts = client.submitTopologyWithOpts.call_args.kwargs["options"]
    assert opts.initial_status == TopologyInitialStatus.INACTIVE


def test_options_are_submitted_as_json():
    from pystorm_a8c.cli import submit

    client = MagicMock()
    client.getVersion.return_value = "1.2.3"
    client.isTopologyNameAllowed.return_value = True
    submit._submit_topology(
        "raws",
        MagicMock(),
        "/remote.jar",
        {},
        {},
        client,
        options={"topology.workers": 3},
    )
    import json

    conf = json.loads(client.submitTopologyWithOpts.call_args.kwargs["jsonConf"])
    assert conf == {"topology.workers": 3}


def test_the_submit_parser_accepts_the_flags_casterisk_deploys_with():
    """These are the exact arguments `bin/topo-submit` builds."""
    import argparse

    from pystorm_a8c.cli import submit

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    submit.subparser_hook(subparsers)

    args = parser.parse_args(
        [
            "submit",
            "-e",
            "storm4",
            "-n",
            "raws",
            "-N",
            "raws_prod",
            "-j",
            "topology.jar",
            "-f",
            "--wait",
            "30",
            "--venv-blobstore-key",
            "myproject-venv-abc123_tar_gz",
            "-o",
            "topology.max.spout.pending=200",
        ]
    )
    assert args.environment == "storm4"
    assert args.name == "raws"
    assert args.override_name == "raws_prod"
    assert args.local_jar_path == "topology.jar"
    assert args.force is True
    assert args.wait == 30
    assert args.active is True
    assert args.venv_blobstore_key == "myproject-venv-abc123_tar_gz"
    # The four venv -o flags this used to carry are derived now.
    assert args.options == {"topology.max.spout.pending": 200}


def test_the_venv_blobstore_key_is_required():
    """There is no worker layout without a venv, so there is no default."""
    import argparse

    from pystorm_a8c.cli import submit

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    submit.subparser_hook(subparsers)

    with pytest.raises(SystemExit):
        parser.parse_args(["submit", "-n", "raws"])


def test_submit_topology_refuses_an_empty_key():
    """Belt and braces for library callers, who do not go through argparse."""
    from pystorm_a8c.cli.submit import submit_topology

    with pytest.raises(ValueError, match="venv_blobstore_key is required"):
        submit_topology(venv_blobstore_key="")


def test_submit_topology_takes_only_keyword_arguments():
    """An outdated positional call must fail, not mean something else.

    The signature used to begin `name=None`, so a leading positional
    venv_blobstore_key would have made `submit_topology("raws")` keep working
    while silently submitting an auto-discovered topology against a blobstore
    key of "raws".
    """
    import inspect

    from pystorm_a8c.cli.submit import submit_topology

    kinds = {p.kind for p in inspect.signature(submit_topology).parameters.values()}
    assert kinds == {inspect.Parameter.KEYWORD_ONLY}

    with pytest.raises(TypeError):
        submit_topology("raws")


def test_the_removed_flags_are_really_removed():
    """Each of these only drove virtualenv-over-SSH or the uber-JAR path."""
    import argparse

    from pystorm_a8c.cli import submit

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    submit.subparser_hook(subparsers)

    for flag in ("-r", "--overwrite_virtualenv", "--user", "--pool_size", "-u"):
        with pytest.raises(SystemExit):
            parser.parse_args(["submit", flag, "x"])


# --------------------------------------------------------------------- flow


def _fake_topology_class():
    """A topology whose specs look like what the DSL produces."""
    shell = ShellComponent(execution_command="pystorm-a8c-run", script="mypkg.mod.B")
    bolt = SimpleNamespace(
        bolt_object=SimpleNamespace(shell=shell),
        common=SimpleNamespace(parallelism_hint=4),
    )
    return (
        SimpleNamespace(
            thrift_bolts={"b": bolt},
            thrift_spouts={},
            config={},
            thrift_topology=object(),
        ),
        shell,
    )


def _patched_submit(monkeypatch, nimbus, **overrides):
    """Stub out everything submit_topology reaches outside its own module."""
    from pystorm_a8c.cli import submit as mod

    topology_class, shell = _fake_topology_class()
    monkeypatch.setattr(mod, "get_config", lambda **kw: {})
    monkeypatch.setattr(
        mod, "get_topology_definition", lambda n, **kw: ("raws", "t.py")
    )
    monkeypatch.setattr(
        mod, "get_env_config", lambda e, **kw: ("storm4", {"workers": ["w1", "w2"]})
    )
    monkeypatch.setattr(mod, "get_topology_from_file", lambda f: topology_class)
    monkeypatch.setattr(mod, "get_storm_workers", lambda env: ["w1", "w2"])
    monkeypatch.setattr(mod, "get_nimbus_host_port", lambda env: ("nimbus-host", 6627))
    monkeypatch.setattr(mod, "get_nimbus_client", lambda *a, **kw: nimbus)
    monkeypatch.setattr(mod, "nimbus_storm_version", lambda c: (1, 2, 3))
    monkeypatch.setattr(mod, "set_topology_serializer", lambda *a: None)
    return topology_class, shell


def _nimbus():
    client = MagicMock()
    client.getClusterInfo.return_value = SimpleNamespace(topologies=[])
    client.isTopologyNameAllowed.return_value = True
    client.beginFileUpload.return_value = "/upload/loc"
    return client


def test_submit_topology_uploads_and_submits(monkeypatch, tmp_path):
    """The whole orchestration, with Nimbus mocked out."""
    from pystorm_a8c.cli.submit import submit_topology

    nimbus = _nimbus()
    topology_class, shell = _patched_submit(monkeypatch, nimbus)
    jar = tmp_path / "topology.jar"
    jar.write_bytes(b"payload")

    submit_topology(
        venv_blobstore_key="myproject-venv-abc123_tar_gz",
        local_jar_path=str(jar),
    )

    # The JAR was uploaded and the topology submitted under its own name.
    nimbus.beginFileUpload.assert_called_once()
    nimbus.finishFileUpload.assert_called_once_with("/upload/loc")
    _, kwargs = nimbus.submitTopologyWithOpts.call_args
    assert kwargs["name"] == "raws"
    assert kwargs["uploadedJarLocation"] == "/upload/loc"

    conf = json.loads(kwargs["jsonConf"])
    assert conf["topology.original_name"] == "raws"
    assert conf["topology.blobstore.map"] == {
        "myproject-venv-abc123_tar_gz": {"localname": "venv", "uncompress": True}
    }
    # ...and the specs were pointed at the venv the blobstore will deliver.
    assert shell.execution_command == "../venv/bin/pystorm-a8c-run"


def test_submit_topology_honours_override_name(monkeypatch, tmp_path):
    from pystorm_a8c.cli.submit import submit_topology

    nimbus = _nimbus()
    _patched_submit(monkeypatch, nimbus)
    jar = tmp_path / "topology.jar"
    jar.write_bytes(b"payload")

    submit_topology(
        venv_blobstore_key="k_tar_gz",
        override_name="raws_prod",
        local_jar_path=str(jar),
    )

    _, kwargs = nimbus.submitTopologyWithOpts.call_args
    assert kwargs["name"] == "raws_prod"
    # The original name still travels, so the deploy is traceable.
    assert json.loads(kwargs["jsonConf"])["topology.original_name"] == "raws"


def test_submit_topology_reuses_a_remote_jar(monkeypatch):
    """-R skips the upload entirely."""
    from pystorm_a8c.cli.submit import submit_topology

    nimbus = _nimbus()
    _patched_submit(monkeypatch, nimbus)

    submit_topology(venv_blobstore_key="k_tar_gz", remote_jar_path="/srv/topology.jar")

    nimbus.beginFileUpload.assert_not_called()
    _, kwargs = nimbus.submitTopologyWithOpts.call_args
    assert kwargs["uploadedJarLocation"] == "/srv/topology.jar"


def test_submit_topology_refuses_a_par_dict_missing_this_env(monkeypatch):
    """The guard runs before the JAR is uploaded, not after."""
    from pystorm_a8c.cli.submit import submit_topology

    nimbus = _nimbus()
    topology_class, _ = _patched_submit(monkeypatch, nimbus)
    topology_class.thrift_bolts["b"].common.parallelism_hint = {"storm3": 150}

    with pytest.raises(ValueError, match="storm4"):
        submit_topology(venv_blobstore_key="k_tar_gz", remote_jar_path="/srv/t.jar")

    nimbus.submitTopologyWithOpts.assert_not_called()
