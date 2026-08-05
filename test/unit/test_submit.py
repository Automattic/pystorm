"""
Tests for the submit command.
"""

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pystorm_a8c.storm import ShellComponent


def shell_bolt(execution_command="pystorm_a8c_run", script="mypkg.mod.MyBolt"):
    shell = ShellComponent(execution_command=execution_command, script=script)
    return SimpleNamespace(bolt_object=SimpleNamespace(shell=shell)), shell


def shell_spout(execution_command="pystorm_a8c_run", script="mypkg.mod.MySpout"):
    shell = ShellComponent(execution_command=execution_command, script=script)
    return SimpleNamespace(spout_object=SimpleNamespace(shell=shell)), shell


def test_execution_command_is_rewritten_to_the_venv_path():
    """The blobstore venv path is the a8c deploy mechanism -- guard it."""
    from pystorm_a8c.cli.submit import rewrite_execution_commands

    bolt, shell = shell_bolt()
    topology_class = SimpleNamespace(thrift_bolts={"b": bolt}, thrift_spouts={})

    rewrite_execution_commands(
        topology_class, virtualenv_root="..", virtualenv_name="venv"
    )

    assert shell.execution_command == "../venv/bin/pystorm_a8c_run"


def test_spouts_are_rewritten_too():
    from pystorm_a8c.cli.submit import rewrite_execution_commands

    spout, shell = shell_spout()
    topology_class = SimpleNamespace(thrift_bolts={}, thrift_spouts={"s": spout})

    rewrite_execution_commands(
        topology_class, virtualenv_root="/data/virtualenvs", virtualenv_name="raws"
    )

    assert shell.execution_command == "/data/virtualenvs/raws/bin/pystorm_a8c_run"


def test_the_script_is_left_alone_when_rewriting():
    """Only the interpreter moves into the venv; the dotted target must not."""
    from pystorm_a8c.cli.submit import rewrite_execution_commands

    bolt, shell = shell_bolt()
    topology_class = SimpleNamespace(thrift_bolts={"b": bolt}, thrift_spouts={})

    rewrite_execution_commands(
        topology_class, virtualenv_root="..", virtualenv_name="venv"
    )

    assert shell.script == "mypkg.mod.MyBolt"


def test_a_foreign_execution_command_is_not_rewritten():
    from pystorm_a8c.cli.submit import rewrite_execution_commands

    bolt, shell = shell_bolt(execution_command="/usr/bin/node")
    topology_class = SimpleNamespace(thrift_bolts={"b": bolt}, thrift_spouts={})

    rewrite_execution_commands(
        topology_class, virtualenv_root="..", virtualenv_name="venv"
    )

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


def test_submit_does_not_open_an_ssh_tunnel():
    from pystorm_a8c.cli import submit

    assert "ssh_tunnel" not in inspect.getsource(submit)


def test_submit_has_no_user_hooks():
    """`tasks.py` / `fabfile.py` in the cwd are no longer imported and called."""
    from pystorm_a8c.cli import submit

    source = inspect.getsource(submit)
    assert "pre_submit" not in source
    assert "fabfile" not in source


def test_install_virtualenv_is_accepted_and_ignored(capsys):
    """casterisk passes `-o install_virtualenv=0`; the flag has no code left."""
    from pystorm_a8c.cli.submit import check_install_virtualenv

    check_install_virtualenv({"install_virtualenv": 0})
    assert capsys.readouterr().err == ""


def test_a_truthy_install_virtualenv_warns(capsys):
    from pystorm_a8c.cli.submit import check_install_virtualenv

    check_install_virtualenv({"install_virtualenv": 1})
    assert "install_virtualenv" in capsys.readouterr().err


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
            "-o",
            "install_virtualenv=0",
            "-o",
            "virtualenv_name=venv",
            "-o",
            'topology.blobstore.map={"k_tar_gz":{"localname":"venv"}}',
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
    assert args.options == {
        "install_virtualenv": 0,
        "virtualenv_name": "venv",
        "topology.blobstore.map": {"k_tar_gz": {"localname": "venv"}},
        "topology.max.spout.pending": 200,
    }


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
