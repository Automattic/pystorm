# pystorm-a8c

Slim Apache Storm multi-lang runtime, topology DSL, and Nimbus submit client
for Parse.ly.

This package replaces the two Automattic forks `pystorm` and `streamparse`
with a single distribution containing only the codepaths that are actually
used: the runtime component classes, the topology DSL, a `submit` command that
talks directly to Nimbus over Thrift, and a `jar` command that assembles the
topology archive with the standard library.

Target: **Apache Storm 1.2.x**. One runtime dependency: `thriftpy2`.

See `doc/plans/2026-08-05-pystorm-a8c.md` for the full design and rationale.

## Install

```bash
pip install pystorm-a8c
```

Two console scripts are installed:

| Script | Who runs it |
|---|---|
| `pystorm-a8c` | You, to build and submit topologies |
| `pystorm_a8c_run` | Storm, on each worker, to start a bolt or spout |

You never invoke `pystorm_a8c_run` yourself. It is what a component's
`execution_command` points at, and it imports the class named by `script`.

## Writing a topology

```python
# src/wordcount/components.py
from pystorm_a8c import Bolt, Spout


class WordSpout(Spout):
    outputs = ["word"]

    def next_tuple(self):
        self.emit(["dog"])


class CountBolt(Bolt):
    outputs = ["word", "count"]

    def process(self, tup):
        self.emit([tup.values[0], 1])
```

```python
# topologies/wordcount.py
from pystorm_a8c import Grouping, Topology
from wordcount.components import CountBolt, WordSpout


class WordCount(Topology):
    word_spout = WordSpout.spec(par=2)
    count_bolt = CountBolt.spec(
        inputs={word_spout: Grouping.fields("word")},
        par={"storm4": 8, "local": 1},
    )
```

`par` accepts an int or a dict keyed by environment name; the dict is resolved
to this environment's value at submit time.

If a `par` dict has no key for the environment you are submitting to, the
submit **fails** and names every component that is missing one. `streamparse`
passed `None` through to Nimbus here, which Nimbus reads as "field absent" and
runs the component at parallelism 1 — a topology sized for hundreds of
executors would come up crippled with nothing in the log to say so.

## The CLI

There are exactly two subcommands.

### `pystorm-a8c jar`

```bash
pystorm-a8c jar --src src -o .artifacts/topology.jar
```

Zips `src/` into `resources/` inside the archive — that is the entire payload
of a topology JAR for a pure-Python topology. Storm unpacks it into the worker
directory and `pystorm_a8c_run` adds `resources/` to `sys.path`.

Symlinked directories are followed. Timestamps are fixed, so an unchanged tree
rebuilds byte-identically.

### `pystorm-a8c submit`

```bash
pystorm-a8c submit \
    -e storm4 \
    -n raws \
    -N raws_prod \
    -j .artifacts/topology.jar \
    -f \
    --wait 30 \
    -o 'install_virtualenv=0' \
    -o 'virtualenv_name=venv' \
    -o 'topology.blobstore.map={"myproject-venv-abc123_tar_gz":{"localname":"venv","uncompress":true}}' \
    -o 'topology.environment={"PATH":"../venv/bin:/usr/local/bin:/usr/bin:/bin"}' \
    -o 'topology.max.spout.pending=200'
```

| Flag | Meaning |
|---|---|
| `-e`, `--environment` | Which `envs` entry in config.json to use |
| `-n`, `--name` | Topology definition file to load (without `.py`) |
| `-N`, `--override_name` | Submit under a different name than the file |
| `-j`, `--local_jar_path` | Upload this prebuilt JAR |
| `-R`, `--remote_jar_path` | Reuse a JAR already on the Nimbus server |
| `-f`, `--force` | Kill a running topology of the same name first |
| `-i`, `--inactive` | Submit deactivated |
| `--wait` | Seconds to wait when killing (default 5) |
| `--timeout` | Milliseconds to wait for Nimbus (default 7000) |
| `-o`, `--option` | `key=value` passed through to the Storm conf; repeatable |
| `--config` | Path to config.json |

`-o` values are parsed as JSON when possible and left as strings otherwise, so
`-o topology.workers=4` gives an int, `-o topology.environment={...}` gives a
dict, and `-o virtualenv_flags=-p /usr/bin/python3.9` gives a string.

The virtualenv is expected to reach the workers through the Storm blobstore.
`submit` rewrites each component's `execution_command` to
`<virtualenv_root>/<virtualenv_name>/bin/pystorm_a8c_run`, which must match the
blobstore `localname` and the `PATH` you set via `-o topology.environment`.

## config.json

Only these keys are read. Anything else is ignored.

```json
{
  "topology_specs": "topologies/",
  "envs": {
    "storm4": {
      "nimbus": "storm-ha.example.com:6627",
      "virtualenv_root": "..",
      "workers": ["supervisor01", "supervisor02"],
      "log": { "level": "info", "path": "/var/log/storm" },
      "options": {
        "topology.message.timeout.secs": 60,
        "topology.max.spout.pending": 500
      }
    }
  }
}
```

| Key | Meaning |
|---|---|
| `topology_specs` | Directory holding topology definition files |
| `envs.<name>.nimbus` | `host` or `host:port` (port defaults to 6627) |
| `envs.<name>.virtualenv_root` | Prefix for the worker venv path (default `..`) |
| `envs.<name>.workers` | Supervisor hosts. If absent, Nimbus is asked |
| `envs.<name>.log` | `level`, `path`, `file`, `max_bytes`, `backup_count` |
| `envs.<name>.options` | Storm conf defaults, overridden by `-o` |
| `serializer` | Accepted only as `"json"` — see below |

Option precedence, lowest to highest:
`envs.<name>.options` → log/venv keys → the `Topology` class's `config` → `-o`.

`use_ssh_for_nimbus` is accepted and **ignored**, with a warning. Nimbus is
always contacted directly.

`serializer` is accepted only when set to `"json"`. Any other value is a hard
error rather than a silent downgrade — JSON is the only wire protocol.

### `PYSTORM_A8C_NIMBUS`

Set this environment variable to override the `nimbus` value from config.json
entirely:

```bash
PYSTORM_A8C_NIMBUS=other-nimbus:6627 pystorm-a8c submit -e storm4 -n raws
```

Useful for pointing an existing config at a different cluster without editing
it.

## Migrating from `streamparse`

There is **no `streamparse` compatibility shim**. `import streamparse` raises
`ImportError`, and every import path has to move to `pystorm_a8c` as part of
the same change that adopts this package. A migration that misses one is a
failure at worker start, not a warning — grep before you deploy:

```bash
grep -rn '\bstreamparse\b' --include='*.py' .
```

The mapping is one-to-one:

| Old import | New import |
|---|---|
| `from streamparse import Bolt, BatchingBolt, TicklessBatchingBolt` | `from pystorm_a8c.bolt import Bolt, BatchingBolt, TicklessBatchingBolt` |
| `from streamparse import Spout, ReliableSpout` | `from pystorm_a8c.spout import Spout, ReliableSpout` |
| `from streamparse import Tuple, Component` | `from pystorm_a8c.component import Tuple, Component` |
| `from streamparse import Topology` | `from pystorm_a8c.dsl.topology import Topology` |
| `from streamparse import Stream, Grouping` | `from pystorm_a8c.dsl.stream import Stream, Grouping` |
| `from streamparse import StormWentAwayError` | `from pystorm_a8c.exceptions import StormWentAwayError` |
| `from streamparse.bolt import ...` | `from pystorm_a8c.bolt import ...` |
| `from streamparse.spout import ...` | `from pystorm_a8c.spout import ...` |
| `from streamparse.util import get_env_config, get_storm_workers` | `from pystorm_a8c.util import get_env_config, get_storm_workers` |
| `from streamparse.version import __version__` | `from pystorm_a8c import __version__` |

Every name above is also re-exported from the `pystorm_a8c` root, so
`from pystorm_a8c import Bolt, Topology, Stream` works too and is what the
casterisk topologies use.

Two `streamparse` names have **no** replacement, because the code behind them
was deleted rather than moved:

- `streamparse.util.activate_env` — Fabric/SSH remote execution. Gone; there
  is no SSH in this package. See "What was removed and why".
- `streamparse.util.prepare_topology` — copied `src/` into `_resources/` for
  the `lein` build. Gone; `pystorm-a8c jar` zips `src/` directly.

> **History:** `1.1.x` shipped a deprecated `streamparse` forwarding module so
> that a consumer could migrate its imports gradually. `1.2.0` deletes it. If
> you are moving off `1.1.x` and were still relying on the shim, do the import
> rewrite first, then upgrade.

## What was removed and why

The two forks together carried a large surface that Parse.ly never called.
Each removal below is one less thing to keep working across six Python
versions.

| Removed | Why |
|---|---|
| SSH / Fabric / Paramiko (`ssh_tunnel`, `activate_env`, `run_cmd`, remote log tailing) | Nimbus is reachable directly. Everything SSH-shaped existed to reach hosts that are now reached by other means. |
| `lein`, `project.clj`, the JDK build chain | A pure-Python topology JAR contains no Java. It is a ZIP of `resources/`, which the stdlib can write. |
| Virtualenv creation over SSH (`update_virtualenv`, `install_virtualenv`) | Virtualenvs ship to workers through the Storm blobstore. `install_virtualenv` is still accepted but warns if truthy. |
| The pluggable serializer indirection and `msgpack` | JSON was the only serializer ever configured. |
| Nine unused subcommands (`run`, `visualize`, `quickstart`, `tail`, `list`, `kill`, `restart`, `stats`, ...) | `sparse` auto-discovered its subcommands, which is how these persisted unnoticed. The two that are used are now registered explicitly. |
| Java component specs, Flux emission | Flux was never on the submit path; submission builds native Storm Thrift types. |
| `six`, `simplejson`, `ruamel.yaml`, `texttable`, `requests`, `pkg_resources` | Replaced by the standard library. A test asserts none of them come back. |

## Versioning

Plain `MAJOR.MINOR.PATCH`, with one twist:

```
1 . 0 . 0
│   │   └── patch: fixes
│   └────── minor: features AND breaking Python-API changes
└────────── MAJOR: the Apache Storm major version this package supports
```

**The major version tracks Apache Storm, not this package's Python API.**
`1.x` supports Storm 1.x. Storm 2.x support will be released as `2.0.0`.

**Because the major position is spoken for, breaking changes to this
package's own API ship in the minor position.** Read the changelog before a
minor bump, not just before a major one. This is the one thing about our
versioning that will surprise you.

Cutting a release is written up in [doc/RELEASING.md](doc/RELEASING.md).

There is no epoch and no local version label. The distribution name
`pystorm-a8c` already distinguishes this package from upstream `pystorm`, so
neither would order or identify anything the name doesn't. The `+a8c.N`
suffixes you may remember belong to the retired `pystorm==3.1.4+a8c.1` and
`streamparse==5.0.0+a8c.1` builds, which were published under the *upstream*
names and needed the marker.

### Pinning

```
pystorm-a8c==1.2.0
```

### Git tags

Releases are tagged `pystorm-a8c-v1.2.0`, not `v1.2.0`. This branch lives in
the `Automattic/pystorm` fork and inherits upstream pystorm's tag history,
which already includes `v1.0.0` through `v3.1.4` — the bare names collide.

## Development

```bash
make test        # current interpreter
make test-all    # 3.9 through 3.14
make lint        # black --check
make fmt         # black
make dist        # build the wheel and sdist
```
