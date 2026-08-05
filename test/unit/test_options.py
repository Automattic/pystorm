"""
Tests for `-o key=value` parsing and option precedence resolution.
"""

from types import SimpleNamespace

import pytest

from pystorm_a8c.cli.submit import parse_option, resolve_options


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("install_virtualenv=0", ("install_virtualenv", 0)),
        ("virtualenv_name=venv", ("virtualenv_name", "venv")),
        (
            "topology.subprocess.timeout.secs=600",
            ("topology.subprocess.timeout.secs", 600),
        ),
        ("topology.stats.sample.rate=1.0", ("topology.stats.sample.rate", 1.0)),
        ("topology.ignore_sentry=1", ("topology.ignore_sentry", 1)),
        (
            'topology.environment={"PATH":"../venv/bin:/usr/local/bin:/usr/bin:/bin"}',
            (
                "topology.environment",
                {"PATH": "../venv/bin:/usr/local/bin:/usr/bin:/bin"},
            ),
        ),
        (
            'topology.blobstore.map={"k_tar_gz":{"localname":"venv","uncompress":true}}',
            (
                "topology.blobstore.map",
                {"k_tar_gz": {"localname": "venv", "uncompress": True}},
            ),
        ),
        (
            "virtualenv_flags=-p /usr/local/bin/python3.9",
            ("virtualenv_flags", "-p /usr/local/bin/python3.9"),
        ),
    ],
)
def test_parse_option_matches_every_value_casterisk_passes(raw, expected):
    assert parse_option(raw) == expected


def test_parse_option_requires_an_equals_sign():
    with pytest.raises(ValueError):
        parse_option("no-equals-here")


def test_parse_option_keeps_equals_in_value():
    assert parse_option("k=a=b") == ("k", "a=b")


def test_parse_option_accepts_an_empty_value():
    assert parse_option("k=") == ("k", "")


def test_bare_words_stay_strings():
    """YAML would have turned these into bools; JSON leaves them alone."""
    assert parse_option("virtualenv_name=on") == ("virtualenv_name", "on")
    assert parse_option("k=yes") == ("k", "yes")


# ------------------------------------------------------------- precedence


def fake_topology(config=None):
    return SimpleNamespace(config=config or {})


def resolve(cli_options=None, env_config=None, topology_config=None):
    return resolve_options(
        cli_options,
        env_config if env_config is not None else {"workers": ["w1", "w2"]},
        fake_topology(topology_config),
        "some_topology",
    )


def test_cli_options_beat_topology_options_beat_env_options():
    options = resolve(
        cli_options={"topology.max.spout.pending": 3},
        env_config={
            "workers": ["w1"],
            "options": {
                "topology.max.spout.pending": 1,
                "topology.message.timeout.secs": 60,
            },
        },
        topology_config={"topology.max.spout.pending": 2, "topology.debug": False},
    )
    assert options["topology.max.spout.pending"] == 3
    assert options["topology.message.timeout.secs"] == 60


def test_topology_options_beat_env_options():
    options = resolve(
        env_config={"workers": ["w1"], "options": {"topology.max.spout.pending": 1}},
        topology_config={"topology.max.spout.pending": 2},
    )
    assert options["topology.max.spout.pending"] == 2


def test_workers_and_ackers_default_to_the_worker_count():
    options = resolve(env_config={"workers": ["w1", "w2", "w3"]})
    assert options["topology.workers"] == 3
    assert options["topology.acker.executors"] == 3


def test_a_comma_separated_worker_list_is_split():
    """casterisk's deploy task passes `-o storm.workers.list="'a,b,c'"`."""
    options = resolve(cli_options={"storm.workers.list": "a,b,c"})
    assert options["storm.workers.list"] == ["a", "b", "c"]
    assert options["topology.workers"] == 3


def test_log_config_becomes_pystorm_log_options():
    options = resolve(env_config={"workers": ["w1"], "log": {"level": "INFO"}})
    assert options["pystorm.log.level"] == "info"


def test_file_logging_keys_are_not_forwarded_to_nimbus():
    """The worker's RotatingFileHandler is gone; don't advertise a path.

    Forwarding these made submit print "Routing Python logging to ..." for a
    file no worker ever creates.
    """
    options = resolve(
        env_config={
            "workers": ["w1"],
            "log": {
                "level": "INFO",
                "path": "/var/log/storm",
                "file": "x.log",
                "max_bytes": 100,
                "backup_count": 3,
            },
        }
    )
    assert "pystorm.log.path" not in options
    assert "pystorm.log.file" not in options
    assert "pystorm.log.max_bytes" not in options
    assert "pystorm.log.backup_count" not in options


def test_topology_debug_forces_debug_logging():
    options = resolve(cli_options={"topology.debug": True})
    assert options["pystorm.log.level"] == "debug"


def test_topology_python_path_points_into_the_venv():
    """It names the same directory the execution command does.

    It used to be built from the topology name while the execution command was
    built from `virtualenv_name`, so with `-o virtualenv_name=venv` the two
    named different directories -- and this is the one an operator reads first
    when a worker will not start.
    """
    from pystorm_a8c.cli.submit import VIRTUALENV_BIN

    options = resolve(env_config={"workers": ["w1"]})
    assert options["topology.python.path"] == f"{VIRTUALENV_BIN}/python"
    assert options["topology.python.path"] == "../venv/bin/python"


def test_the_venv_wiring_is_added_from_the_blobstore_key():
    from pystorm_a8c.cli.submit import resolve_options

    options = resolve_options(
        None,
        {"workers": ["w1"]},
        fake_topology(),
        "raws",
        venv_blobstore_key="myproject-venv-abc123_tar_gz",
    )

    assert options["topology.blobstore.map"] == {
        "myproject-venv-abc123_tar_gz": {"localname": "venv", "uncompress": True}
    }
    assert options["topology.environment"] == {
        "PATH": "../venv/bin:/usr/local/bin:/usr/bin:/bin"
    }
    # Not emitted: constants that nothing reads would just be dead conf keys.
    assert "virtualenv_name" not in options
    assert "virtualenv_root" not in options


def test_extra_environment_variables_are_merged_with_the_derived_path():
    """Adding TZ or LD_LIBRARY_PATH is ordinary; only PATH is reserved."""
    from pystorm_a8c.cli.submit import resolve_options

    options = resolve_options(
        {"topology.environment": {"TZ": "UTC", "LD_LIBRARY_PATH": "/opt/lib"}},
        {"workers": ["w1"]},
        fake_topology(),
        "raws",
        venv_blobstore_key="k_tar_gz",
    )

    assert options["topology.environment"] == {
        "TZ": "UTC",
        "LD_LIBRARY_PATH": "/opt/lib",
        "PATH": "../venv/bin:/usr/local/bin:/usr/bin:/bin",
    }


def test_a_hand_written_path_is_refused():
    """PATH has to put the venv first, or the component runs under the
    supervisor's interpreter."""
    from pystorm_a8c.cli.submit import resolve_options

    with pytest.raises(ValueError, match="must not set PATH"):
        resolve_options(
            {"topology.environment": {"PATH": "/usr/bin"}},
            {"workers": ["w1"]},
            fake_topology(),
            "raws",
            venv_blobstore_key="k_tar_gz",
        )


def test_a_non_dict_environment_is_refused():
    from pystorm_a8c.cli.submit import resolve_options

    with pytest.raises(ValueError, match="must be a dict"):
        resolve_options(
            {"topology.environment": "PATH=/usr/bin"},
            {"workers": ["w1"]},
            fake_topology(),
            "raws",
            venv_blobstore_key="k_tar_gz",
        )


def test_a_retired_key_in_the_env_block_is_refused():
    """config.json carried these too, not just the -o flags."""
    with pytest.raises(ValueError, match="use_ssh_for_nimbus"):
        resolve(env_config={"workers": ["w1"], "use_ssh_for_nimbus": False})


def test_a_stale_virtualenv_root_in_config_json_is_refused():
    """casterisk's config.json sets this key; it is derived now.

    Ignoring it silently would move every worker's venv without saying so.
    """
    with pytest.raises(ValueError, match="virtualenv_root"):
        resolve(env_config={"workers": ["w1"], "virtualenv_root": "/data/virtualenvs"})


def test_a_json_serializer_is_still_accepted():
    """`serializer` is consumed by set_topology_serializer, so it is not retired."""
    options = resolve(env_config={"workers": ["w1"], "serializer": "json"})
    assert options["topology.workers"] == 1


def test_a_bare_key_in_the_env_options_block_is_refused():
    with pytest.raises(ValueError, match="virtualenv_flags"):
        resolve(env_config={"workers": ["w1"], "options": {"virtualenv_flags": "-p x"}})


def test_a_bare_key_in_the_topology_config_is_refused():
    with pytest.raises(ValueError, match="Topology.config"):
        resolve(topology_config={"install_virtualenv": 0})


def test_sudo_user_is_gone():
    """It only ever fed the SSH/sudo path, and nothing in the monorepo reads it."""
    assert "sudo_user" not in resolve()


def test_local_only_skips_nimbus():
    options = resolve_options(
        None, {}, fake_topology(), "some_topology", local_only=True
    )
    assert options["storm.workers.list"] == []
    assert options["topology.workers"] == 1
