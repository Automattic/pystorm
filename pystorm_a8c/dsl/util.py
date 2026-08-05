"""
Utility functions for the DSL
"""

from pystorm_a8c.storm import JavaObjectArg


def to_java_arg(arg):
    """Converts Python objects to equivalent Thrift JavaObjectArgs"""
    if isinstance(arg, bool):
        java_arg = JavaObjectArg(bool_arg=arg)
    elif isinstance(arg, int):
        # Just use long all the time since Python 3 doesn't
        # distinguish between long and int
        java_arg = JavaObjectArg(long_arg=arg)
    elif isinstance(arg, bytes):
        java_arg = JavaObjectArg(binary_arg=arg)
    elif isinstance(arg, str):
        java_arg = JavaObjectArg(string_arg=arg)
    elif isinstance(arg, float):
        java_arg = JavaObjectArg(double_arg=arg)
    else:
        raise TypeError(
            "Only basic data types can be specified"
            " as arguments to JavaObject "
            "constructors.  Given: {!r}".format(arg)
        )
    return java_arg
