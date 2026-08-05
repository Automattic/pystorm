"""Build a Storm topology JAR.

A topology JAR for a pure-Python topology carries no Java: its entire
payload is the ``resources/`` tree that Storm unpacks into the worker
directory and that ``pystorm_a8c.run`` adds to ``sys.path``. A JAR is a
ZIP, so the stdlib is the whole toolchain -- no lein, no JDK.
"""

import os
import pathlib
import zipfile

RESOURCES_ROOT = "resources"
EXCLUDED_DIRS = {"__pycache__", ".git", ".pytest_cache", ".mypy_cache"}
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".pyd")
EXCLUDED_NAMES = {".DS_Store"}

# A fixed timestamp keeps rebuilds byte-identical, which keeps the blobstore
# digest in bin/topo-submit stable across rebuilds of unchanged code.
# ZIP cannot store years before 1980.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _collect(src_dir):
    """Yield (absolute_path, posix_arcname) pairs, following symlinks.

    ``followlinks=True`` is required, not stylistic: casterisk-realtime's
    ``src/casterisk`` is a symlink to ``../casterisk``, and os.walk skips
    symlinked directories by default -- which yields an empty JAR.
    """
    src_dir = pathlib.Path(src_dir)
    if not src_dir.is_dir():
        raise FileNotFoundError(f"source directory does not exist: {src_dir}")
    for root, dirs, files in os.walk(src_dir, followlinks=True):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
        rel_root = pathlib.PurePath(root).relative_to(src_dir)
        for name in sorted(files):
            if name in EXCLUDED_NAMES or name.endswith(EXCLUDED_SUFFIXES):
                continue
            arcname = pathlib.PurePosixPath(RESOURCES_ROOT, *rel_root.parts, name)
            yield os.path.join(root, name), str(arcname)


def build_jar(src_dir="src", output_path=None):
    """Zip ``src_dir`` into a topology JAR and return the output path."""
    if output_path is None:
        output_path = os.path.join(".artifacts", "topology.jar")
    output_path = pathlib.Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    entries = list(_collect(src_dir))
    if not entries:
        raise RuntimeError(
            f"no files to package under {src_dir!r}. If it contains a symlink, "
            "note that only directories reachable with followlinks=True are "
            "walked -- an empty JAR submits successfully and then fails at "
            "worker start, so this is a hard error."
        )

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for abs_path, arcname in entries:
            info = zipfile.ZipInfo(arcname, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(abs_path, "rb") as fh:
                zf.writestr(info, fh.read())

    print(f"Topology JAR created: {output_path} ({len(entries)} files)")
    return str(output_path)


def subparser_hook(subparsers):
    """Hook to add subparser for this command."""
    p = subparsers.add_parser(
        "jar", description="Build a topology JAR from a source directory"
    )
    p.add_argument(
        "--src", default="src", help="Directory packaged as resources/ (default: src)"
    )
    p.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output path (default: .artifacts/topology.jar)",
    )
    p.set_defaults(func=main)


def main(args):
    """Build a topology JAR."""
    build_jar(src_dir=args.src, output_path=args.output)
