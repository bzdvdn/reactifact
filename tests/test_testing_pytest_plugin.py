"""`reactifact.testing.pytest_plugin` — the opt-in pytest integration, run in
an isolated pytest instance through `pytester`."""

from __future__ import annotations

from pytest import Pytester


def _enable(pytester: Pytester) -> None:
    pytester.makeconftest('pytest_plugins = ["reactifact.testing.pytest_plugin"]')


def test_async_scenario_tests_run_and_scenario_skip_becomes_skip(
    pytester: Pytester,
) -> None:
    _enable(pytester)
    pytester.makepyfile(
        test_scenarios="""
        from reactifact.testing import ScenarioSkip


        async def test_async_runs():
            assert True


        async def test_opts_out_without_a_key():
            raise ScenarioSkip("no API key")
        """
    )

    result = pytester.runpytest("-q")

    result.assert_outcomes(passed=1, skipped=1)


def test_scenario_lab_fixture_builds_a_lab(pytester: Pytester) -> None:
    _enable(pytester)
    pytester.makepyfile(
        test_fixture="""
        def test_factory(scenario_lab):
            result = scenario_lab([]).run_sync()
            assert result.report.turns == 1
        """
    )

    result = pytester.runpytest("-q")

    result.assert_outcomes(passed=1)
