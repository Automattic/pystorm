"""
Helper script for starting up bolts and spouts.
"""

import argparse
import importlib
import os
import sys

RESOURCES_PATH = "resources"


def main():
    """main entry point for Python bolts and spouts"""
    parser = argparse.ArgumentParser(
        prog="pystorm_a8c_run",
        description="Run a bolt/spout class",
        epilog="This is internal to pystorm-a8c and is used by Storm to run "
        "spout and bolt classes on each worker.",
    )
    parser.add_argument("target_class", help="The bolt/spout class to start.")
    # Storm sends everything as one string, which is not great
    if len(sys.argv) == 2:
        sys.argv = [sys.argv[0]] + sys.argv[1].split()
    args = parser.parse_args()
    mod_name, cls_name = args.target_class.rsplit(".", 1)
    # Storm unpacks the topology JAR into the worker directory, so the
    # component modules live under resources/. Storm <= 1.0.2 put them at the
    # top level instead; that branch is gone, since every cluster this package
    # targets runs 1.2.3. Appending a path that does not exist is harmless.
    sys.path.append(os.path.join(os.getcwd(), RESOURCES_PATH))
    # Import module
    mod = importlib.import_module(mod_name)
    # Get class from module and run it
    cls = getattr(mod, cls_name)
    cls().run()


if __name__ == "__main__":
    main()
