"""The mutmut Hypothesis relaxations reach every test, not only the profile.

`tests/conftest.py` relaxes two Hypothesis health checks when running under
mutmut (`MUTANT_UNDER_TEST` present). That used to be a settings *profile*
only, and a test's own `@settings(suppress_health_check=[...])` replaces a
profile's list instead of extending it: a test suppressing just `too_slow`
re-enabled `differing_executors`, and mutmut's second in-process pass over
the suite aborted the lane's clean run on
`test_clang_template_index_reuse.py`. Nine test modules state their own
suppressions, so the fix is a per-test merge, and these tests state it as
invariants rather than for the one reported test.
"""

from __future__ import annotations

import importlib.util
import itertools
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from hypothesis import HealthCheck, settings

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFTEST = REPO_ROOT / "tests" / "conftest.py"

# Stated independently of conftest's own constant: the two checks mutmut's
# driver trips for reasons unrelated to any property.
EXPECTED_EXTRA = {HealthCheck.too_slow, HealthCheck.differing_executors}


@pytest.fixture(scope="module")
def conftest_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_conftest_mutmut_probe", CONFTEST)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _all_suppression_lists() -> list[tuple[HealthCheck, ...]]:
    members = list(HealthCheck)
    return [
        combo
        for size in range(len(members) + 1)
        for combo in itertools.combinations(members, size)
    ]


def test_widening_keeps_every_explicit_suppression_and_adds_the_mutmut_ones(
    conftest_module: ModuleType,
) -> None:
    """Exhaustive over every subset of health checks a test could state."""
    combos = _all_suppression_lists()
    assert len(combos) == 2 ** len(list(HealthCheck))  # vacuity guard
    wrong = []
    for combo in combos:
        original = settings(suppress_health_check=combo, max_examples=7, deadline=123)
        widened = conftest_module._widen_settings_for_mutmut(original)
        got = set(widened.suppress_health_check)
        if got != set(combo) | EXPECTED_EXTRA:
            wrong.append((combo, got))
        # Everything but the two relaxed knobs is the test's own choice.
        assert widened.max_examples == 7
        assert widened.deadline is None
    assert not wrong, wrong[:5]


def _fake_item(test_settings: settings) -> SimpleNamespace:
    def fn() -> None: ...

    fn._hypothesis_internal_use_settings = test_settings  # type: ignore[attr-defined]
    return SimpleNamespace(function=fn)


def test_hook_widens_only_under_mutmut(
    conftest_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    only_slow = settings(suppress_health_check=[HealthCheck.too_slow])

    monkeypatch.delenv("MUTANT_UNDER_TEST", raising=False)
    item = _fake_item(only_slow)
    conftest_module._apply_mutmut_hypothesis_settings([item])
    assert item.function._hypothesis_internal_use_settings is only_slow

    # Presence, not truthiness: mutmut's clean run sets it to "".
    monkeypatch.setenv("MUTANT_UNDER_TEST", "")
    item = _fake_item(only_slow)
    plain = SimpleNamespace(function=lambda: None)  # not a Hypothesis test
    conftest_module._apply_mutmut_hypothesis_settings([item, plain])
    assert EXPECTED_EXTRA <= set(
        item.function._hypothesis_internal_use_settings.suppress_health_check
    )


@pytest.mark.parametrize(
    "suppress",
    ["[HealthCheck.too_slow]", "[HealthCheck.filter_too_much]", "[]"],
)
def test_two_in_process_passes_survive_like_mutmut_runs_them(
    tmp_path: Path, suppress: str
) -> None:
    """End to end: the real conftest, a class-based `@given` test with its own
    suppression list, and two `pytest.main` calls in one process under
    `MUTANT_UNDER_TEST` -- mutmut's exact driving pattern. Before the fix the
    second pass failed with `differing_executors`."""
    (tmp_path / "conftest.py").write_text(
        CONFTEST.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "test_prop.py").write_text(
        textwrap.dedent(
            f"""
            from hypothesis import HealthCheck, given, settings, strategies as st

            class TestProp:
                @settings(max_examples=5, suppress_health_check={suppress})
                @given(st.integers())
                def test_it(self, x):
                    assert isinstance(x, int)
            """
        ),
        encoding="utf-8",
    )
    driver = textwrap.dedent(
        """
        import os, sys, pytest
        os.environ["MUTANT_UNDER_TEST"] = ""
        # An explicit basetemp of its own: a nested session on the default
        # numbered basetemp runs pytest's retention pass over the shared
        # pytest-of-<user> root and can delete the outer run's tree -- this
        # test's own tmp_path, which is this process's cwd.
        args = ["-q", "-p", "no:cacheprovider", "-p", "no:randomly", "-p", "no:xdist",
                "--basetemp", "inner-basetemp", "test_prop.py"]
        codes = [int(pytest.main(args)) for _ in range(2)]
        sys.exit(max(codes))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", driver],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]


_STALE_LOCK_PLUGIN = '''
import os, time, pytest

@pytest.hookimpl(trylast=True)
def pytest_sessionstart(session):
    """Make this session's numbered basetemp look abandoned, as a long
    in-process mutmut run's does, so pytest's retention may prune it."""
    bt = session.config._tmp_path_factory.getbasetemp()
    old = time.time() - 5 * 86400
    for p in (bt, bt / ".lock"):
        if p.exists():
            os.utime(p, (old, old))
'''


def test_nested_sessions_never_prune_the_outer_runs_temp_tree(
    tmp_path: Path,
) -> None:
    """Bug class: a test that starts its own pytest sessions must not let them
    clean up the *outer* session's temp tree. Every nested session that uses
    the default numbered basetemp runs pytest's retention pass over the shared
    ``pytest-of-<user>`` root, and once the outer run's lock reads as stale it
    deletes the outer's directory -- including the ``tmp_path`` the nested run
    is standing in. The oracle is the outer run's own verdict on the whole
    module, under a temp root it owns and a lock made to look stale."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / "stale_lock_plugin.py").write_text(
        _STALE_LOCK_PLUGIN, encoding="utf-8"
    )
    temproot = tmp_path / "temproot"
    temproot.mkdir()
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(plugin_dir), os.environ.get("PYTHONPATH", "")]
        ),
        "PYTEST_DEBUG_TEMPROOT": str(temproot),
        "PYTHONIOENCODING": "utf-8",
    }
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(Path(__file__)),
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:randomly",
            "-p",
            "no:xdist",
            "-p",
            "stale_lock_plugin",
            "-k",
            "two_in_process_passes",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
