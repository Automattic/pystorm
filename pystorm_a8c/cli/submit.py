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

RUN_COMMAND = "pystorm-a8c-run"

#: Where the blobstore drops the virtualenv, and what it is called there.
#:
#: These are constants, not settings. Storm starts the multi-lang subprocess
#: with its cwd already inside the unpacked ``resources/``, so ``..`` is the
#: worker directory -- which is where the blobstore extracts ``localname``.
#: Every piece of the deploy has to agree on this one path, so it is derived
#: in one place rather than assembled from options that could disagree.
VIRTUALENV_NAME = "venv"
VIRTUALENV_ROOT = ".."
VIRTUALENV_BIN = f"{VIRTUALENV_ROOT}/{VIRTUALENV_NAME}/bin"

#: PATH the workers run with. The venv's bin comes first so the component's
#: interpreter and console scripts win over anything on the supervisor.
WORKER_PATH = f"{VIRTUALENV_BIN}:/usr/local/bin:/usr/bin:/bin"

#: Storm conf keys that ``--venv-blobstore-key`` builds outright. Passing one
#: by hand is refused rather than merged: two sources for one path is how the
#: execution command and the blobstore localname drift apart.
DERIVED_OPTIONS = {
    "topology.blobstore.map": "built from --venv-blobstore-key",
}

#: Options this package used to act on and no longer does. Refused with the
#: reason, rather than accepted and quietly ignored.
_FIXED_VENV_PATH = (
    f"the venv is always unpacked as {VIRTUALENV_BIN.rsplit('/', 1)[0]!r} and is "
    f"not configurable; name the tarball with --venv-blobstore-key"
)
RETIRED_OPTIONS = {
    "install_virtualenv": (
        "nothing here builds a virtualenv any more -- the blobstore ships one"
    ),
    "use_virtualenv": "always on; there is no non-virtualenv worker layout",
    "virtualenv_flags": (
        "these were flags for the `virtualenv` command this package used to run "
        "over SSH"
    ),
    "virtualenv_name": _FIXED_VENV_PATH,
    "virtualenv_root": _FIXED_VENV_PATH,
    "use_ssh_for_nimbus": "Nimbus is always contacted directly",
}
# `serializer` is deliberately absent: it is still consumed, by
# util.set_topology_serializer, which accepts "json" and refuses anything else.


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


def blobstore_options(venv_blobstore_key, environment=None):
    """The Storm settings that put a virtualenv on every worker.

    ``bin/topo-submit`` used to spell these out as four ``-o`` flags whose
    values had to agree with each other -- the blobstore ``localname``, the
    ``PATH``, and two virtualenv keys that all encode the same path. Deriving
    them from the one key that actually varies removes the chance to get that
    agreement wrong.

    ``virtualenv_name`` and ``virtualenv_root`` are deliberately *not* emitted.
    They are constants now, and nothing -- here or in Storm -- reads them, so
    sending them would just put two more dead keys in the topology conf.

    :param environment: extra worker environment variables to carry alongside
                        the derived ``PATH``. ``PATH`` itself is not accepted;
                        see :func:`check_worker_environment`.
    """
    return {
        "topology.blobstore.map": {
            venv_blobstore_key: {"localname": VIRTUALENV_NAME, "uncompress": True}
        },
        "topology.environment": {**(environment or {}), "PATH": WORKER_PATH},
    }


def check_worker_environment(environment):
    """``topology.environment`` may add variables, but not redefine ``PATH``.

    Setting extra variables is ordinary -- ``TZ``, ``LD_LIBRARY_PATH``, an SDK
    credential. ``PATH`` is not: it has to put the venv's ``bin`` first, or the
    component runs under whatever interpreter the supervisor happens to have,
    which is the failure the derived value exists to prevent.
    """
    if environment is None:
        return
    if not isinstance(environment, dict):
        raise ValueError(
            f"topology.environment must be a dict of environment variables, "
            f"got {environment!r}"
        )
    if "PATH" in environment:
        raise ValueError(
            f"topology.environment must not set PATH: it is derived as "
            f"{WORKER_PATH!r} so that the venv delivered by "
            f"--venv-blobstore-key comes first. Set the other variables you "
            f"need and leave PATH out."
        )


def check_options_are_consumed(options, source):
    """Refuse any option this package will not act on.

    Storm conf keys are dotted -- ``topology.*``, ``storm.*``, ``pystorm.*`` --
    and are forwarded to Nimbus untouched. A bare word is addressed to
    ``pystorm-a8c`` itself, and after the virtualenv settings became constants
    there is nothing left for one to mean. Silently passing it on is how
    ``virtualenv_flags`` sat in the topology conf for years doing nothing.

    :param source: where these options came from, for the error message.
    """
    problems = []
    for key in sorted(options or {}):
        if key in DERIVED_OPTIONS:
            problems.append(f"{key} ({DERIVED_OPTIONS[key]})")
        elif key in RETIRED_OPTIONS:
            problems.append(f"{key} ({RETIRED_OPTIONS[key]})")
        elif "." not in key:
            problems.append(f"{key} (not a Storm conf key, and not one of ours)")
    if problems:
        raise ValueError(
            f"Unsupported {source}: {'; '.join(problems)}. Storm conf keys are "
            f"dotted and pass through untouched; the virtualenv is configured "
            f"by --venv-blobstore-key alone."
        )


def resolve_options(
    cli_options,
    env_config,
    topology_class,
    topology_name,
    local_only=False,
    venv_blobstore_key=None,
):
    """Resolve potentially conflicting Storm options from three sources:

    CLI options > Topology options > config.json options

    The settings derived from ``venv_blobstore_key`` are applied last of all,
    because nothing else is allowed to set them -- see
    :func:`check_options_are_consumed`.

    :param local_only: Whether or not we should talk to Nimbus to get Storm
                       workers and other info.

    .. note::
       The worker-list lookup below runs before the submit's own Nimbus client
       exists, so it does not see ``--timeout``. It uses
       :data:`~pystorm_a8c.util.DEFAULT_NIMBUS_TIMEOUT_MS` instead, which is
       the same value that flag defaults to. Threading the CLI value down to
       ``get_storm_workers`` would mean changing its signature, and
       casterisk-realtime's conftest.py replaces that function.
    """
    check_options_are_consumed(env_config.get("options"), "options in config.json")
    # The env block carries these as plain keys rather than under `options`.
    # DERIVED_OPTIONS is included because config.json really does set
    # `virtualenv_root`: without this it would be silently ignored, which is
    # the failure mode this check exists to prevent.
    check_options_are_consumed(
        {
            k: v
            for k, v in env_config.items()
            if k in RETIRED_OPTIONS or k in DERIVED_OPTIONS
        },
        "keys in the config.json env block",
    )
    check_options_are_consumed(topology_class.config, "Topology.config entries")
    check_options_are_consumed(cli_options, "-o options")

    storm_options = {}

    # Start with environment options
    storm_options.update(env_config.get("options", {}))

    # Set topology.python.path. Built from the same constants as the execution
    # command, so the two cannot name different directories -- they used to,
    # whenever virtualenv_name was set, because this line used the topology
    # name instead. Informational only; no pystorm-a8c code reads it.
    storm_options["topology.python.path"] = f"{VIRTUALENV_BIN}/python"

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

    # Override options with topology options
    storm_options.update(topology_class.config)

    # Override options with CLI options
    storm_options.update(cli_options or {})

    # The venv wiring goes on last and is not overridable: every other source
    # was just checked for these keys and refused. `topology.environment` is
    # the exception -- extra variables are merged in, with PATH still ours.
    if venv_blobstore_key is not None:
        environment = storm_options.get("topology.environment")
        check_worker_environment(environment)
        storm_options.update(blobstore_options(venv_blobstore_key, environment))

    # Set log level to debug if topology.debug is set
    if storm_options.get("topology.debug", False):
        storm_options["pystorm.log.level"] = "debug"

    # If ackers and executors still aren't set, use number of worker nodes
    if not local_only:
        if not storm_options.get("storm.workers.list"):
            storm_options["storm.workers.list"] = get_storm_workers(env_config)
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


def rewrite_execution_commands(topology_class):
    """Point every shell component at the entry point inside the venv.

    Produces ``"../venv/bin/pystorm-a8c-run"``, matching the blobstore
    ``localname`` and the PATH that :func:`blobstore_options` sets, because all
    three are built from the same constants. This is the a8c deploy mechanism;
    the path has to line up with the venv tarball that was uploaded under
    ``--venv-blobstore-key``.
    """
    run_path = f"{VIRTUALENV_BIN}/{RUN_COMMAND}"
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
    *,
    venv_blobstore_key,
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
    """Submit a topology to a remote Storm cluster.

    :param venv_blobstore_key: blobstore key of the virtualenv tarball the
                               workers run out of. Required: there is no
                               worker layout without one.

    .. note::
       Every parameter is keyword-only, and deliberately so. This function used
       to begin ``name=None``, so had ``venv_blobstore_key`` been added as a
       leading positional, an existing ``submit_topology("raws")`` would have
       kept working while silently meaning something else entirely -- a
       blobstore key of "raws" and an auto-discovered topology. Keyword-only
       turns that into a TypeError at the call site.
    """
    if not venv_blobstore_key:
        raise ValueError(
            "venv_blobstore_key is required: workers run out of a virtualenv "
            "delivered by the Storm blobstore, and the key names the tarball."
        )
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
        options,
        env_config,
        topology_class,
        override_name,
        venv_blobstore_key=venv_blobstore_key,
    )

    # Point the specs at the venv the blobstore is about to deliver.
    rewrite_execution_commands(topology_class)

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
        help='Storm conf setting, e.g. "-o topology.debug=true". May be '
        "repeated. Keys are dotted Storm conf names and pass through "
        "untouched; a bare word is refused, because this package no longer "
        "has settings of its own.",
    )
    subparser.add_argument(
        "--venv-blobstore-key",
        dest="venv_blobstore_key",
        required=True,
        metavar="KEY",
        help="Blobstore key of the virtualenv tarball the workers run out of. "
        f"Sets up the blobstore map, the worker PATH, and the "
        f"{RUN_COMMAND!r} path inside {VIRTUALENV_BIN!r} -- all of which have "
        "to agree, which is why they are derived from this one value rather "
        "than passed separately.",
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
        venv_blobstore_key=args.venv_blobstore_key,
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
