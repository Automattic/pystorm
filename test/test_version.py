import pathlib
import re

import pytest

import pystorm_a8c

VERSION_RE = re.compile(r"^(?P<major>\d+)\.\d+\.\d+$")


def test_version_is_a_plain_release():
    assert VERSION_RE.match(
        pystorm_a8c.__version__
    ), f"{pystorm_a8c.__version__!r} must be MAJOR.MINOR.PATCH, e.g. 1.0.0"


def test_version_carries_no_epoch_or_local_label():
    # The distribution name already distinguishes this from upstream pystorm;
    # the retired +a8c.N suffix scheme must not creep back in.
    assert "!" not in pystorm_a8c.__version__
    assert "+" not in pystorm_a8c.__version__


@pytest.mark.xfail(reason="pystorm_a8c.storm lands in Task 6", strict=False)
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
