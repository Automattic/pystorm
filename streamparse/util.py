"""DEPRECATED -- use :mod:`pystorm_a8c.util`. TODO: remove with the shim."""

from pystorm_a8c.util import (  # noqa: F401
    die,
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

from streamparse import _warn

_warn("streamparse.util", "pystorm_a8c.util", stacklevel=2)
