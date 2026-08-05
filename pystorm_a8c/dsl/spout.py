"""
Spout Specification

This module is called spout to mirror organization of storm package.
"""

from pystorm_a8c.dsl.component import ShellComponentSpec


class ShellSpoutSpec(ShellComponentSpec):
    """A :class:`ShellComponentSpec` that a Topology sorts into its spouts.

    Like :class:`~pystorm_a8c.dsl.bolt.ShellBoltSpec`, a marker type for
    ``TopologyType``'s ``isinstance`` dispatch rather than a behaviour.

    A spout takes no ``inputs``; the base class defaults it to None and
    ``Spout.spec`` never passes one. Redeclaring the whole parameter list here
    to omit it made the omission a promise this class could not keep anyway --
    ``TopologyType.add_spout_spec`` is what actually decides a spout is a
    spout.
    """
