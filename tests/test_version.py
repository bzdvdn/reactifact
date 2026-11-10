"""The distribution version is the single source of truth."""

from importlib.metadata import version

import reactifact


def test_version_matches_installed_distribution():
    assert reactifact.__version__ == version("reactifact")
