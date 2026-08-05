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
