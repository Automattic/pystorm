import io
import signal
import time
from unittest.mock import patch

import pytest

from pystorm_a8c.bolt import TicklessBatchingBolt
from pystorm_a8c.component import Tuple


class CollectingBolt(TicklessBatchingBolt):
    secs_between_batches = 0.05

    def initialize(self, conf, ctx):
        self.processed = []

    def process_batch(self, key, tups):
        self.processed.append((key, list(tups)))


class ExplodingBolt(TicklessBatchingBolt):
    secs_between_batches = 0.05
    exit_on_exception = False

    def process_batch(self, key, tups):
        raise ValueError("batch blew up")


def make_tup(i):
    return Tuple(id=str(i), component="c", stream="default", task=1, values=[i])


_BUILT = []


@pytest.fixture(autouse=True)
def park_batcher_threads():
    """Stop built bolts' batcher threads from outliving their test.

    `TicklessBatchingBolt` starts a daemon thread in `__init__` and has no stop
    hook -- in production the thread lives as long as the worker process, which
    is the point. In tests it means an `ExplodingBolt` keeps raising every
    `secs_between_batches` for the rest of the session and, once the test's
    `patch("os.kill")` is undone, fires a real SIGUSR1 into whatever pytest is
    doing at the time. Park each thread on a long sleep instead.
    """
    yield
    while _BUILT:
        bolt = _BUILT.pop()
        bolt.secs_between_batches = 3600
        bolt.process_batches = lambda: None


def build(cls):
    # BytesIO, not StringIO: _wrap_stream puts a TextIOWrapper around whatever
    # it is given, and a TextIOWrapper writes bytes -- which StringIO rejects.
    bolt = cls(input_stream=io.BytesIO(), output_stream=io.BytesIO())
    bolt.initialize({}, {})
    _BUILT.append(bolt)
    return bolt


def test_batcher_thread_is_daemon_and_named():
    bolt = build(CollectingBolt)
    assert bolt._batcher.daemon is True
    assert bolt._batcher.name == "CollectingBolt:_batcher-thread"


def test_batches_are_processed_on_the_timer():
    bolt = build(CollectingBolt)
    with patch.object(bolt, "ack"):
        bolt.process(make_tup(1))
        bolt.process(make_tup(2))
        time.sleep(0.2)
    assert bolt.processed, "batcher thread never ran process_batch"
    key, tups = bolt.processed[0]
    assert key is None
    assert [t.values[0] for t in tups] == [1, 2]


def test_batcher_thread_survives_an_exception_and_keeps_batching():
    """Regression guard for a8c commit 24fe387.

    Before that fix the try/except sat outside the `while True`, so the first
    exception killed the batcher thread permanently and, with
    exit_on_exception=False, the bolt silently stopped batching forever.
    """
    bolt = build(ExplodingBolt)
    with (
        patch.object(bolt, "raise_exception"),
        patch.object(bolt, "fail"),
        patch("os.kill"),
    ):
        bolt.process(make_tup(1))
        time.sleep(0.25)
        assert bolt._batcher.is_alive(), "batcher thread died on first exception"
        assert bolt.exc_info is not None


def test_exception_in_batcher_signals_the_main_thread():
    bolt = build(ExplodingBolt)
    with patch("os.kill") as mock_kill:
        bolt.process(make_tup(1))
        time.sleep(0.2)
    assert mock_kill.called
    (pid, signum), _ = mock_kill.call_args
    assert pid == bolt.pid
    assert signum == signal.SIGUSR1


def test_handle_worker_exception_reraises_on_main_thread():
    bolt = build(ExplodingBolt)
    bolt.exc_info = (ValueError, ValueError("from batcher"), None)
    with pytest.raises(ValueError, match="from batcher"):
        bolt._handle_worker_exception(signal.SIGUSR1, None)


def test_handle_worker_exception_ignores_a_stray_signal():
    # signal.signal() is process-global, so the last bolt constructed owns the
    # handler. A SIGUSR1 meant for a sibling must not blow up on `exc_info`.
    bolt = build(CollectingBolt)
    assert bolt.exc_info is None
    assert bolt._handle_worker_exception(signal.SIGUSR1, None) is None


def test_process_tick_just_acks():
    bolt = build(CollectingBolt)
    tick = Tuple(id="t", component="__system", stream="__tick", task=-1, values=[1])
    with patch.object(bolt, "ack") as mock_ack:
        bolt.process_tick(tick)
    mock_ack.assert_called_once_with(tick)
    assert bolt.processed == []


def test_group_key_splits_batches():
    class Grouped(CollectingBolt):
        def group_key(self, tup):
            return tup.values[0] % 2

    bolt = build(Grouped)
    with patch.object(bolt, "ack"):
        for i in range(4):
            bolt.process(make_tup(i))
        bolt.process_batches()
    assert sorted(k for k, _ in bolt.processed) == [0, 1]


def test_both_batching_classes_exist_with_upstream_hierarchy():
    """Pin the two-class shape, since an earlier revision flattened it.

    `TicklessBatchingBolt` must stay a *subclass* of `BatchingBolt`: that is
    what keeps the MRO of the ten casterisk bolts identical to pre-migration,
    and it is the property a well-meaning cleanup is most likely to undo.
    """
    from pystorm_a8c.bolt import BatchingBolt, Bolt, TicklessBatchingBolt

    assert issubclass(TicklessBatchingBolt, BatchingBolt)
    assert issubclass(BatchingBolt, Bolt)
    assert TicklessBatchingBolt.__mro__[:3] == (
        TicklessBatchingBolt,
        BatchingBolt,
        Bolt,
    )


def test_the_two_batching_classes_flush_on_different_triggers():
    """The reason both are kept: tick-driven vs timer-driven flushing."""
    from pystorm_a8c.bolt import BatchingBolt, TicklessBatchingBolt

    # BatchingBolt counts tick tuples; TicklessBatchingBolt sleeps.
    assert BatchingBolt.ticks_between_batches == 1
    assert TicklessBatchingBolt.secs_between_batches == 2
    # The tickless override is what makes ticks inert on the subclass.
    assert TicklessBatchingBolt.process_tick is not BatchingBolt.process_tick


def test_emit_never_waits_for_task_ids():
    bolt = build(CollectingBolt)
    with (
        patch.object(bolt, "send_message"),
        patch.object(bolt, "read_task_ids") as mock_read,
    ):
        bolt.emit([1, 2])
    mock_read.assert_not_called()
