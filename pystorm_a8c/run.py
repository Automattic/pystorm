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
    # Storm unpacks the topology JAR's resources/ into the worker directory
    # and starts us with the cwd already set to it, so the component packages
    # are siblings of the cwd rather than children of it. Python does not put
    # the cwd on sys.path -- only the script's own directory, which here is the
    # venv's bin/ -- so without this the import fails on every component.
    #
    # The resources/ child is appended too because not every Storm layout puts
    # us inside it; appending a directory that does not exist would be harmless
    # but is skipped so the path stays honest.
    sys.path.append(os.getcwd())
    resources_path = os.path.join(os.getcwd(), RESOURCES_PATH)
    if os.path.isdir(resources_path):
        sys.path.append(resources_path)
    # Import module
    mod = importlib.import_module(mod_name)
    # Get class from module and run it
    cls = getattr(mod, cls_name)
    cls().run()


if __name__ == "__main__":
    main()
