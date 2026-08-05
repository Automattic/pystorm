"""DEPRECATED -- use :mod:`pystorm_a8c.bolt`. TODO: remove with the shim."""

from pystorm_a8c.bolt import BatchingBolt, Bolt, TicklessBatchingBolt  # noqa: F401

from streamparse import _warn

_warn("streamparse.bolt", "pystorm_a8c.bolt", stacklevel=2)

__all__ = ["BatchingBolt", "Bolt", "TicklessBatchingBolt"]
