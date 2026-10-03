# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""``scripts/check_ai_readiness.py``'s ``main()`` -- selection and reporting.

Split out of ``test_ai_readiness.py`` only because that file is at the
AI-readiness gate's own 2000-line hard cap; the checks' own live-tree tests
stay there, and this module holds the orchestration contract around them.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def car():
    """Load ``scripts/check_ai_readiness.py`` as a module.

    Same loader the sibling ``test_ai_readiness.py`` uses -- ``scripts/`` is
    not an importable package.
    """
    path = ROOT / "scripts" / "check_ai_readiness.py"
    spec = importlib.util.spec_from_file_location("check_ai_readiness_main", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# main()'s own contract: selection, skipping, exit codes, JSON.
#
# These replace a former `test_main_returns_zero_on_clean_tree`, which ran
# the *live repository* through nearly the whole checker registry (~5m in a
# measured run) in order to assert one thing about main(): that it returns 0.
# The whole-tree gate is not deleted -- it has an owner, the dedicated
# `ai-readiness` CI job, which runs `verify.py --profile pr --only
# ai-readiness` with no skips at all, i.e. strictly more than that test did.
# What is deleted is re-running the repository a second time, from inside the
# unit lane, to exercise argument dispatch.
#
# Each individual check still has its own live-tree test elsewhere in this
# file; those are the checks' contracts. The tests below are main()'s: that
# it runs what was selected, runs nothing that was skipped, propagates a
# failing check's exit code, and reports the same result twice (human and
# JSON). They drive the *real* main() -- only the registry it dispatches
# over is replaced, with a tiny instrumented one, so the assertions are
# about dispatch rather than about this repository's current contents.
# ---------------------------------------------------------------------------


def _recording_registry(monkeypatch, car, **outcomes):
    """Install a synthetic CHECKS registry and return the call-order list.

    ``outcomes`` maps a check name to ``None`` (clean), an error message, or
    a warning message prefixed with ``"warn:"``. ``main`` reads ``CHECKS``
    at call time (including for argparse's own ``choices``), so patching it
    reconfigures selection and validation together, the way a real registry
    change would.
    """
    called: list[str] = []

    def _make(name, outcome):
        def _check(findings):
            called.append(name)
            if outcome is None:
                return
            if outcome.startswith("warn:"):
                findings.warn(name, outcome[len("warn:") :])
            else:
                findings.err(name, outcome)

        return _check

    monkeypatch.setattr(
        car, "CHECKS", {name: _make(name, out) for name, out in outcomes.items()}
    )
    return called


def test_main_runs_every_registered_check_by_default(car, monkeypatch, capsys):
    called = _recording_registry(
        car=car, monkeypatch=monkeypatch, a=None, b=None, c=None
    )
    rc = car.main([])
    capsys.readouterr()
    assert sorted(called) == ["a", "b", "c"]
    assert rc == 0


def test_main_returns_zero_when_every_check_is_clean(car, monkeypatch, capsys):
    _recording_registry(car=car, monkeypatch=monkeypatch, a=None, b=None)
    assert car.main([]) == 0
    capsys.readouterr()


def test_main_returns_nonzero_when_any_check_errors(car, monkeypatch, capsys):
    """One erroring check is enough, wherever it sits in the registry --
    checked from both ends so a fold that only remembers the last (or the
    first) result cannot pass."""
    for failing in ("a", "b", "c"):
        outcomes = {name: None for name in ("a", "b", "c")}
        outcomes[failing] = f"{failing} is broken"
        _recording_registry(car=car, monkeypatch=monkeypatch, **outcomes)
        rc = car.main([])
        out = capsys.readouterr().out
        assert rc != 0, f"error in {failing} did not fail the run"
        assert f"{failing} is broken" in out


def test_main_passes_on_warnings_but_still_reports_them(car, monkeypatch, capsys):
    """A warning is printed and does not fail -- the documented WARN/ERROR
    split ("Errors fail; warnings print and pass")."""
    _recording_registry(car=car, monkeypatch=monkeypatch, a="warn:a is untidy", b=None)
    rc = car.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "a is untidy" in out


def test_main_only_runs_exactly_the_named_checks(car, monkeypatch, capsys):
    called = _recording_registry(
        car=car, monkeypatch=monkeypatch, a=None, b="b is broken", c=None
    )
    rc = car.main(["--only", "a"])
    capsys.readouterr()
    assert called == ["a"]
    # b was not selected, so its error must not reach the exit code.
    assert rc == 0


def test_main_skip_excludes_a_check_including_a_failing_one(car, monkeypatch, capsys):
    called = _recording_registry(
        car=car, monkeypatch=monkeypatch, a=None, b="b is broken", c=None
    )
    rc = car.main(["--skip", "b"])
    capsys.readouterr()
    assert "b" not in called
    assert sorted(called) == ["a", "c"]
    assert rc == 0


def test_main_skip_wins_over_only_for_the_same_check(car, monkeypatch, capsys):
    """The two selectors compose in one direction only: --skip is applied
    after --only, so naming a check in both runs it zero times, not once."""
    called = _recording_registry(car=car, monkeypatch=monkeypatch, a=None, b=None)
    rc = car.main(["--only", "a", "--skip", "a"])
    capsys.readouterr()
    assert called == []
    assert rc == 0


def test_main_rejects_an_unknown_check_name(car, monkeypatch, capsys):
    """argparse's ``choices`` is built from the live registry, so a
    misspelled --only/--skip is a usage error rather than a silent no-op
    (a silently-ignored name would disable a gate in CI unnoticed)."""
    _recording_registry(car=car, monkeypatch=monkeypatch, a=None)
    for flag in ("--only", "--skip"):
        with pytest.raises(SystemExit) as excinfo:
            car.main([flag, "no-such-check"])
        assert excinfo.value.code != 0
        capsys.readouterr()


def test_main_json_summary_agrees_with_the_exit_code_and_findings(
    car, monkeypatch, capsys
):
    """--json is an additional rendering of the same run, not a second
    opinion about it: every error/warning and the exit code must match what
    the human report was computed from."""
    import json as _json

    _recording_registry(
        car=car,
        monkeypatch=monkeypatch,
        a="a is broken",
        b="warn:b is untidy",
        c=None,
    )
    rc = car.main(["--json"])
    out = capsys.readouterr().out
    payload = _json.loads(out.splitlines()[-1])

    assert payload["exit_code"] == rc
    assert [e["check"] for e in payload["errors"]] == ["a"]
    assert [e["message"] for e in payload["errors"]] == ["a is broken"]
    assert [w["check"] for w in payload["warnings"]] == ["b"]
    assert [w["message"] for w in payload["warnings"]] == ["b is untidy"]


def test_parallel_jobs_report_matches_serial(car, capsys) -> None:
    """``--jobs`` only changes scheduling: the report is identical to serial,
    over several real (cheap, pure) checks with different outcomes."""
    selected = [
        "claude-md-coverage",
        "examples-ground-truth",
        "script-inventory",
        "test-ratio",
    ]
    argv = [a for n in selected for a in ("--only", n)]
    rc_serial = car.main([*argv, "--json"])
    serial = capsys.readouterr().out
    rc_parallel = car.main([*argv, "--json", "--jobs", "3"])
    parallel = capsys.readouterr().out
    assert rc_serial == rc_parallel
    assert serial == parallel


def test_jobs_under_spawn_never_breaks_the_pool(car, capsys, monkeypatch) -> None:
    """Windows and macOS spawn workers, which re-import this module by name.
    The fixture loads it under an invented one, so a spawned pool could not
    unpickle the task: the run must fall back to serial, same report."""
    import multiprocessing

    monkeypatch.setattr(multiprocessing, "get_start_method", lambda *a, **k: "spawn")
    assert car._workers_can_import_this_module() is False
    argv = ["--only", "claude-md-coverage", "--only", "test-ratio", "--json"]
    rc_serial = car.main(argv)
    serial = capsys.readouterr().out
    rc_jobs = car.main([*argv, "--jobs", "2"])
    assert (rc_jobs, capsys.readouterr().out) == (rc_serial, serial)


def test_a_dotted_name_resolves_every_component(tmp_path, monkeypatch) -> None:
    """A findable top-level package proves nothing about the submodule a
    spawned worker must import; only the full name, ending at this file, does."""
    import importlib.util
    import multiprocessing

    monkeypatch.setattr(multiprocessing, "get_start_method", lambda *a, **k: "spawn")
    src = ROOT / "scripts" / "check_ai_readiness.py"
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "real.py").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))

    def load(name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    # The package resolves, the submodule does not: must not claim workers can.
    assert load("pkg.missing", src)._workers_can_import_this_module() is False
    # Resolves fully and lands on the very file that is running: workers can.
    assert load("pkg.real", pkg / "real.py")._workers_can_import_this_module() is True
    # Resolves fully, but to a different file than the one running: they can't.
    assert load("pkg.real", src)._workers_can_import_this_module() is False
