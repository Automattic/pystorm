"""DEPRECATED -- use :attr:`pystorm_a8c.__version__`. TODO: remove with the shim."""

from pystorm_a8c import __version__  # noqa: F401

from streamparse import _warn

_warn("streamparse.version", "pystorm_a8c.__version__", stacklevel=2)

__all__ = ["__version__"]
