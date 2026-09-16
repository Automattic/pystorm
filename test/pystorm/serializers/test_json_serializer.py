from __future__ import absolute_import, print_function, unicode_literals

from io import StringIO

try:
    from unittest import mock
except ImportError:
    import mock

import simplejson as json
import pytest

from pystorm.exceptions import StormWentAwayError
from pystorm.serializers.json_serializer import JSONSerializer

from .serializer import SerializerTestCase


class TestJSONSerializer(SerializerTestCase):

    INSTANCE_CLS = JSONSerializer

    def test_read_message_dict(self):
        msg_dict = {"hello": "world"}
        self.instance.input_stream = StringIO(self.instance.serialize_dict(msg_dict))
        assert self.instance.read_message() == msg_dict

    def test_read_message_list(self):
        msg_list = [3, 4, 5]
        self.instance.input_stream = StringIO(self.instance.serialize_dict(msg_list))
        assert self.instance.read_message() == msg_list

    def test_send_message(self):
        msg_dict = {"hello": "world"}
        expected_output = """{"hello": "world"}\nend\n"""
        self.instance.output_stream = StringIO()
        self.instance.send_message(msg_dict)
        assert self.instance.output_stream.getvalue() == expected_output

    @pytest.mark.parametrize("value", [2 ** 63, -(2 ** 63) - 1, 2 ** 64 - 1])
    def test_serialize_replaces_integers_storm_cannot_read(self, value):
        """
        When: a message holds an integer wider than a Java Long
        Then: `serialize_dict` writes null in its place
        """
        serialized = self.instance.serialize_dict({"value": value})
        assert json.loads(serialized.split("\n")[0]) == {"value": None}

    @pytest.mark.parametrize("value", [2 ** 63 - 1, -(2 ** 63), 0, 1789559340000])
    def test_serialize_keeps_integers_storm_can_read(self, value):
        """
        When: a message holds an integer that fits in a Java Long
        Then: `serialize_dict` leaves it alone
        """
        serialized = self.instance.serialize_dict({"value": value})
        assert json.loads(serialized.split("\n")[0]) == {"value": value}

    def test_serialize_reaches_integers_inside_the_emitted_tuple(self):
        """
        When: the out-of-range integer sits nested in a batch
        Then: `serialize_dict` replaces it and keeps the rest of the batch
        """
        msg = {
            "command": "emit",
            "tuple": [
                "a-stream",
                [{"id": "first", "value": 2 ** 64}, {"id": "second", "value": 7}],
                None,
            ],
        }
        serialized = self.instance.serialize_dict(msg)
        events = json.loads(serialized.split("\n")[0])["tuple"][1]
        assert events[0] == {"id": "first", "value": None}
        assert events[1] == {"id": "second", "value": 7}

    def test_serialize_keeps_long_digit_runs_inside_strings(self):
        """
        When: a string value holds more digits than any integer
        Then: `serialize_dict` leaves the string alone
        """
        identifier = "08988130376555764913840025431799009902"
        serialized = self.instance.serialize_dict({"id": identifier})
        assert json.loads(serialized.split("\n")[0]) == {"id": identifier}

    def test_serialize_keeps_booleans_as_booleans(self):
        """
        When: a message holds booleans, which subclass int
        Then: `serialize_dict` leaves them alone
        """
        serialized = self.instance.serialize_dict({"yes": True, "no": False})
        assert json.loads(serialized.split("\n")[0]) == {"yes": True, "no": False}

    def test_serialize_replaces_a_bare_integer_message(self):
        """
        When: the whole message is an out-of-range integer
        Then: `serialize_dict` writes null
        """
        serialized = self.instance.serialize_dict(2 ** 64)
        assert json.loads(serialized.split("\n")[0]) is None

    def test_send_message_raises_stormwentaway(self):
        string_io_mock = mock.MagicMock(autospec=True)

        def raiser():  # lambdas can't raise
            raise IOError()

        string_io_mock.flush.side_effect = raiser
        self.instance.output_stream = string_io_mock
        with pytest.raises(StormWentAwayError):
            self.instance.send_message({"hello": "world"})

    @mock.patch("pystorm.serializers.serializer.log.exception", autospec=True)
    def test_send_message_bad_value(self, log_mock):
        msg_dict = {"hello": b"\xfc\x89"}
        self.instance.output_stream = StringIO()
        self.instance.send_message(msg_dict)
        log_mock.assert_called_with("Failed to send message: %r", msg_dict)
