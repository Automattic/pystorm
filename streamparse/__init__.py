"""DEPRECATED backward-compatibility shim for the retired `streamparse` package.

Everything here forwards to :mod:`pystorm_a8c` and emits a
:class:`DeprecationWarning`. Nothing new should import from this package.

TODO(pystorm-a8c): remove this entire `streamparse/` package once no consumer
imports it. Removal checklist:
  1. `grep -rn "streamparse" --include=*.py` across every consuming repo
     returns nothing (casterisk-realtime is migrated in Task 14).
  2. Delete this directory.
  3. Drop "streamparse" from `module-name` in pyproject.toml.
  4. Delete test/unit/test_streamparse_compat.py.
  5. Release a MINOR version bump -- removing this is a breaking Python-API
     change, and MAJOR is reserved for the Storm major line (see README).
"""

import importlib
import warnings

#: Public name -> the canonical module it now lives in. Used for the warning
#: message; the object itself is always fetched from the `pystorm_a8c` root.
_MOVED = {
    "Bolt": "pystorm_a8c.bolt",
    "BatchingBolt": "pystorm_a8c.bolt",
    "TicklessBatchingBolt": "pystorm_a8c.bolt",
    "Spout": "pystorm_a8c.spout",
    "ReliableSpout": "pystorm_a8c.spout",
    "Component": "pystorm_a8c.component",
    "Tuple": "pystorm_a8c.component",
    "Topology": "pystorm_a8c.dsl.topology",
    "Stream": "pystorm_a8c.dsl.stream",
    "Grouping": "pystorm_a8c.dsl.stream",
    "StormWentAwayError": "pystorm_a8c.exceptions",
}

__all__ = sorted(_MOVED)


def _warn(old, new, stacklevel=3):
    warnings.warn(
        f"{old} is deprecated and will be removed; use {new} instead. "
        "The `streamparse` package name is a temporary compatibility shim "
        "over `pystorm_a8c`.",
        DeprecationWarning,
        stacklevel=stacklevel,
    )


def __getattr__(name):
    # PEP 562: only called for names not already in module globals.
    if name not in _MOVED:
        raise AttributeError(f"module 'streamparse' has no attribute {name!r}")
    _warn(f"streamparse.{name}", f"{_MOVED[name]}.{name}")
    value = getattr(importlib.import_module("pystorm_a8c"), name)
    # Cache in globals so `from streamparse import X` -- which looks the
    # attribute up more than once -- warns exactly once per symbol per process.
    globals()[name] = value
    return value


def __dir__():
    return list(__all__)
