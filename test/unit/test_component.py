"""
Tests for basic IPC stuff via Component class
"""

import io
import json
import logging
import os
import unittest
from io import BytesIO
from unittest.mock import patch

import pytest

from pystorm_a8c.component import Component, LogStream
from pystorm_a8c.exceptions import StormWentAwayError

log = logging.getLogger(__name__)


class ComponentTests(unittest.TestCase):
    conf = {
        "topology.message.timeout.secs": 3,
        "topology.tick.tuple.freq.secs": 1,
        "topology.debug": True,
        "topology.name": "foo",
    }
    context = {
        "task->component": {
            "1": "example-spout",
            "2": "__acker",
            "3": "example-bolt1",
            "4": "example-bolt2",
        },
        "taskid": 3,
        # Everything below this line is only available in Storm 0.11.0+
        "componentid": "example-bolt1",
        "stream->target->grouping": {"default": {"example-bolt2": {"type": "SHUFFLE"}}},
        "streams": ["default"],
        "stream->outputfields": {"default": ["word"]},
        "source->stream->grouping": {
            "example-spout": {"default": {"type": "FIELDS", "fields": ["word"]}}
        },
        "source->stream->fields": {
            "example-spout": {"default": ["sentence", "word", "number"]}
        },
    }

    def test_read_handshake(self):
        handshake_dict = {"conf": self.conf, "pidDir": ".", "context": self.context}
        pid_dir = handshake_dict["pidDir"]
        expected_conf = handshake_dict["conf"]
        expected_context = handshake_dict["context"]
        inputs = ["{}\n".format(json.dumps(handshake_dict)), "end\n"]
        component = Component(
            input_stream=BytesIO("".join(inputs).encode("utf-8")),
            output_stream=BytesIO(),
        )
        given_conf, given_context = component.read_handshake()
        pid_path = os.path.join(pid_dir, str(component.pid))
        self.assertTrue(os.path.exists(pid_path))
        os.remove(pid_path)
        self.assertEqual(given_conf, expected_conf)
        self.assertEqual(given_context, expected_context)
        self.assertEqual(
            component.serializer.serialize_dict({"pid": component.pid}).encode("utf-8"),
            component.serializer.output_stream.buffer.getvalue(),
        )

    def test_setup_component(self):
        conf = self.conf
        component = Component(input_stream=BytesIO(), output_stream=BytesIO())
        component._setup_component(conf, self.context)
        self.assertEqual(component.topology_name, conf["topology.name"])
        self.assertEqual(component.task_id, self.context["taskid"])
        self.assertEqual(
            component.component_name,
            self.context["task->component"][str(self.context["taskid"])],
        )
        self.assertEqual(component.storm_conf, conf)
        self.assertEqual(component.context, self.context)

    def test_read_message(self):
        inputs = [  # Task IDs
            "[12, 22, 24]\n",
            "end\n",
            # Incoming Tuple for bolt
            (
                '{ "id": "-6955786537413359385", "comp": "1", "stream": "1"'
                ', "task": 9, "tuple": ["snow white and the seven dwarfs", '
                '"field2", 3]}\n'
            ),
            "end\n",
            # next command for spout
            '{"command": "next"}\n',
            "end\n",
            # empty message, which should trigger sys.exit (end ignored)
            "",
            "",
        ]
        outputs = [json.loads(msg) for msg in inputs[::2] if msg]
        outputs.append("")
        component = Component(
            input_stream=BytesIO("".join(inputs).encode("utf-8")),
            output_stream=BytesIO(),
        )
        for output in outputs:
            log.info("Checking msg for %r", output)
            if output:
                msg = component.read_message()
                self.assertEqual(output, msg)
            else:
                with self.assertRaises(StormWentAwayError):
                    component.read_message()

    def test_read_message_unicode(self):
        inputs = [  # Task IDs
            "[12, 22, 24]\n",
            "end\n",
            # Incoming Tuple for bolt
            (
                '{ "id": "-6955786537413359385", "comp": "1", "stream": "1"'
                ', "task": 9, "tuple": ["snow white \uffe6 the seven dwarfs"'
                ', "field2", 3]}\n'
            ),
            "end\n",
            # next command for spout
            '{"command": "next"}\n',
            "end\n",
            # empty message, which should trigger sys.exit (end ignored)
            "",
            "",
        ]
        outputs = [json.loads(msg) for msg in inputs[::2] if msg]
        outputs.append("")
        component = Component(
            input_stream=BytesIO("".join(inputs).encode("utf8")),
            output_stream=BytesIO(),
        )
        for output in outputs:
            log.info("Checking msg for %r", output)
            if output:
                msg = component.read_message()
                self.assertEqual(output, msg)
            else:
                with self.assertRaises(StormWentAwayError):
                    component.read_message()

    def test_read_split_message(self):
        # Make sure we can read something that's broken up into many "lines"
        inputs = [
            '{ "id": "-6955786537413359385", ',
            '"comp": "1", "stream": "1"\n',
            "\n",
            ', "task": 9, "tuple": ["snow white and the seven dwarfs", ',
            '"field2", 3]}\n',
            "end\n",
        ]
        output = json.loads("".join(inputs[:-1]))

        component = Component(
            input_stream=BytesIO("".join(inputs).encode("utf-8")),
            output_stream=BytesIO(),
        )
        msg = component.read_message()
        self.assertEqual(output, msg)

    def test_read_command(self):
        # Check that we properly queue task IDs and return only commands
        inputs = [  # Task IDs
            "[12, 22, 24]\n",
            "end\n",
            # Incoming Tuple for bolt
            (
                '{ "id": "-6955786537413359385", "comp": "1", "stream": "1"'
                ', "task": 9, "tuple": ["snow white and the seven dwarfs", '
                '"field2", 3]}\n'
            ),
            "end\n",
            # next command for spout
            '{"command": "next"}\n',
            "end\n",
        ]
        outputs = [json.loads(msg) for msg in inputs[::2]]
        component = Component(
            input_stream=BytesIO("".join(inputs).encode("utf-8")),
            output_stream=BytesIO(),
        )

        # Skip first output, because it's a task ID, and won't be returned by
        # read_command
        for output in outputs[1:]:
            log.info("Checking msg for %r", output)
            msg = component.read_command()
            self.assertEqual(output, msg)
        self.assertEqual(component._pending_task_ids.pop(), outputs[0])

    def test_read_task_ids(self):
        # Check that we properly queue commands and return only task IDs
        inputs = [  # Task IDs
            "[4, 8, 15]\n",
            "end\n",
            # Incoming Tuple for bolt
            (
                '{ "id": "-6955786537413359385", "comp": "1", "stream": "1"'
                ', "task": 9, "tuple": ["snow white and the seven dwarfs", '
                '"field2", 3]}\n'
            ),
            "end\n",
            # next command for spout
            '{"command": "next"}\n',
            "end\n",
            # Task IDs
            "[16, 23, 42]\n",
            "end\n",
        ]
        outputs = [json.loads(msg) for msg in inputs[::2]]
        component = Component(
            input_stream=BytesIO("".join(inputs).encode("utf-8")),
            output_stream=BytesIO(),
        )

        # Skip middle outputs, because they're commands and won't be returned by
        # read_task_ids
        for output in (outputs[0], outputs[-1]):
            log.info("Checking msg for %r", output)
            msg = component.read_task_ids()
            self.assertEqual(output, msg)
        for output in outputs[1:-1]:
            self.assertEqual(component._pending_commands.popleft(), output)

    def test_send_message(self):
        component = Component(input_stream=BytesIO(), output_stream=BytesIO())
        inputs = [
            {
                "command": "emit",
                "id": 4,
                "stream": "",
                "task": 9,
                "tuple": ["field1", 2, 3],
            },
            {"command": "log", "msg": "I am a robot monkey."},
            {"command": "next"},
            {"command": "sync"},
        ]
        for cmd in inputs:
            component.serializer.output_stream.close()
            component.serializer.output_stream = component.serializer._wrap_stream(
                BytesIO()
            )
            component.send_message(cmd)
            self.assertEqual(
                component.serializer.serialize_dict(cmd).encode("utf-8"),
                component.serializer.output_stream.buffer.getvalue(),
            )

        # A non-dict message is now a hard error. Silently dropping it is how
        # protocol desyncs become invisible.
        with self.assertRaises(TypeError):
            component.send_message(["foo", "bar"])

    def test_send_message_unicode(self):
        component = Component(input_stream=BytesIO(), output_stream=BytesIO())
        inputs = [
            {
                "command": "emit",
                "id": 4,
                "stream": "",
                "task": 9,
                "tuple": ["field\uffe6", 2, 3],
            },
            {"command": "log", "msg": "I am a robot monkey."},
            {"command": "next"},
            {"command": "sync"},
        ]
        for cmd in inputs:
            component.serializer.output_stream.close()
            component.serializer.output_stream = component.serializer._wrap_stream(
                BytesIO()
            )
            component.send_message(cmd)
            self.assertEqual(
                component.serializer.serialize_dict(cmd).encode("utf-8"),
                component.serializer.output_stream.buffer.getvalue(),
            )

        # A non-dict message is now a hard error. Silently dropping it is how
        # protocol desyncs become invisible.
        with self.assertRaises(TypeError):
            component.send_message(["foo", "bar"])

    @patch.object(Component, "send_message", autospec=True)
    def test_log(self, send_message_mock):
        component = Component(input_stream=BytesIO(), output_stream=BytesIO())
        inputs = [
            ("I am a robot monkey.", None, 2),
            ("I am a monkey who learned to talk.", "warning", 3),
        ]
        for msg, level, storm_level in inputs:
            component.serializer.output_stream.close()
            component.serializer.output_stream = component.serializer._wrap_stream(
                BytesIO()
            )
            component.log(msg, level=level)
            send_message_mock.assert_called_with(
                component, {"command": "log", "msg": msg, "level": storm_level}
            )

    def test_exit_on_exception_true(self):
        handshake_dict = {"conf": self.conf, "pidDir": ".", "context": self.context}
        inputs = ["{}\n".format(json.dumps(handshake_dict)), "end\n"]
        component = Component(
            input_stream=BytesIO("".join(inputs).encode("utf-8")),
            output_stream=BytesIO(),
        )
        component.exit_on_exception = True
        with self.assertRaises(SystemExit) as raises_fixture:
            component.run()
        assert raises_fixture.exception.code == 1

    @patch.object(Component, "_run", autospec=True)
    def test_exit_on_exception_false(self, _run_mock):
        # Make sure _run raises an exception
        def raiser(self):  # lambdas can't raise
            raise StormWentAwayError if _run_mock.called else NotImplementedError

        _run_mock.side_effect = raiser

        handshake_dict = {"conf": self.conf, "pidDir": ".", "context": self.context}
        inputs = ["{}\n".format(json.dumps(handshake_dict)), "end\n"]
        component = Component(
            input_stream=BytesIO("".join(inputs).encode("utf-8")),
            output_stream=BytesIO(),
        )
        component.exit_on_exception = False
        with self.assertRaises(SystemExit) as raises_fixture:
            component.run()
        assert raises_fixture.exception.code == 2

    @patch.object(Component, "_handle_run_exception", autospec=True)
    @patch("pystorm_a8c.component.log", autospec=True)
    def test_nested_exception(self, log_mock, _handle_run_exception_mock):
        # Make sure self._handle_run_exception raises an exception
        def raiser(self):  # lambdas can't raise
            raise Exception("Oops")

        handshake_dict = {"conf": self.conf, "pidDir": ".", "context": self.context}
        inputs = ["{}\n".format(json.dumps(handshake_dict)), "end\n"]
        component = Component(
            input_stream=BytesIO("".join(inputs).encode("utf-8")),
            output_stream=BytesIO(),
        )
        component.exit_on_exception = True
        _handle_run_exception_mock.side_effect = raiser

        with self.assertRaises(SystemExit) as raises_fixture:
            component.run()
        assert log_mock.error.call_count == 2
        assert raises_fixture.exception.code == 1

    @patch.object(Component, "_handle_run_exception", autospec=True)
    @patch("pystorm_a8c.component.log", autospec=True)
    def test_nested_went_away_exception(self, log_mock, _handle_run_exception_mock):
        # Make sure self._handle_run_exception raises an exception
        def raiser(*args):  # lambdas can't raise
            raise StormWentAwayError

        handshake_dict = {"conf": self.conf, "pidDir": ".", "context": self.context}
        inputs = ["{}\n".format(json.dumps(handshake_dict)), "end\n"]
        component = Component(
            input_stream=BytesIO("".join(inputs).encode("utf-8")),
            output_stream=BytesIO(),
        )
        component.exit_on_exception = True
        _handle_run_exception_mock.side_effect = raiser

        with self.assertRaises(SystemExit) as raises_fixture:
            component.run()
        assert log_mock.error.call_count == 1
        assert log_mock.info.call_count == 1
        assert raises_fixture.exception.code == 2


def test_serializer_kwarg_is_gone():
    # Only JSON is supported; the pluggable-serializer knob is removed.
    import inspect

    from pystorm_a8c.component import Component

    assert "serializer" not in inspect.signature(Component.__init__).parameters


def test_no_sigusr1_handler_registered_by_component():
    # remote_pdb support is removed; TicklessBatchingBolt owns SIGUSR1.
    import signal

    from pystorm_a8c.component import Component

    before = signal.getsignal(signal.SIGUSR1)
    Component(input_stream=BytesIO(), output_stream=BytesIO())
    assert signal.getsignal(signal.SIGUSR1) is before


def test_report_metric_is_gone():
    from pystorm_a8c.component import Component

    assert not hasattr(Component, "report_metric")


def test_setup_component_requires_componentid():
    # The pre-Storm-0.10.0 task->component fallback is dropped.
    from pystorm_a8c.component import Component

    c = Component(input_stream=BytesIO(), output_stream=BytesIO())
    c._setup_component(
        {"topology.name": "topo"},
        {"taskid": 3, "componentid": "my-bolt"},
    )
    assert c.component_name == "my-bolt"
    assert c.task_id == 3
    assert c.topology_name == "topo"


def test_send_message_rejects_non_dict():
    from pystorm_a8c.component import Component

    c = Component(input_stream=BytesIO(), output_stream=BytesIO())
    with pytest.raises(TypeError):
        c.send_message(["not", "a", "dict"])


class _RecordingLogger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(message)


def test_log_stream_answers_the_questions_a_real_stdout_answers():
    """It IS sys.stdout for the life of the worker, not a write/flush stub.

    Libraries that colourize or draw progress bars ask isatty() and encoding
    on import; a bare class answered both with AttributeError.
    """
    stream = LogStream(_RecordingLogger())

    assert stream.isatty() is False
    assert stream.encoding == "utf-8"
    assert stream.writable() is True
    assert stream.readable() is False


def test_log_stream_supplies_the_textiowrapper_only_attributes():
    """io.TextIOBase does not define these; io.TextIOWrapper does.

    Before the redirect went live, sys.stdout was the real stream and all of
    them worked, so leaving them off is a regression rather than a gap.
    """
    stream = LogStream(_RecordingLogger())

    assert stream.line_buffering is True
    assert isinstance(stream.name, str)
    stream.reconfigure(line_buffering=True, write_through=True)
    stream.reconfigure(encoding="utf-8")


def test_log_stream_refuses_an_encoding_it_cannot_honor():
    stream = LogStream(_RecordingLogger())

    with pytest.raises(ValueError, match="UTF-8"):
        stream.reconfigure(encoding="latin-1")


def test_log_stream_withholds_buffer():
    """Byte writes through .buffer would bypass the logger entirely."""
    assert not hasattr(LogStream(_RecordingLogger()), "buffer")


def test_log_stream_refuses_to_hand_out_the_real_descriptor():
    """fileno() would let a subprocess write straight into Storm's pipe."""
    stream = LogStream(_RecordingLogger())

    with pytest.raises(io.UnsupportedOperation):
        stream.fileno()


def test_log_stream_logs_writes_and_drops_blank_lines():
    logger = _RecordingLogger()
    stream = LogStream(logger)

    assert stream.write("hello\n") == len("hello\n")
    assert stream.write("   \n") == len("   \n")

    assert logger.messages == ["hello\n"]


def test_print_through_log_stream_reaches_the_logger():
    """The whole point: print() must not reach the multi-lang pipe.

    print() writes the text and the trailing newline as two calls, and the
    newline-only one is dropped as a blank line -- so the logger sees one
    record with no trailing newline, not two.
    """
    logger = _RecordingLogger()

    print("via print", file=LogStream(logger))

    assert logger.messages == ["via print"]


if __name__ == "__main__":
    unittest.main()
