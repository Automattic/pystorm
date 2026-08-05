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

# A fixed timestamp makes rebuilds of an unchanged tree byte-identical, so the
# blobstore digest stays stable. ZIP cannot store years before 1980.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _dir_key(path):
    """``(st_dev, st_ino)`` identifying a directory.

    An OSError propagates: a source tree we cannot stat is a broken build.
    """
    stat = os.stat(path)
    return (stat.st_dev, stat.st_ino)


def _contains_itself(root, key, keys_by_path):
    """Is ``root`` a link back to a directory it is already nested inside?

    os.walk is top-down, so every ancestor has been recorded by now.
    """
    parent = os.path.dirname(root)
    while parent in keys_by_path:
        if keys_by_path[parent] == key:
            return True
        grandparent = os.path.dirname(parent)
        if grandparent == parent:
            break
        parent = grandparent
    return False


def _collect(src_dir):
    """Yield (absolute_path, posix_arcname) pairs, following symlinks.

    ``followlinks=True`` is required: a project's ``src/<pkg>`` is often a
    symlink, and os.walk skips symlinked directories by default, which yields
    an empty JAR.

    That leaves os.walk with no loop protection, so each directory is compared
    against its own ancestors and pruned if it contains itself. Ancestors only
    -- two symlinks aimed at one real directory are not a loop, and each
    belongs in the JAR under its own name.
    """
    src_dir = pathlib.Path(src_dir)
    if not src_dir.is_dir():
        raise FileNotFoundError(f"source directory does not exist: {src_dir}")
    keys_by_path = {}
    for root, dirs, files in os.walk(src_dir, followlinks=True):
        key = _dir_key(root)
        keys_by_path[root] = key
        if _contains_itself(root, key, keys_by_path):
            dirs[:] = []
            continue
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
            f"no files to package under {src_dir!r}. An empty JAR submits "
            "successfully and then fails at worker start, so this is an error."
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
