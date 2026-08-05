"""
Spout Specification

This module is called spout to mirror organization of storm package.
"""

from pystorm_a8c.dsl.component import ShellComponentSpec


class ShellSpoutSpec(ShellComponentSpec):
    """A :class:`ShellComponentSpec` that a Topology sorts into its spouts.

    A marker type for ``TopologyType``'s ``isinstance`` dispatch. A spout takes
    no ``inputs``; the base class defaults them to None.
    """
