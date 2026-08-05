"""
Command-line interface for pystorm-a8c.
"""

import argparse
import sys

from pystorm_a8c import __version__
from pystorm_a8c.cli import jar, submit


def build_parser():
    """Build the argparse root.

    This replaces ``sparse``'s ``pkgutil.iter_modules`` auto-discovery with an
    explicit two-line registry. With two subcommands, auto-discovery was hiding
    rather than helping -- and it is what let nine unused subcommands persist
    unnoticed.
    """
    parser = argparse.ArgumentParser(
        prog="pystorm-a8c",
        description="Build and submit Apache Storm topologies.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")
    submit.subparser_hook(subparsers)
    jar.subparser_hook(subparsers)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)
