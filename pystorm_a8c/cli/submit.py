"""
Submit a Storm topology to Nimbus.
"""

import argparse
import copy
import json
import sys
import time
from itertools import chain
from os.path import getsize

from pystorm_a8c.storm import (
    KillOptions,
    ShellComponent,
    SubmitOptions,
    TopologyInitialStatus,
)
from pystorm_a8c.util import (
    get_config,
    get_env_config,
    get_nimbus_client,
    get_nimbus_host_port,
    get_storm_workers,
    get_topology_definition,
    get_topology_from_file,
    nimbus_storm_version,
    set_topology_serializer,
    warn,
)

THRIFT_CHUNK_SIZE = 307200

RUN_COMMAND = "pystorm_a8c_run"

#: Options that configure the virtualenv the workers run out of. They are
#: passed through to the topology conf untouched; nothing in this package
#: creates or updates a virtualenv any more (that is the deploy script's job,
#: via the Storm blobstore).
VIRTUALENV_OPTIONS = (
    "install_virtualenv",
    "use_virtualenv",
    "virtualenv_flags",
    "virtualenv_root",
    "virtualenv_name",
)


# ------------------------------------------------------------ -o parsing


def parse_option(raw):
    """Parse a single ``-o key=value`` argument.

    Values are parsed as JSON when possible so ints, floats, bools and JSON
    objects survive; anything else stays a string.

    This replaces upstream's ``ruamel.yaml`` parse, the last non-``thriftpy2``
    runtime dependency. JSON is a subset of YAML for every value the deploy
    actually passes, and it is stricter in the one place that matters: YAML
    would coerce bare words like ``on``/``yes`` to booleans.
    """
    key, sep, value = raw.partition("=")
    if not sep:
        raise ValueError(f"Option must be of the form key=value, got: {raw!r}")
    try:
        return key, json.loads(value)
    except ValueError:
        return key, value


class _StoreDictAction(argparse.Action):
    """Action for storing repeated ``key=value`` option strings as one dict."""

    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest, None) is None:
            setattr(namespace, self.dest, {})
        # Only doing a copy here because that's what _AppendAction does
        items = copy.copy(getattr(namespace, self.dest))
        try:
            key, val = parse_option(values)
        except ValueError as e:
            raise argparse.ArgumentError(self, str(e))
        items[key] = val
        setattr(namespace, self.dest, items)


def resolve_options(
    cli_options,
    env_config,
    topology_class,
    topology_name,
    local_only=False,
    timeout=None,
):
    """Resolve potentially conflicting Storm options from three sources:

    CLI options > Topology options > config.json options

    :param local_only: Whether or not we should talk to Nimbus to get Storm
                       workers and other info.
    :param timeout: milliseconds to wait for Nimbus on the worker-list lookup.
                    Threaded through so that lookup honours ``--timeout``; it
                    runs before the submit's own client is built.
    """
    storm_options = {}

    # Start with environment options
    storm_options.update(env_config.get("options", {}))

    # Set topology.python.path
    if env_config.get("use_virtualenv", True):
        # Upstream raw-subscripted virtualenv_root, so an env config without
        # the key crashed the submit before it ever reached Nimbus.
        virtualenv_root = env_config.get("virtualenv_root", "..")
        python_path = "/".join([virtualenv_root, topology_name, "bin", "python"])
        # This setting is for information purposes only, and is not actually
        # read by any pystorm-a8c code.
        storm_options["topology.python.path"] = python_path

    # Set logging options based on environment config.
    #
    # Only the level is forwarded. `path`/`file`/`max_bytes`/`backup_count`
    # drove a RotatingFileHandler on the worker; that handler is gone --
    # Component always logs through StormHandler now -- so passing those keys
    # to Nimbus would advertise a destination no worker ever writes to.
    log_config = env_config.get("log", {})
    log_path = log_config.get("path") or env_config.get("log_path")
    log_file = log_config.get("file") or env_config.get("log_file")
    if log_path or log_file:
        warn(
            "log path/file settings are no longer supported and are being "
            "ignored: worker logging goes to Storm's own logs via "
            "StormHandler. Remove them from config.json."
        )
    if isinstance(log_config.get("level"), str):
        storm_options["pystorm.log.level"] = log_config["level"].lower()

    # Make sure virtualenv options are present here
    for venv_option in VIRTUALENV_OPTIONS:
        if venv_option in env_config:
            storm_options[venv_option] = env_config[venv_option]

    # Override options with topology options
    storm_options.update(topology_class.config)

    # Override options with CLI options
    storm_options.update(cli_options or {})

    # Set log level to debug if topology.debug is set
    if storm_options.get("topology.debug", False):
        storm_options["pystorm.log.level"] = "debug"

    # If ackers and executors still aren't set, use number of worker nodes
    if not local_only:
        if not storm_options.get("storm.workers.list"):
            storm_options["storm.workers.list"] = get_storm_workers(
                env_config, timeout=timeout
            )
        elif isinstance(storm_options["storm.workers.list"], str):
            storm_options["storm.workers.list"] = storm_options[
                "storm.workers.list"
            ].split(",")
        num_storm_workers = len(storm_options["storm.workers.list"])
    else:
        storm_options["storm.workers.list"] = []
        num_storm_workers = 1
    if storm_options.get("topology.acker.executors") is None:
        storm_options["topology.acker.executors"] = num_storm_workers
    if storm_options.get("topology.workers") is None:
        storm_options["topology.workers"] = num_storm_workers

    return storm_options


# ------------------------------------------------------ topology mangling


def check_install_virtualenv(options):
    """Warn if a config still asks this package to build a virtualenv.

    ``install_virtualenv`` used to drive an SSH fan-out that pip-installed a
    virtualenv on every supervisor. That is now the deploy script's job, via a
    tarball in the Storm blobstore, so the option is accepted (casterisk passes
    ``-o install_virtualenv=0``) but there is no code left to honor it. Saying
    so is better than appearing to install a venv that never appears.
    """
    if options.get("install_virtualenv"):
        warn(
            "install_virtualenv is set but is no longer supported: virtualenvs "
            "are shipped to workers through the Storm blobstore. Set "
            "install_virtualenv=0 to silence this."
        )


def rewrite_execution_commands(topology_class, virtualenv_root, virtualenv_name):
    """Point every shell component at the interpreter inside the venv.

    With ``virtualenv_root=".."`` and ``virtualenv_name="venv"`` this produces
    ``"../venv/bin/pystorm_a8c_run"``, matching the blobstore ``localname`` and
    the PATH set via ``-o topology.environment``. This is the a8c deploy
    mechanism; the path has to line up with what ``bin/topo-submit`` uploads.
    """
    run_path = "/".join([virtualenv_root, virtualenv_name, "bin", RUN_COMMAND])
    shells = chain(
        (b.bolt_object.shell for b in topology_class.thrift_bolts.values()),
        (s.spout_object.shell for s in topology_class.thrift_spouts.values()),
    )
    for shell in shells:
        if isinstance(shell, ShellComponent) and RUN_COMMAND in shell.execution_command:
            shell.execution_command = run_path


def resolve_parallelism(topology_class, env_name):
    """Collapse per-environment parallelism dicts down to this env's value.

    A spec may set ``par={"storm4": 8, "local": 1}`` so one topology file can
    serve several clusters. Thrift only accepts an int, so the dict has to be
    resolved before submission.

    A dict that has no entry for ``env_name`` is an error. Upstream used
    ``.get()`` here, which sent ``None`` to Nimbus; Nimbus reads that as "field
    absent" and silently runs the component at parallelism 1. On a topology
    sized for hundreds of executors that is an outage that submits cleanly, so
    refuse it instead and name what is missing.
    """
    missing = []
    for name, thrift_component in chain(
        topology_class.thrift_bolts.items(), topology_class.thrift_spouts.items()
    ):
        par_hint = thrift_component.common.parallelism_hint
        if isinstance(par_hint, dict):
            if env_name not in par_hint:
                missing.append((name, sorted(par_hint)))
                continue
            thrift_component.common.parallelism_hint = par_hint[env_name]

    if missing:
        detail = "; ".join(
            f"{name} (defines: {', '.join(envs)})" for name, envs in sorted(missing)
        )
        raise ValueError(
            f"No parallelism defined for env {env_name!r} on: {detail}. Add a "
            f"{env_name!r} key to each par dict -- submitting without one would "
            f"silently run these components at parallelism 1."
        )


# --------------------------------------------------------------- Nimbus


def _list_topologies(nimbus_client):
    """:returns: A list of running Storm topologies"""
    cluster_summary = nimbus_client.getClusterInfo()
    return cluster_summary.topologies


def _kill_topology(topology_name, nimbus_client, wait=None):
    kill_opts = KillOptions(wait_secs=wait)
    nimbus_client.killTopologyWithOpts(name=topology_name, options=kill_opts)


def is_safe_to_submit(topology_name, nimbus_client):
    """Is topology not in list of current topologies?"""
    topologies = _list_topologies(nimbus_client)
    return not any(topology.name == topology_name for topology in topologies)


def _kill_existing_topology(topology_name, force, wait, nimbus_client):
    if force and not is_safe_to_submit(topology_name, nimbus_client):
        print(f'Killing current "{topology_name}" topology.')
        sys.stdout.flush()
        _kill_topology(topology_name, nimbus_client, wait=wait)
        while not is_safe_to_submit(topology_name, nimbus_client):
            print(f"Waiting for topology {topology_name} to quit...")
            sys.stdout.flush()
            time.sleep(0.5)
        print("Killed.")
        sys.stdout.flush()


def _upload_jar(nimbus_client, local_path):
    upload_location = nimbus_client.beginFileUpload()
    print(
        f"Uploading topology jar {local_path} to assigned location: {upload_location}"
    )
    total_bytes = getsize(local_path)
    bytes_uploaded = 0
    with open(local_path, "rb") as local_jar:
        while True:
            print(f"Uploaded {bytes_uploaded}/{total_bytes} bytes", end="\r")
            sys.stdout.flush()
            curr_chunk = local_jar.read(THRIFT_CHUNK_SIZE)
            if not curr_chunk:
                break
            nimbus_client.uploadChunk(upload_location, curr_chunk)
            bytes_uploaded += len(curr_chunk)
        nimbus_client.finishFileUpload(upload_location)
        print(f"Uploaded {bytes_uploaded}/{total_bytes} bytes")
        sys.stdout.flush()
    return upload_location


def _submit_topology(
    topology_name,
    topology_class,
    remote_jar_path,
    config,
    env_config,
    nimbus_client,
    options=None,
    active=True,
):
    set_topology_serializer(env_config, config, topology_class)

    # Check if topology name is okay on Storm versions that support that
    if nimbus_storm_version(nimbus_client) >= (1, 1, 0):
        if not nimbus_client.isTopologyNameAllowed(topology_name):
            raise ValueError(
                f"Nimbus says {topology_name} is an invalid name for a Storm topology."
            )

    print(f"Submitting {topology_name} topology to nimbus...", end="")
    sys.stdout.flush()
    initial_status = (
        TopologyInitialStatus.ACTIVE if active else TopologyInitialStatus.INACTIVE
    )
    submit_options = SubmitOptions(initial_status=initial_status)
    nimbus_client.submitTopologyWithOpts(
        name=topology_name,
        uploadedJarLocation=remote_jar_path,
        jsonConf=json.dumps(options),
        topology=topology_class.thrift_topology,
        options=submit_options,
    )
    print("done")


def submit_topology(
    name=None,
    env_name=None,
    options=None,
    force=False,
    wait=None,
    override_name=None,
    local_jar_path=None,
    remote_jar_path=None,
    timeout=None,
    config_file=None,
    active=True,
):
    """Submit a topology to a remote Storm cluster."""
    config = get_config(config_file=config_file)
    name, topology_file = get_topology_definition(name, config_file=config_file)
    env_name, env_config = get_env_config(env_name, config_file=config_file)
    topology_class = get_topology_from_file(topology_file)
    if override_name is None:
        override_name = name
    if remote_jar_path and local_jar_path:
        warn("Ignoring local_jar_path because given remote_jar_path")
        local_jar_path = None

    # Handle option conflicts
    options = resolve_options(
        options, env_config, topology_class, override_name, timeout=timeout
    )

    check_install_virtualenv(options)

    # If using virtualenv, make sure paths are correct in specs
    if options.get("use_virtualenv", True):
        rewrite_execution_commands(
            topology_class,
            virtualenv_root=env_config.get("virtualenv_root", ".."),
            virtualenv_name=options.get("virtualenv_name", override_name),
        )

    # In case we're overriding things, let's save the original name
    options["topology.original_name"] = name

    # Set parallelism based on env_name if necessary
    resolve_parallelism(topology_class, env_name)

    if local_jar_path:
        print(f"Using prebuilt JAR: {local_jar_path}")
    elif not remote_jar_path:
        # Imported here rather than at module scope because pystorm_a8c.cli
        # imports both submit and jar; a top-level import would be a cycle.
        from pystorm_a8c.cli.jar import build_jar

        local_jar_path = build_jar()

    if name != override_name:
        print(f'Deploying "{name}" topology with name "{override_name}"...')
    else:
        print(f'Deploying "{name}" topology...')
    sys.stdout.flush()

    host, port = get_nimbus_host_port(env_config)
    nimbus_client = get_nimbus_client(env_config, host=host, port=port, timeout=timeout)
    if remote_jar_path:
        print(f"Reusing remote JAR on Nimbus server at path: {remote_jar_path}")
    else:
        remote_jar_path = _upload_jar(nimbus_client, local_jar_path)
    _kill_existing_topology(override_name, force, wait, nimbus_client)
    _submit_topology(
        override_name,
        topology_class,
        remote_jar_path,
        config,
        env_config,
        nimbus_client,
        options=options,
        active=active,
    )


# ------------------------------------------------------------------ CLI


def subparser_hook(subparsers):
    """Hook to add subparser for this command."""
    subparser = subparsers.add_parser("submit", description=__doc__, help=main.__doc__)
    subparser.set_defaults(func=main)
    # A path, not an argparse.FileType. submit_topology resolves the config
    # three times (get_config, get_topology_definition, get_env_config); an
    # open handle would be exhausted after the first read. FileType is also
    # deprecated as of Python 3.14.
    subparser.add_argument("--config", help="Specify path to config.json")
    subparser.add_argument(
        "-e",
        "--environment",
        help="The environment to use for the command. Corresponds to an "
        'environment in your "envs" dictionary in config.json. If you only '
        "have one environment specified, it is used automatically.",
    )
    subparser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force a topology to submit by killing any currently running "
        "topologies with the same name.",
    )
    subparser.add_argument(
        "-i",
        "--inactive",
        help="Submit topology as inactive instead of active. This is useful if "
        "you are migrating the topology to a new environment and already have "
        "it running actively in an older one.",
        action="store_false",
        dest="active",
    )
    subparser.add_argument(
        "-j",
        "--local_jar_path",
        help="Path to a prebuilt JAR to upload to Nimbus. This is useful when "
        "you have multiple topologies that all run out of the same JAR, or you "
        "have manually created the JAR.",
    )
    subparser.add_argument(
        "-n",
        "--name",
        help="The name of the topology to act on. If you have only one "
        'topology defined in your "topologies" directory, it is used '
        "automatically.",
    )
    subparser.add_argument(
        "-N",
        "--override_name",
        help="For operations such as killing/submitting topologies, use this "
        "value instead of NAME. This is useful if you want to submit the same "
        "topology twice without having to duplicate the topology file.",
    )
    subparser.add_argument(
        "-o",
        "--option",
        dest="options",
        action=_StoreDictAction,
        help='Topology option to pass on to Storm, e.g. "-o topology.debug=true".'
        " May be repeated for multiple options.",
    )
    subparser.add_argument(
        "-R",
        "--remote_jar_path",
        help="Path to a prebuilt JAR that already exists on your Nimbus server. "
        "This is useful when you have multiple topologies that all run out of "
        "the same JAR, and you do not want to upload it multiple times.",
    )
    subparser.add_argument(
        "--timeout",
        type=int,
        default=7000,
        help="Milliseconds to wait for Nimbus to respond. (default: %(default)s)",
    )
    subparser.add_argument(
        "--wait",
        type=int,
        default=5,
        help="Seconds to wait before killing topology. (default: %(default)s)",
    )


def main(args):
    """Submit a Storm topology to Nimbus."""
    submit_topology(
        name=args.name,
        env_name=args.environment,
        options=args.options,
        force=args.force,
        wait=args.wait,
        override_name=args.override_name,
        local_jar_path=args.local_jar_path,
        remote_jar_path=args.remote_jar_path,
        timeout=args.timeout,
        config_file=args.config,
        active=args.active,
    )
