from importlib.metadata import version as _dist_version

from pystorm_a8c.bolt import BatchingBolt, Bolt, TicklessBatchingBolt
from pystorm_a8c.component import Component, Tuple
from pystorm_a8c.dsl.stream import Grouping, Stream
from pystorm_a8c.dsl.topology import Topology
from pystorm_a8c.exceptions import StormWentAwayError
from pystorm_a8c.spout import ReliableSpout, Spout

# The version is declared once, in pyproject.toml, and read back here from the
# installed distribution metadata. The uv_build backend requires a literal
# `version` in pyproject.toml -- it rejects `dynamic = ["version"]` -- so this
# direction is the only one that keeps a single source of truth.
#
# Version scheme: MAJOR.MINOR.PATCH, where MAJOR is the Apache Storm major
# version this package supports (1.x <-> Storm 1.x). Because MAJOR is spoken
# for by Storm, breaking changes to this package's own Python API go in the
# MINOR position. See README.md "Versioning".
#
# This raises PackageNotFoundError if pystorm_a8c is imported from a source
# checkout that was never installed. That is intentional: every supported
# workflow (`uv run`, `uv sync`, the deployed blobstore venv) installs the
# package, and a loud failure beats silently reporting a placeholder version
# into a worker log.
__version__ = _dist_version("pystorm-a8c")

__all__ = [
    "BatchingBolt",
    "Bolt",
    "Component",
    "Grouping",
    "ReliableSpout",
    "Spout",
    "Stream",
    "StormWentAwayError",
    "TicklessBatchingBolt",
    "Topology",
    "Tuple",
    "__version__",
]
