"""
Configuration loading and the Nimbus Thrift client. Nimbus is always contacted
directly; there is no SSH tunnel.
"""

import importlib
import json
import os
import re
import sys
from glob import glob

from thriftpy2.protocol import TBinaryProtocolFactory
from thriftpy2.rpc import make_client
from thriftpy2.transport import TFramedTransportFactory

from pystorm_a8c.dsl.topology import Topology, TopologyType
from pystorm_a8c.storm import Nimbus

DEFAULT_NIMBUS_PORT = 6627
NIMBUS_ENV_VAR = "PYSTORM_A8C_NIMBUS"

#: Milliseconds to wait on a Nimbus socket when no caller supplies a value.
#: Never leave this `None`: thriftpy2 maps a falsy timeout to a blocking
#: socket, so a Nimbus that accepts the connection and then hangs would wedge
#: the submit forever.
DEFAULT_NIMBUS_TIMEOUT_MS = 7000


# ---------------------------------------------------------------- config


def get_config(config_file=None):
    """Parse the project's config.json and return it as a `dict`.

    :param config_file: a path or `file`-like object holding the config.json
                        contents. If `None`, ``config.json`` in the working
                        directory is used.

    :returns: a `dict` representing the parsed config.
    """
    if config_file is None:
        config_file = "config.json"

    if isinstance(config_file, (str, bytes, os.PathLike)):
        with open(config_file) as fp:
            return json.load(fp)
    # One handle is often passed to several of the functions below; rewind so
    # the second read is not empty.
    config_file.seek(0)
    return json.load(config_file)


def get_topology_definition(topology_name=None, config_file=None):
    """Fetch a topology name and definition file.

    If ``topology_name`` is None and only one topology definition exists, that
    one is selected; otherwise this raises rather than guess.

    :param topology_name: the name of the topology (without the .py extension).
    :param config_file: see :func:`get_config`.

    :returns: a `tuple` of (topology_name, topology_file).
    """
    config = get_config(config_file=config_file)
    topology_path = config["topology_specs"]
    if topology_name is None:
        topology_files = glob(f"{topology_path}/*.py")
        if not topology_files:
            raise FileNotFoundError(
                f"No topology definitions are defined in {topology_path}."
            )
        if len(topology_files) > 1:
            raise ValueError(
                f"Found more than one topology definition file in "
                f"{topology_path}. When more than one topology definition file "
                f"exists, you must explicitly specify the topology by name "
                f"using the -n or --name flags."
            )
        topology_file = topology_files[0]
        topology_name = re.sub(rf"(^{topology_path}/|\.py$)", "", topology_file)
    else:
        topology_file = f"{os.path.join(topology_path, topology_name)}.py"
        if not os.path.exists(topology_file):
            raise FileNotFoundError(
                f"Topology definition file not found {topology_file}. You need "
                f"to create a topology definition file first."
            )

    return (topology_name, topology_file)


def get_env_config(env_name=None, config_file=None):
    """Fetch an environment name and config object from config.json.

    If ``env_name`` is None and only one environment exists, that one is
    selected; otherwise this raises rather than guess.

    :param config_file: see :func:`get_config`.

    :returns: a `tuple` of (env_name, env_config).

    .. note::
       Name, signature and return shape are an external contract: consumers
       import this and unpack the pair.
    """
    config = get_config(config_file=config_file)
    if env_name is None and len(config["envs"]) == 1:
        env_name = list(config["envs"].keys())[0]
    elif env_name is None and len(config["envs"]) > 1:
        raise ValueError(
            "Found more than one environment in config.json. When more than "
            "one environment exists, you must explicitly specify the "
            "environment name via the -e or --environment flags."
        )
    if env_name not in config["envs"]:
        raise ValueError(
            f'Could not find a "{env_name}" in config.json, have you specified one?'
        )

    return (env_name, config["envs"][env_name])


def get_topology_from_file(topology_file):
    """Import a topology definition module and return its `Topology` subclass."""
    topology_dir, mod_name = os.path.split(topology_file)
    mod_name = mod_name[:-3]  # strip .py
    sys.path.append(os.path.join(topology_dir, "..", "src"))
    sys.path.append(topology_dir)
    mod = importlib.import_module(mod_name)
    for attr in mod.__dict__.values():
        if isinstance(attr, TopologyType) and attr != Topology:
            topology_class = attr
            break
    else:
        raise ValueError("Could not find topology subclass in topology module.")
    return topology_class


def set_topology_serializer(env_config, config, topology_class):
    """Validate the configured serializer. JSON is the only one supported.

    Accepted rather than rejected outright because config.json files in the
    wild carry ``"serializer": "json"``. Any other value is refused rather
    than downgraded: swapping the protocol would mis-decode every tuple.
    """
    serializer = env_config.get("serializer", config.get("serializer", None))
    if serializer is not None and serializer != "json":
        raise ValueError(
            f'Unsupported serializer "{serializer}". pystorm-a8c speaks JSON '
            f"only; remove the serializer key from config.json."
        )


# ---------------------------------------------------------------- Nimbus


def get_nimbus_host_port(env_config):
    """Resolve the Nimbus RPC endpoint.

    ``PYSTORM_A8C_NIMBUS`` overrides config.json entirely.

    :param env_config: The project's parsed env config.
    :type env_config: `dict`

    :returns: (host, port)
    """
    nimbus = os.environ.get(NIMBUS_ENV_VAR) or env_config.get("nimbus")
    if not nimbus:
        raise ValueError("No nimbus host specified in env config or environment")
    if ":" in nimbus:
        host, _, port = nimbus.rpartition(":")
        return host, int(port)
    return nimbus, DEFAULT_NIMBUS_PORT


def get_nimbus_client(env_config=None, host=None, port=None, timeout=None):
    """Open a Thrift RPC client against Nimbus.

    :param env_config: The project's parsed env config. Not consulted when
                       ``host`` is given.
    :param timeout: milliseconds to wait for a response from Nimbus. `None`
                    means :data:`DEFAULT_NIMBUS_TIMEOUT_MS`, never "no
                    timeout" -- see that constant.

    :returns: a thriftpy2 RPC client for the Nimbus service.
    """
    if host is None:
        host, port = get_nimbus_host_port(env_config)
    if timeout is None:
        timeout = DEFAULT_NIMBUS_TIMEOUT_MS
    return make_client(
        Nimbus,
        host=host,
        port=port,
        proto_factory=TBinaryProtocolFactory(),
        trans_factory=TFramedTransportFactory(),
        timeout=timeout,
    )


def get_storm_workers(env_config):
    """Return the list of supervisor hosts.

    Uses ``workers`` from config.json when present; otherwise asks Nimbus.

    .. note::
       Name and signature are an external contract: consumers monkeypatch this
       in their own test suites. Do not add parameters -- a one-argument
       stand-in would raise TypeError as soon as a caller passed anything else.
    """
    workers = env_config.get("workers")
    if workers:
        return workers
    client = get_nimbus_client(env_config)
    return [s.host for s in client.getClusterInfo().supervisors]


def nimbus_storm_version(nimbus_client):
    """Return Nimbus's Storm version as a comparable tuple.

    A tuple rather than a version object, so ``setuptools`` stays out of the
    runtime dependencies. RPC failures propagate: a timeout or a wrong host is
    not an old Storm.
    """
    parts = []
    for chunk in str(nimbus_client.getVersion()).split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)
