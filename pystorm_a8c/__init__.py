from importlib.metadata import version as _dist_version

from pystorm_a8c.bolt import BatchingBolt, Bolt, TicklessBatchingBolt
from pystorm_a8c.component import Component, Tuple
from pystorm_a8c.dsl.stream import Grouping, Stream
from pystorm_a8c.dsl.topology import Topology
from pystorm_a8c.exceptions import StormWentAwayError
from pystorm_a8c.spout import ReliableSpout, Spout

# Read from the installed distribution, because pyproject.toml holds the only
# literal: uv_build rejects `dynamic = ["version"]`. Raises
# PackageNotFoundError from an uninstalled source checkout, which is preferred
# to reporting a placeholder version into a worker log.
#
# MAJOR tracks the Apache Storm major line, so breaking changes to this
# package's own API go in MINOR. See README.md "Versioning".
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
