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

"""Contract tests for the strict external-process harness.

Per AGENTS.md's "Primitive-level property tests": this is a reusable helper, so
its contract is stated directly rather than only through its callers.

Every test here is paired — the harness must *accept* the correct invocation
and *reject* the specific wiring bug a permissive `if "aquery" in cmd` mock
lets through. A harness that only accepts is no stricter than what it replaces.
"""

from __future__ import annotations

import subprocess

import pytest
from _strict_process import StrictProcessRunner, UnexpectedProcessCall, proc

AQUERY = ["bazel", "aquery", "--output=jsonproto", "deps(//foo:foo)"]
CQUERY = ["bazel", "cquery", "--output=files", "deps(//foo:foo)"]


def _scripted() -> StrictProcessRunner:
    return (
        StrictProcessRunner()
        .expect(argv_contains=["aquery"], returns=proc(stdout="AQUERY-DATA"))
        .expect(argv_contains=["cquery"], returns=proc(stdout="CQUERY-DATA"))
    )


class TestDistinctFixturePerCommand:
    def test_each_command_gets_its_own_result(self) -> None:
        r = _scripted()
        assert r(AQUERY).stdout == "AQUERY-DATA"
        assert r(CQUERY).stdout == "CQUERY-DATA"
        r.assert_exhausted()

    def test_a_second_call_cannot_silently_reuse_the_first_fixture(self) -> None:
        """The catch-all bug: a fallback path re-running aquery would have been
        answered with cquery's payload by a permissive mock."""
        r = _scripted()
        r(AQUERY)
        with pytest.raises(UnexpectedProcessCall, match="argv is missing token"):
            r(AQUERY)


class TestUnexpectedCallsAreRejected:
    def test_an_extra_call_fails(self) -> None:
        r = _scripted()
        r(AQUERY)
        r(CQUERY)
        with pytest.raises(UnexpectedProcessCall, match="no calls left"):
            r(["bazel", "build", "//foo"])

    def test_a_completely_unrelated_command_fails(self) -> None:
        r = _scripted()
        with pytest.raises(UnexpectedProcessCall, match="argv is missing token"):
            r(["curl", "https://example.invalid"])

    def test_a_dropped_argument_fails(self) -> None:
        r = StrictProcessRunner().expect(argv=AQUERY, returns=proc())
        with pytest.raises(UnexpectedProcessCall, match="not exactly"):
            r(["bazel", "aquery", "deps(//foo:foo)"])  # --output dropped


class TestMissingCallsAreRejected:
    def test_assert_exhausted_catches_a_command_that_never_ran(self) -> None:
        """The half a permissive mock structurally cannot check."""
        r = _scripted()
        r(AQUERY)
        with pytest.raises(AssertionError, match="never happened"):
            r.assert_exhausted()

    def test_assert_exhausted_passes_when_the_script_completed(self) -> None:
        r = _scripted()
        r(AQUERY)
        r(CQUERY)
        r.assert_exhausted()

    def test_no_calls_at_all_is_reported(self) -> None:
        r = _scripted()
        with pytest.raises(AssertionError, match="no commands were run"):
            r.assert_exhausted()


class TestOrdering:
    def test_ordered_mode_rejects_a_swapped_sequence(self) -> None:
        r = _scripted()
        with pytest.raises(UnexpectedProcessCall, match="does not match the next"):
            r(CQUERY)

    def test_unordered_mode_accepts_either_order_but_still_requires_both(self) -> None:
        r = StrictProcessRunner(ordered=False)
        r.expect(argv_contains=["aquery"], returns=proc(stdout="A"))
        r.expect(argv_contains=["cquery"], returns=proc(stdout="C"))
        assert r(CQUERY).stdout == "C"
        assert r(AQUERY).stdout == "A"
        r.assert_exhausted()

    def test_unordered_mode_still_rejects_an_unscripted_command(self) -> None:
        r = StrictProcessRunner(ordered=False)
        r.expect(argv_contains=["aquery"], returns=proc())
        with pytest.raises(UnexpectedProcessCall, match="matched no remaining"):
            r(["bazel", "build"])


class TestEnvironmentAndCwd:
    def test_cwd_mismatch_is_caught(self, tmp_path) -> None:
        r = StrictProcessRunner().expect(
            argv_contains=["aquery"], cwd=tmp_path, returns=proc()
        )
        with pytest.raises(UnexpectedProcessCall, match="cwd is"):
            r(AQUERY, cwd="/somewhere/else")

    def test_cwd_match_passes(self, tmp_path) -> None:
        r = StrictProcessRunner().expect(
            argv_contains=["aquery"], cwd=tmp_path, returns=proc()
        )
        r(AQUERY, cwd=str(tmp_path))
        r.assert_exhausted()

    def test_missing_env_key_is_caught(self) -> None:
        r = StrictProcessRunner().expect(
            argv_contains=["aquery"],
            env_contains={"BAZEL_FLAGS": "-c opt"},
            returns=proc(),
        )
        with pytest.raises(UnexpectedProcessCall, match="env is missing"):
            r(AQUERY, env={"PATH": "/usr/bin"})

    def test_wrong_env_value_is_caught(self) -> None:
        r = StrictProcessRunner().expect(
            argv_contains=["aquery"],
            env_contains={"BAZEL_FLAGS": "-c opt"},
            returns=proc(),
        )
        with pytest.raises(UnexpectedProcessCall, match=r"env\['BAZEL_FLAGS'\]"):
            r(AQUERY, env={"BAZEL_FLAGS": "-c dbg"})


class TestDiagnostics:
    def test_failure_message_carries_the_whole_transcript(self) -> None:
        """Without the transcript a mismatch says what failed but not what ran."""
        r = _scripted()
        r(AQUERY)
        r(CQUERY)
        with pytest.raises(UnexpectedProcessCall) as exc:
            r(["bazel", "build", "//foo"])
        message = str(exc.value)
        assert "transcript of all commands run" in message
        assert "bazel aquery" in message
        assert "bazel cquery" in message
        assert "1." in message and "2." in message

    def test_transcript_records_cwd(self, tmp_path) -> None:
        r = StrictProcessRunner().expect(argv_contains=["aquery"], returns=proc())
        r(AQUERY, cwd=tmp_path)
        assert f"cwd={tmp_path}" in r.transcript()

    def test_call_count_assertion(self) -> None:
        r = _scripted()
        r(AQUERY)
        r(CQUERY)
        r.assert_call_count(2)
        with pytest.raises(AssertionError, match="expected 3"):
            r.assert_call_count(3)


class TestResultShapes:
    def test_returns_a_real_completed_process(self) -> None:
        r = StrictProcessRunner().expect(argv_contains=["x"], returns=proc(stdout="s"))
        result = r(["x"])
        assert isinstance(result, subprocess.CompletedProcess)
        assert result.stdout == "s" and result.returncode == 0

    def test_a_callable_result_sees_the_actual_argv(self) -> None:
        r = StrictProcessRunner().expect(
            argv_contains=["x"], returns=lambda argv, kw: proc(stdout=" ".join(argv))
        )
        assert r(["x", "--flag"]).stdout == "x --flag"

    def test_raises_simulates_a_failing_tool(self) -> None:
        r = StrictProcessRunner().expect(
            argv_contains=["x"], raises=FileNotFoundError("bazel not found")
        )
        with pytest.raises(FileNotFoundError):
            r(["x"])


class TestCatchAllIsRefused:
    def test_an_expectation_with_no_matcher_is_an_error(self) -> None:
        """The whole point: you cannot script a permissive catch-all."""
        r = StrictProcessRunner().expect(returns=proc())
        with pytest.raises(AssertionError, match="no argv matcher"):
            r(["anything", "at", "all"])


class TestInstall:
    def test_install_patches_the_named_attribute(self, monkeypatch) -> None:
        class Holder:
            @staticmethod
            def run_bounded(cmd, **kw):  # pragma: no cover - replaced
                raise AssertionError("real runner must not be called")

        r = StrictProcessRunner().expect(
            argv_contains=["aquery"], returns=proc(stdout="A")
        )
        r.install(monkeypatch, Holder, "run_bounded")
        assert Holder.run_bounded(AQUERY).stdout == "A"
        r.assert_exhausted()
