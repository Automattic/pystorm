import pathlib
import re

import pystorm_a8c

#: MAJOR.MINOR.PATCH, optionally followed by a PEP 440 pre-release segment.
#: The pre-release part is allowed only so this branch can carry `1.0.0b1`
#: while it is in review; a published release drops it.
VERSION_RE = re.compile(r"^(?P<major>\d+)\.\d+\.\d+(?P<pre>(a|b|rc)\d+)?$")


def test_version_is_major_minor_patch_with_at_most_a_prerelease():
    assert VERSION_RE.match(pystorm_a8c.__version__), (
        f"{pystorm_a8c.__version__!r} must be MAJOR.MINOR.PATCH with an "
        f"optional aN/bN/rcN suffix, e.g. 1.0.0 or 1.0.0b1"
    )


def test_the_prerelease_marker_is_written_in_canonical_form():
    """`1.0.0b1`, never `1.0.0-beta1`.

    pyproject.toml is read as a raw string, but `__version__` comes back from
    installed metadata, which is PEP 440 *normalized*. A non-canonical spelling
    makes the two disagree and surfaces as test_version_matches_pyproject
    telling you to reinstall, which is not the problem.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.9 / 3.10
        import tomli as tomllib

    pyproject = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject.open("rb") as fh:
        declared = tomllib.load(fh)["project"]["version"]

    assert VERSION_RE.match(declared), (
        f"pyproject.toml declares {declared!r}; write pre-releases as bN "
        f"(e.g. 1.0.0b1) so the raw string matches the normalized metadata"
    )


def test_version_carries_no_epoch_or_local_label():
    # The distribution name already distinguishes this from upstream pystorm;
    # the retired +a8c.N suffix scheme must not creep back in.
    assert "!" not in pystorm_a8c.__version__
    assert "+" not in pystorm_a8c.__version__


def test_major_version_tracks_the_storm_major_line():
    # Major is spoken for by Storm compatibility: 1.x <-> Storm 1.x.
    from pystorm_a8c.storm import STORM_IDL_VERSION

    major = VERSION_RE.match(pystorm_a8c.__version__).group("major")
    assert STORM_IDL_VERSION == f"{major}.x"


def test_version_matches_pyproject():
    """pyproject.toml is the single source; __version__ is derived from it.

    They cannot drift by editing one and forgetting the other -- but they CAN
    drift if pyproject.toml was edited without reinstalling, which leaves stale
    .dist-info metadata. This is the test that makes that state obvious instead
    of confusing.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.9 / 3.10
        import tomli as tomllib

    pyproject = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject.open("rb") as fh:
        declared = tomllib.load(fh)["project"]["version"]

    assert pystorm_a8c.__version__ == declared, (
        f"installed metadata says {pystorm_a8c.__version__!r} but pyproject.toml "
        f"says {declared!r} -- reinstall the package (`uv sync`)"
    )


def test_storm_went_away_is_exported():
    from pystorm_a8c.exceptions import StormWentAwayError

    assert issubclass(StormWentAwayError, Exception)
