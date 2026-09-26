"""Test-suite configuration.

`pytester` is enabled so `tests/test_testing_pytest_plugin.py` can spin up
isolated pytest runs that opt into `reactifact.testing.pytest_plugin`.
"""

pytest_plugins = ["pytester"]
