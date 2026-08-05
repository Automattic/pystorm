"""DEPRECATED -- use :mod:`pystorm_a8c.spout`. TODO: remove with the shim."""

from pystorm_a8c.spout import ReliableSpout, Spout  # noqa: F401

from streamparse import _warn

_warn("streamparse.spout", "pystorm_a8c.spout", stacklevel=2)

__all__ = ["ReliableSpout", "Spout"]
