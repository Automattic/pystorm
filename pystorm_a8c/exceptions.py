class StormWentAwayError(Exception):
    """Raised when the connection to Storm's stdin/stdout is closed."""

    def __init__(self):
        message = "Got EOF while reading from Storm"
        super().__init__(message)
