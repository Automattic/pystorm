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
    set_topology_serializer,
)

THRIFT_CHUNK_SIZE = 307200

RUN_COMMAND = "pystorm-a8c-run"

#: Where the blobstore drops the virtualenv. Storm starts the multi-lang
#: subprocess with its cwd inside the unpacked ``resources/``, so ``..`` is the
#: worker directory, which is where the blobstore extracts ``localname``.
VIRTUALENV_NAME = "venv"
VIRTUALENV_ROOT = ".."
VIRTUALENV_BIN = f"{VIRTUALENV_ROOT}/{VIRTUALENV_NAME}/bin"

#: The venv's bin comes first so the component's interpreter and console
#: scripts win over anything installed on the supervisor.
WORKER_PATH = f"{VIRTUALENV_BIN}:/usr/local/bin:/usr/bin:/bin"

#: Options this package will not act on, and the reason. Refused wherever they
#: appear -- `-o`, the config.json env block, its `options` block, or a
#: Topology's `config` -- rather than accepted and quietly dropped.
UNSUPPORTED_OPTIONS = {
    "topology.blobstore.map": "built from --venv-blobstore-key",
    # Dotted, so it reads like a Storm setting, but Apache Storm has no such
    # key: it would be accepted and do nothing.
    "storm.workers.list": (
        "not an Apache Storm setting; it sized the topology as a side effect of "
        "streamparse's SSH fan-out. Use -o topology.workers=N instead"
    ),
    "install_virtualenv": "nothing here builds a virtualenv; the blobstore ships one",
    "use_virtualenv": "always on; there is no non-virtualenv worker layout",
    "virtualenv_flags": "nothing here runs the `virtualenv` command",
    "virtualenv_name": f"the venv is always {VIRTUALENV_ROOT}/{VIRTUALENV_NAME}",
    "virtualenv_root": f"the venv is always {VIRTUALENV_ROOT}/{VIRTUALENV_NAME}",
    "use_ssh_for_nimbus": "Nimbus is always contacted directly",
    # A config.json `log` block is checked with its keys prefixed, so these
    # name `log.path`, `log.file` and so on. Only `log.level` is honoured.
    "log_path": "worker logging goes to Storm's own logs via StormHandler",
    "log_file": "worker logging goes to Storm's own logs via StormHandler",
    "log_max_bytes": "there is no rotating file handler on the worker to size",
    "log_backup_count": "there is no rotating file handler on the worker to rotate",
}
# `serializer` is absent on purpose: util.set_topology_serializer consumes it.


# ------------------------------------------------------------ -o parsing


def parse_option(raw):
    """Parse a single ``-o key=value`` argument.

    Values are parsed as JSON when possible so ints, floats, bools and JSON
    objects survive; anything else stays a string. Bare words like ``on`` and
    ``yes`` therefore stay strings rather than becoming booleans.
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
        items = copy.copy(getattr(namespace, self.dest))
        try:
            key, val = parse_option(values)
        except ValueError as e:
            raise argparse.ArgumentError(self, str(e))
        items[key] = val
        setattr(namespace, self.dest, items)


def blobstore_options(venv_blobstore_key, environment=None):
    """The Storm settings that put a virtualenv on every worker.

    The blobstore ``localname``, the ``PATH`` and the execution command all
    encode the same path, so all of them are derived from the one key that
    varies rather than passed separately.

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

    ``PATH`` has to put the venv's ``bin`` first, or the component runs under
    whatever interpreter the supervisor happens to have.
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

    Storm conf keys are dotted and forwarded to Nimbus untouched. A bare word
    is addressed to ``pystorm-a8c`` itself, and there is nothing left for one
    to mean, so it is an error rather than a setting that does nothing.

    :param source: where these options came from, for the error message.
    """
    problems = []
    for key in sorted(options or {}):
        if key in UNSUPPORTED_OPTIONS:
            problems.append(f"{key} ({UNSUPPORTED_OPTIONS[key]})")
        elif "." not in key:
            problems.append(f"{key} (not a Storm conf key, and not one of ours)")
    if problems:
        raise ValueError(
            f"Unsupported {source}: {'; '.join(problems)}. Storm conf keys are "
            f"dotted and pass through untouched; the virtualenv is configured "
            f"by --venv-blobstore-key alone."
        )


def resolve_options(cli_options, env_config, topology_class, venv_blobstore_key=None):
    """Resolve potentially conflicting Storm options from three sources:

    CLI options > Topology options > config.json options

    The settings derived from ``venv_blobstore_key`` are applied last and are
    not overridable.

    .. note::
       The worker-count lookup runs before the submit's own Nimbus client
       exists, so it does not see ``--timeout`` and uses
       :data:`~pystorm_a8c.util.DEFAULT_NIMBUS_TIMEOUT_MS`. Passing the CLI
       value down would change ``get_storm_workers``'s signature, which
       casterisk-realtime's conftest.py depends on.
    """
    log_config = env_config.get("log", {})
    for options, source in (
        (env_config.get("options"), "options in config.json"),
        # The env block carries some of these as plain keys rather than under
        # `options`, and config.json really does set `virtualenv_root` -- so
        # without this it would be silently ignored, which is the failure this
        # check exists to prevent.
        (env_config, "keys in the config.json env block"),
        (
            {
                f"log_{k}": v
                for k, v in log_config.items()
                if f"log_{k}" in UNSUPPORTED_OPTIONS
            },
            "keys in the config.json log block",
        ),
        (topology_class.config, "Topology.config entries"),
        (cli_options, "-o options"),
    ):
        check_options_are_consumed(
            # The env block legitimately holds bare keys of its own -- nimbus,
            # workers, log -- so only the named-unsupported ones are checked
            # there; everywhere else a bare key is wrong by itself.
            (
                {k: v for k, v in options.items() if k in UNSUPPORTED_OPTIONS}
                if options is env_config
                else options
            ),
            source,
        )

    storm_options = {}
    storm_options.update(env_config.get("options", {}))

    # Informational only; nothing reads it. Built from the same constants as
    # the execution command so the two cannot name different directories.
    storm_options["topology.python.path"] = f"{VIRTUALENV_BIN}/python"

    if isinstance(log_config.get("level"), str):
        storm_options["pystorm.log.level"] = log_config["level"].lower()

    storm_options.update(topology_class.config)
    storm_options.update(cli_options or {})

    # `topology.environment` is the one derived setting a caller may add to:
    # extra variables are merged in, with PATH still ours.
    if venv_blobstore_key is not None:
        environment = storm_options.get("topology.environment")
        check_worker_environment(environment)
        storm_options.update(blobstore_options(venv_blobstore_key, environment))

    if storm_options.get("topology.debug", False):
        storm_options["pystorm.log.level"] = "debug"

    # One worker JVM per supervisor. Storm's own default is 1, which would run
    # an entire topology in a single process on a single host.
    # `topology.acker.executors` is left unset on purpose: Storm reads null as
    # "equal to the worker count", which is what we would set anyway.
    if storm_options.get("topology.workers") is None:
        storm_options["topology.workers"] = len(get_storm_workers(env_config))

    return storm_options


# ------------------------------------------------------ topology mangling


def rewrite_execution_commands(topology_class):
    """Point every shell component at the entry point inside the venv.

    Produces ``"../venv/bin/pystorm-a8c-run"``, matching the blobstore
    ``localname`` and the PATH from :func:`blobstore_options` -- all three come
    from the same constants, and the venv tarball has to contain that path.
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

    A dict with no entry for ``env_name`` is an error rather than a ``None``
    sent to Nimbus: Nimbus reads a missing ``parallelism_hint`` as "field
    absent" and runs the component at parallelism 1, so a topology sized for
    hundreds of executors would submit cleanly and come up crippled.
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

    Keyword-only on purpose, so that a call written against an older argument
    order fails instead of binding a topology name to ``venv_blobstore_key``.

    :param venv_blobstore_key: blobstore key of the virtualenv tarball the
                               workers run out of. Required: there is no
                               worker layout without one.
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
    options = resolve_options(
        options, env_config, topology_class, venv_blobstore_key=venv_blobstore_key
    )
    rewrite_execution_commands(topology_class)
    options["topology.original_name"] = name
    resolve_parallelism(topology_class, env_name)

    if local_jar_path:
        print(f"Using prebuilt JAR: {local_jar_path}")
    elif not remote_jar_path:
        # Imported here, not at module scope: pystorm_a8c.cli imports both
        # submit and jar, so a top-level import would be a cycle.
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
        "untouched; a bare word is refused, because this package has no "
        "settings of its own.",
    )
    subparser.add_argument(
        "--venv-blobstore-key",
        dest="venv_blobstore_key",
        required=True,
        metavar="KEY",
        help="Blobstore key of the virtualenv tarball the workers run out of. "
        f"Derives the blobstore map, the worker PATH, and the {RUN_COMMAND!r} "
        f"path inside {VIRTUALENV_BIN!r}.",
    )
    jar = subparser.add_mutually_exclusive_group()
    jar.add_argument(
        "-j",
        "--local_jar_path",
        help="Path to a prebuilt JAR to upload to Nimbus. This is useful when "
        "you have multiple topologies that all run out of the same JAR, or you "
        "have manually created the JAR.",
    )
    jar.add_argument(
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
