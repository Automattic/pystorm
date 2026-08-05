import io
import json
import logging
import sys

from pystorm_a8c.exceptions import StormWentAwayError

log = logging.getLogger(__name__)


class JSONSerializer:
    """Storm multi-lang JSON line protocol.

    Messages are newline-delimited JSON terminated by a line containing
    exactly ``end``.
    """

    def __init__(self, input_stream, output_stream, reader_lock, writer_lock):
        #: Whether the stream we were handed is the process's ``sys.stdout``,
        #: i.e. whether a stray ``print()`` would land in Storm's pipe.
        #: Recorded here because ``_wrap_stream`` returns a *new* TextIOWrapper:
        #: comparing ``self.output_stream`` against ``sys.stdout`` later is
        #: always False, which silently disabled the component's redirect.
        self.wraps_stdout = output_stream is sys.stdout
        self.input_stream = self._wrap_stream(input_stream)
        self.output_stream = self._wrap_stream(output_stream)
        self._reader_lock = reader_lock
        self._writer_lock = writer_lock
        self._blank_lines_read = 0

    @staticmethod
    def _wrap_stream(stream):
        """Reopen a stream in UTF-8 text mode."""
        if hasattr(stream, "buffer"):
            return io.TextIOWrapper(stream.buffer, encoding="utf-8")
        if hasattr(stream, "readable"):
            return io.TextIOWrapper(stream, encoding="utf-8")
        raise TypeError(
            f"Cannot wrap {stream!r} as UTF-8: it has neither .buffer nor "
            f".readable. Returning it unwrapped would mis-encode every tuple "
            f"on the multi-lang wire."
        )

    def read_message(self):
        """Read one complete multi-lang message.

        :returns: a ``dict`` (a command) or a ``list`` (task IDs)
        :raises StormWentAwayError: if Storm closed the stream
        """
        message = []
        with self._reader_lock:
            while True:
                line = self.input_stream.readline()
                if not line:
                    raise StormWentAwayError()
                line = line.rstrip("\n")
                if line == "end":
                    break
                if line == "":
                    # Storm occasionally emits stray blank lines; count them so
                    # a pathological stream is visible without flooding logs.
                    self._blank_lines_read += 1
                    if self._blank_lines_read % 1000 == 0:
                        log.warning(
                            "Read %d blank lines from Storm",
                            self._blank_lines_read,
                        )
                    continue
                message.append(line)
        return json.loads("\n".join(message))

    def serialize_dict(self, msg_dict):
        """Serialize a message to the multi-lang wire format."""
        return f"{json.dumps(msg_dict)}\nend\n"

    def send_message(self, msg_dict):
        """Write a message to Storm.

        :raises StormWentAwayError: if the pipe is closed
        """
        serialized = self.serialize_dict(msg_dict)
        with self._writer_lock:
            try:
                self.output_stream.flush()
                self.output_stream.write(serialized)
                self.output_stream.flush()
            except IOError as e:
                raise StormWentAwayError() from e
