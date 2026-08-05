"""
Configuration loading and the Nimbus Thrift client.

Ported from ``streamparse.util`` with everything SSH-shaped removed. Nimbus is
always contacted directly: the tunnel, the port-probe, the ``fabric``-driven
remote command helpers and the log-file plumbing that only existed to reach
worker boxes over SSH are all gone.
"""

import importlib
import json
import logging
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
#: Must not be left as `None`: thriftpy2 maps a falsy timeout to
#: `socket_timeout=None`, i.e. a blocking socket, so a Nimbus that accepts the
#: connection and then hangs would wedge the submit forever. Matches the
#: default of `pystorm-a8c submit --timeout`.
DEFAULT_NIMBUS_TIMEOUT_MS = 7000

log = logging.getLogger(__name__)


def warn(msg):
    """Print a warning to stderr.

    Upstream colourized this via ``fabric.colors`` and printed to stdout, which
    meant warnings landed in the middle of machine-readable command output.
    """
    print(f"warning: {msg}", file=sys.stderr)


def die(msg, error_code=1):
    """Print an error to stderr and exit."""
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(error_code)


# ---------------------------------------------------------------- config

_config = None


def get_config(config_file=None):
    """Parse the project's config.json and return it as a `dict`.

    :param config_file: a path or `file`-like object holding the config.json
                        contents. If `None`, ``config.json`` in the working
                        directory is used.

    :returns: a `dict` representing the parsed config.

    .. note::
       Only the working-directory lookup is memoized. Upstream memoized every
       call regardless of argument, so the second caller to pass an explicit
       file silently received the first caller's config back.
    """
    global _config

    if config_file is None:
        if _config is not None:
            return _config
        if not os.path.exists("config.json"):
            die(
                "No config.json found. You must run this command inside a "
                "topology project directory."
            )
        with open("config.json") as fp:
            _config = json.load(fp)
        return _config

    if isinstance(config_file, (str, bytes, os.PathLike)):
        with open(config_file) as fp:
            return json.load(fp)
    # A caller that hands us an open handle usually hands us the same one to
    # several of the functions below; rewind so the second read is not empty.
    try:
        config_file.seek(0)
    except (AttributeError, OSError):
        pass
    return json.load(config_file)


def get_topology_definition(topology_name=None, config_file=None):
    """Fetch a topology name and definition file.

    If ``topology_name`` is None and only one topology definition exists, that
    one is selected; otherwise this dies rather than guess.

    :param topology_name: the name of the topology (without the .py extension).
    :param config_file: see :func:`get_config`.

    :returns: a `tuple` of (topology_name, topology_file).
    """
    config = get_config(config_file=config_file)
    topology_path = config["topology_specs"]
    if topology_name is None:
        topology_files = glob(f"{topology_path}/*.py")
        if not topology_files:
            die(f"No topology definitions are defined in {topology_path}.")
        if len(topology_files) > 1:
            die(
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
            die(
                f"Topology definition file not found {topology_file}. You need "
                f"to create a topology definition file first."
            )

    return (topology_name, topology_file)


def get_env_config(env_name=None, config_file=None):
    """Fetch an environment name and config object from config.json.

    If ``env_name`` is None and only one environment exists, that one is
    selected; otherwise this dies rather than guess.

    :param config_file: see :func:`get_config`.

    :returns: a `tuple` of (env_name, env_config).

    .. note::
       The name, signature and return shape are part of an external contract:
       four ``casterisk-realtime`` modules import this and unpack the pair.
    """
    config = get_config(config_file=config_file)
    if env_name is None and len(config["envs"]) == 1:
        env_name = list(config["envs"].keys())[0]
    elif env_name is None and len(config["envs"]) > 1:
        die(
            "Found more than one environment in config.json. When more than "
            "one environment exists, you must explicitly specify the "
            "environment name via the -e or --environment flags."
        )
    if env_name not in config["envs"]:
        die(f'Could not find a "{env_name}" in config.json, have you specified one?')

    return (env_name, config["envs"][env_name])


def get_topology_from_file(topology_file):
    """Import a topology definition module and return its `Topology` subclass."""
    topology_dir, mod_name = os.path.split(topology_file)
    # Remove .py extension before trying to import
    mod_name = mod_name[:-3]
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

    Upstream prepended ``-s <serializer> `` to every component's script so that
    ``streamparse_run`` would pick a serializer module. That flag no longer
    exists: ``pystorm-a8c-run`` speaks JSON and nothing else, so the prefix
    would make argparse exit non-zero on every worker. Since ``config.json``
    files in the wild still carry ``"serializer": "json"``, the key is accepted
    and the script is left untouched.

    Anything other than ``json`` is refused rather than downgraded -- quietly
    swapping the protocol would mis-decode every tuple on the wire.
    """
    serializer = env_config.get("serializer", config.get("serializer", None))
    if serializer is not None and serializer != "json":
        die(
            f'Unsupported serializer "{serializer}". pystorm-a8c speaks JSON '
            f"only; remove the serializer key from config.json."
        )


# ---------------------------------------------------------------- Nimbus

_storm_workers = {}


def get_nimbus_host_port(env_config):
    """Resolve the Nimbus RPC endpoint.

    ``PYSTORM_A8C_NIMBUS`` overrides config.json entirely.

    :param env_config: The project's parsed env config.
    :type env_config: `dict`

    :returns: (host, port)

    .. note::
       ``use_ssh_for_nimbus`` in config.json is accepted for backwards
       compatibility and ignored, but warns. Nimbus is always contacted
       directly. A stale config that still sets the key would otherwise look
       like it is tunneling when it is not.
    """
    if "use_ssh_for_nimbus" in env_config:
        log.warning(
            "use_ssh_for_nimbus is set in the env config and is ignored: "
            "pystorm-a8c always contacts Nimbus directly. Remove the key."
        )
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

    Uses ``workers`` from config.json when present; otherwise asks Nimbus and
    memoizes the answer per (host, port).

    .. note::
       The name and signature are part of an external contract:
       ``casterisk-realtime``'s ``conftest.py`` monkeypatches this. Do not add
       parameters -- a stand-in written against this one-argument shape raises
       TypeError the moment a caller passes anything else. The Nimbus client
       below gets its own timeout from :data:`DEFAULT_NIMBUS_TIMEOUT_MS`, so
       this lookup cannot hang without one.
    """
    workers = env_config.get("workers")
    if workers:
        return workers
    host, port = get_nimbus_host_port(env_config)
    if (host, port) not in _storm_workers:
        client = get_nimbus_client(env_config, host=host, port=port)
        cluster_info = client.getClusterInfo()
        _storm_workers[(host, port)] = [s.host for s in cluster_info.supervisors]
    return _storm_workers[(host, port)]


def nimbus_storm_version(nimbus_client):
    """Return Nimbus's Storm version as a comparable tuple.

    Returning a tuple rather than a ``pkg_resources`` version object is what
    lets ``setuptools`` stay out of the runtime dependency set.
    """
    try:
        raw = nimbus_client.getVersion()
    except Exception:
        # Storm < 0.10.0 has no getVersion; treat as oldest supported.
        return (0, 0, 0)
    parts = []
    for chunk in str(raw).split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)
