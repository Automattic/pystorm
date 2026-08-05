import io
import threading

import pytest

from pystorm_a8c.exceptions import StormWentAwayError
from pystorm_a8c.serializer import JSONSerializer


def make_serializer(input_bytes=b""):
    # Raw BytesIO, not a TextIOWrapper: _wrap_stream re-wraps `.buffer` when it
    # sees one, which orphans the outer wrapper. Once that temporary is garbage
    # collected it closes the underlying buffer out from under the serializer.
    # sys.stdin/stdout stay referenced by the sys module, so this only bites here.
    inp = io.BytesIO(input_bytes)
    out = io.BytesIO()
    return JSONSerializer(inp, out, threading.RLock(), threading.RLock())


def test_wraps_stdout_is_recorded_before_the_stream_is_wrapped(monkeypatch):
    """_wrap_stream returns a NEW TextIOWrapper, so `output_stream is
    sys.stdout` is never true afterwards. Component relies on this flag to
    decide whether to redirect sys.stdout away from Storm's pipe.

    sys.stdout is patched rather than used directly: wrapping the real one
    orphans a TextIOWrapper that closes pytest's captured stdout when it is
    collected (see make_serializer above).
    """
    import sys

    fake_stdout = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", fake_stdout)

    s = JSONSerializer(io.BytesIO(), fake_stdout, threading.RLock(), threading.RLock())

    assert s.wraps_stdout is True
    assert s.output_stream is not sys.stdout
    assert make_serializer().wraps_stdout is False


def test_read_message_parses_json_terminated_by_end():
    s = make_serializer(b'{"command": "next"}\nend\n')
    assert s.read_message() == {"command": "next"}


def test_read_message_handles_multiline_payload():
    s = make_serializer(b'{"command":\n"next"}\nend\n')
    assert s.read_message() == {"command": "next"}


def test_read_message_raises_when_storm_closes_stdin():
    s = make_serializer(b"")
    with pytest.raises(StormWentAwayError):
        s.read_message()


def test_read_message_parses_task_id_list():
    s = make_serializer(b"[1, 2, 3]\nend\n")
    assert s.read_message() == [1, 2, 3]


def test_serialize_dict_appends_end_terminator():
    s = make_serializer()
    assert s.serialize_dict({"command": "sync"}) == '{"command": "sync"}\nend\n'


def test_serialize_dict_emits_namedtuple_as_array():
    # Storm expects tuple values as a JSON array, never an object.
    from collections import namedtuple

    Point = namedtuple("Point", "x y")
    assert (
        s_out(make_serializer(), {"tuple": Point(1, 2)}) == '{"tuple": [1, 2]}\nend\n'
    )


def s_out(serializer, msg):
    return serializer.serialize_dict(msg)


def test_send_message_writes_and_flushes():
    s = make_serializer()
    s.send_message({"command": "sync"})
    s.output_stream.flush()
    assert s.output_stream.buffer.getvalue() == b'{"command": "sync"}\nend\n'


def test_send_message_propagates_unexpected_errors():
    # The old bare `except:` silently dropped messages. It must not come back.
    s = make_serializer()

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        s.send_message({"bad": Unserializable()})


def test_blank_lines_are_skipped_and_counted():
    """Storm emits stray blank lines; a pathological stream should be visible
    without flooding the log."""
    s = make_serializer(b"\n" * 3 + b'{"command": "next"}\nend\n')

    assert s.read_message() == {"command": "next"}
    assert s._blank_lines_read == 3


def test_a_closed_pipe_becomes_storm_went_away():
    """IOError on write means the parent Storm process is gone."""
    s = make_serializer()

    class ClosedPipe:
        def flush(self):
            raise IOError("broken pipe")

        def write(self, _):
            raise IOError("broken pipe")

    s.output_stream = ClosedPipe()
    with pytest.raises(StormWentAwayError):
        s.send_message({"command": "sync"})


def test_an_unwrappable_stream_is_refused():
    """Returning it unwrapped would mis-encode every tuple on the wire."""
    import threading

    class NotAStream:
        pass

    with pytest.raises(TypeError, match="Cannot wrap"):
        JSONSerializer(NotAStream(), NotAStream(), threading.RLock(), threading.RLock())
