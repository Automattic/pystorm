"""
Bolt Specification

This module is called bolt to mirror organization of storm package.
"""

from pystorm_a8c.dsl.component import ShellComponentSpec


class ShellBoltSpec(ShellComponentSpec):
    """A :class:`ShellComponentSpec` that a Topology sorts into its bolts.

    A marker type with no behaviour of its own: ``TopologyType`` dispatches on
    ``isinstance`` to tell a bolt from a spout.
    """
