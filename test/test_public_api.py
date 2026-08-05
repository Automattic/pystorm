"""
Pins the importable surface consumers are allowed to rely on.
"""

import pytest

EXPECTED = [
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


def test_public_api_is_exactly_the_agreed_surface():
    import pystorm_a8c

    assert sorted(pystorm_a8c.__all__) == sorted(EXPECTED)


@pytest.mark.parametrize("name", EXPECTED)
def test_every_exported_name_resolves(name):
    import pystorm_a8c

    assert getattr(pystorm_a8c, name) is not None


def test_batching_bolt_is_exported_even_though_casterisk_never_uses_it():
    """The one name on the list that is not consumer-driven.

    Every other export is there because casterisk imports it. `BatchingBolt`
    is kept and exported as a deliberate exception (Task 4): it is the
    tick-driven batching option, and upstream `streamparse` exported it from
    its root too, so exporting it here keeps the compat shim a straight alias.
    """
    import pystorm_a8c
    from pystorm_a8c.bolt import BatchingBolt

    assert pystorm_a8c.BatchingBolt is BatchingBolt
    assert issubclass(pystorm_a8c.TicklessBatchingBolt, BatchingBolt)


def test_submodule_import_paths_used_by_casterisk():
    # casterisk imports from both the top level and the submodules.
    from pystorm_a8c.bolt import BatchingBolt, Bolt, TicklessBatchingBolt  # noqa: F401
    from pystorm_a8c.spout import ReliableSpout  # noqa: F401
