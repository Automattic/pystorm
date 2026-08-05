"""
Base Spout classes.
"""

from collections import Counter

from pystorm_a8c.component import Component


class Spout(Component):
    """Base class for all pystorm spouts.

    For more information on spouts, consult Storm's
    `Concepts documentation <http://storm.apache.org/documentation/Concepts.html>`_.
    """

    def ack(self, tup_id):
        """Called when a bolt acknowledges a Tuple in the topology.

        :param tup_id: the ID of the Tuple that has been fully acknowledged in
                       the topology.
        :type tup_id: str
        """
        pass

    def fail(self, tup_id):
        """Called when a Tuple fails in the topology

        A spout can choose to emit the Tuple again or ignore the fail. The
        default is to ignore.

        :param tup_id: the ID of the Tuple that has failed in the topology
                       either due to a bolt calling ``fail()`` or a Tuple
                       timing out.
        :type tup_id: str
        """
        pass

    def next_tuple(self):
        """Implement this function to emit Tuples as necessary.

        This function should not block, or Storm will think the
        spout is dead. Instead, let it return and pystorm will
        send a noop to storm, which lets it know the spout is functioning.
        """
        raise NotImplementedError()

    def emit(
        self, tup, tup_id=None, stream=None, direct_task=None, need_task_ids=False
    ):
        """Emit a spout Tuple message.

        :param tup: the Tuple to send to Storm, should contain only
                    JSON-serializable data.
        :type tup: list or tuple
        :param tup_id: the ID for the Tuple. Leave this blank for an
                       unreliable emit.
        :type tup_id: str
        :param stream: ID of the stream this Tuple should be emitted to.
                       Leave empty to emit to the default stream.
        :type stream: str
        :param direct_task: the task to send the Tuple to if performing a
                            direct emit.
        :type direct_task: int
        :param need_task_ids: indicate whether or not you'd like the task IDs
                              the Tuple was emitted (default: ``False``).
        :type need_task_ids: bool

        :returns: ``None``, unless ``need_task_ids=True``, in which case it will
                  be a ``list`` of task IDs that the Tuple was sent to if. Note
                  that when specifying direct_task, this will be equal to
                  ``[direct_task]``.
        """
        return super().emit(
            tup,
            tup_id=tup_id,
            stream=stream,
            direct_task=direct_task,
            need_task_ids=need_task_ids,
        )

    @classmethod
    def spec(cls, name=None, par=None, config=None, outputs=None):
        """Create a topology DSL spec for this spout.

        Merged in from streamparse's Spout layer so the two-level component
        hierarchy collapses into one class. Unlike :meth:`Bolt.spec` there is
        no ``inputs`` parameter -- spouts are sources.
        """
        from pystorm_a8c.dsl.spout import ShellSpoutSpec

        return ShellSpoutSpec(
            cls,
            command="pystorm_a8c_run",
            script=f"-m {cls.__module__}",
            name=name,
            par=par,
            config=config,
            outputs=outputs,
        )

    def activate(self):
        """Called when the Spout has been activated after being deactivated.

        .. note::
            This requires at least Storm 1.1.0.
        """
        pass

    def deactivate(self):
        """Called when the Spout has been deactivated.

        .. note::
            This requires at least Storm 1.1.0.
        """
        pass

    def _run(self):
        """The inside of ``run``'s infinite loop.

        Separated out so it can be properly unit tested.
        """
        cmd = self.read_command()
        if cmd["command"] == "next":
            self.next_tuple()
        elif cmd["command"] == "ack":
            self.ack(cmd["id"])
        elif cmd["command"] == "fail":
            self.fail(cmd["id"])
        elif cmd["command"] == "activate":
            self.activate()
        elif cmd["command"] == "deactivate":
            self.deactivate()
        else:
            self.logger.error("Received invalid command from Storm: %r", cmd)
        self.send_message({"command": "sync"})


class ReliableSpout(Spout):
    """Reliable spout that will automatically replay failed tuples.

    Failed tuples will be replayed up to ``max_fails`` times.

    For more information on spouts, consult Storm's
    `Concepts documentation <http://storm.apache.org/documentation/Concepts.html>`_.

    :ivar unacked_tuples: ``dict`` mapping tuple ID to the emit arguments needed
        to replay it, populated by :meth:`emit` and drained by :meth:`ack`.

        **This mapping is unbounded by design.** It holds exactly the tuples
        Storm has not yet resolved, so its size is governed by the topology's
        in-flight window and ``topology.message.timeout.secs`` -- not by
        anything this class should cap. Evicting entries to bound it would
        silently break replay: :meth:`fail` would find no saved args and log
        "Received fail for unknown tuple ID" instead of re-emitting, turning a
        recoverable failure into permanent data loss.

        If it grows without bound in production, the cause is upstream (acks
        not arriving, or a spout emitting faster than the topology drains), and
        that is what needs fixing. ``casterisk/spouts/common/base.py`` gauges
        ``len(self.unacked_tuples)`` to Graphite precisely so that condition is
        visible; both this attribute name and ``max_fails`` are part of that
        external contract and must not be renamed.

    :ivar failed_tuples: ``Counter`` of per-tuple-ID failure counts, compared
        against ``max_fails`` to decide between replaying and giving up.
    """

    max_fails = 3

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.failed_tuples = Counter()
        self.unacked_tuples = {}

    def ack(self, tup_id):
        """Called when a bolt acknowledges a Tuple in the topology.

        :param tup_id: the ID of the Tuple that has been fully acknowledged in
                       the topology.
        :type tup_id: str
        """
        self.failed_tuples.pop(tup_id, None)
        try:
            del self.unacked_tuples[tup_id]
        except KeyError:
            self.logger.error("Received ack for unknown tuple ID: %r", tup_id)

    def fail(self, tup_id):
        """Called when a Tuple fails in the topology

        A reliable spout will replay a failed tuple up to ``max_fails`` times.

        :param tup_id: the ID of the Tuple that has failed in the topology
                       either due to a bolt calling ``fail()`` or a Tuple
                       timing out.
        :type tup_id: str
        """
        saved_args = self.unacked_tuples.get(tup_id)
        if saved_args is None:
            self.logger.error("Received fail for unknown tuple ID: %r", tup_id)
            return
        tup, stream, direct_task, need_task_ids = saved_args
        if self.failed_tuples[tup_id] < self.max_fails:
            self.emit(
                tup,
                tup_id=tup_id,
                stream=stream,
                direct_task=direct_task,
                need_task_ids=need_task_ids,
            )
            self.failed_tuples[tup_id] += 1
        else:
            # Just pretend we got an ack when we exceed retry limit
            self.logger.info(
                "Acking tuple ID %r after it exceeded retry limit " "(%r)",
                tup_id,
                self.max_fails,
            )
            self.ack(tup_id)

    def emit(
        self, tup, tup_id=None, stream=None, direct_task=None, need_task_ids=False
    ):
        """Emit a spout Tuple & add metadata about it to `unacked_tuples`.

        In order for this to work, `tup_id` is a required parameter.

        See :meth:`Bolt.emit`.
        """
        if tup_id is None:
            raise ValueError(
                "You must provide a tuple ID when emitting with a "
                "ReliableSpout in order for the tuple to be "
                "tracked."
            )
        args = (tup, stream, direct_task, need_task_ids)
        self.unacked_tuples[tup_id] = args
        return super().emit(
            tup,
            tup_id=tup_id,
            stream=stream,
            direct_task=direct_task,
            need_task_ids=need_task_ids,
        )
