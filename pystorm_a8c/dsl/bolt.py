"""
Bolt Specification

This module is called bolt to mirror organization of storm package.
"""

from pystorm_a8c.dsl.component import ShellComponentSpec


class ShellBoltSpec(ShellComponentSpec):
    """A :class:`ShellComponentSpec` that a Topology sorts into its bolts.

    No behaviour of its own. It exists as a distinct type because
    ``TopologyType`` dispatches on ``isinstance`` to tell a bolt from a spout.
    The ``__init__`` that used to be here re-declared all eight of the base
    class's parameters and forwarded every one of them unchanged.
    """
