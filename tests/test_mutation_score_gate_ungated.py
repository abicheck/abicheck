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

"""Unit tests for scripts/check_mutation_score.py's ungated-run guard.

Split out of tests/test_mutation_score_gate.py (which was already past the
architecture gate's 1200-line test-file cap) rather than grown further — see
that file for the gate's parser/drift/scoping tests generally; this file is
scoped to one class, `TestUngatedRun`.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_GATE_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "check_mutation_score.py"
)
_spec = importlib.util.spec_from_file_location("check_mutation_score", _GATE_PATH)
assert _spec and _spec.loader
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def _write(tmp_path: Path, name: str, text: str) -> str:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def _baseline(tmp_path: Path, modules: dict[str, int]) -> str:
    return _write(
        tmp_path,
        "mutation-baseline.json",
        json.dumps(
            {"modules": {m: {"survivors": n, "keys": []} for m, n in modules.items()}}
        ),
    )


_DIFF = """\
diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py
--- a/abicheck/diff_types.py
+++ b/abicheck/diff_types.py
@@ -1,0 +2,1 @@
+    pass
"""

_SOURCE = """\
def alpha():
    return 1


def untouched():
    return 2
"""


def _diff_scoped_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str]:
    """Set up a diff-scoped run whose *only* possible failure is diff-scoped.

    The per-module baseline is deliberately set high enough to be satisfied, so
    each of these tests isolates one axis instead of passing/failing for two
    reasons at once.
    """
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    return (
        _write(tmp_path, "d.diff", _DIFF),
        _baseline(tmp_path, {"abicheck/diff_types.py": 10}),
    )


#: A diff that touches only a test file — the shape a PR takes when it weakens
#: or deletes assertions without editing the detector.
_TEST_ONLY_DIFF = """diff --git a/tests/test_diff_types.py b/tests/test_diff_types.py
--- a/tests/test_diff_types.py
+++ b/tests/test_diff_types.py
@@ -4,2 +4,1 @@
+    assert result is not None
"""


class TestUngatedRun:
    """A run that examined nothing must not report a pass.

    `--diff-scoped` is attribution-based, so a branch that weakens a detector
    test without touching a production function gives it nothing to scope to.
    Printing the OK line there claimed a check that never happened (Codex
    review); such a change is only visible as *drift*, which needs the
    baseline.
    """

    def test_a_test_only_diff_without_a_baseline_reports_gating_nothing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _diff_scoped_env(tmp_path, monkeypatch)
        diff = _write(tmp_path, "t.diff", _TEST_ONLY_DIFF)
        results = _write(
            tmp_path, "r.txt", "    abicheck.diff_types.x_alpha__mutmut_1: survived\n"
        )
        rc = gate.main(
            [
                "--results-file",
                results,
                "--baseline-file",
                str(tmp_path / "absent.json"),
                "--diff-scoped",
                "--diff-file",
                diff,
            ]
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "GATED NOTHING" in out
        assert "diff-scoped OK" not in out

    def test_the_same_diff_with_a_baseline_is_gated_as_drift(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Negative control, and the reason the message points at the baseline:
        with one recorded, the identical test-only diff *is* checked — the
        survivor the weakened test allows shows up as per-module drift."""
        _diff_scoped_env(tmp_path, monkeypatch)
        diff = _write(tmp_path, "t.diff", _TEST_ONLY_DIFF)
        baseline = _baseline(tmp_path, {"abicheck/diff_types.py": 0})
        results = _write(
            tmp_path, "r.txt", "    abicheck.diff_types.x_alpha__mutmut_1: survived\n"
        )
        rc = gate.main(
            [
                "--results-file",
                results,
                "--baseline-file",
                baseline,
                "--diff-scoped",
                "--diff-file",
                diff,
            ]
        )
        out = capsys.readouterr().out
        assert rc == 1
        assert "GATED NOTHING" not in out
        assert "0 -> 1" in out

    def test_a_changed_function_still_reports_ok(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Second negative control: the ungated message must not swallow the
        ordinary clean run."""
        diff, _ = _diff_scoped_env(tmp_path, monkeypatch)
        results = _write(
            tmp_path,
            "r.txt",
            "    abicheck.diff_types.x_untouched__mutmut_1: survived\n",
        )
        rc = gate.main(
            [
                "--results-file",
                results,
                "--baseline-file",
                str(tmp_path / "absent.json"),
                "--diff-scoped",
                "--diff-file",
                diff,
            ]
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "diff-scoped OK" in out
        assert "GATED NOTHING" not in out

    def test_require_baseline_turns_an_ungated_run_into_a_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The PR lane accepts an ungated run (the baseline is a maintainer
        artifact, not the contributor's); a lane that asks to gate does not."""
        _diff_scoped_env(tmp_path, monkeypatch)
        diff = _write(tmp_path, "t.diff", _TEST_ONLY_DIFF)
        results = _write(
            tmp_path, "r.txt", "    abicheck.diff_types.x_alpha__mutmut_1: survived\n"
        )
        assert (
            gate.main(
                [
                    "--results-file",
                    results,
                    "--baseline-file",
                    str(tmp_path / "absent.json"),
                    "--diff-scoped",
                    "--diff-file",
                    diff,
                    "--require-baseline",
                ]
            )
            == 1
        )

    def test_require_baseline_is_not_satisfied_by_diff_scoped_alone(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The mixed diff: a changed production function *and* a weakened test.

        --diff-scoped is a real gate, but it answers a narrower question —
        only whether the functions this branch changed have survivors. The
        survivors a weakened test allows in an *untouched* function are
        outside its scope, so treating it as satisfying --require-baseline let
        that diff exit 0 with no drift reference at all (Codex review).
        """
        diff, _ = _diff_scoped_env(tmp_path, monkeypatch)
        results = _write(
            tmp_path,
            "r.txt",
            "    abicheck.diff_types.x_untouched__mutmut_1: survived\n",
        )
        rc = gate.main(
            [
                "--results-file",
                results,
                "--baseline-file",
                str(tmp_path / "absent.json"),
                "--diff-scoped",
                "--diff-file",
                diff,
                "--require-baseline",
            ]
        )
        assert rc == 1
        assert "--diff-scoped does not substitute" in capsys.readouterr().out

    def test_require_baseline_is_satisfied_by_a_recorded_baseline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Negative control: the flag must not become unsatisfiable. With a
        baseline recorded, the same mixed diff is checked and passes."""
        diff, baseline = _diff_scoped_env(tmp_path, monkeypatch)
        results = _write(
            tmp_path,
            "r.txt",
            "    abicheck.diff_types.x_untouched__mutmut_1: survived\n",
        )
        assert (
            gate.main(
                [
                    "--results-file",
                    results,
                    "--baseline-file",
                    baseline,
                    "--diff-scoped",
                    "--diff-file",
                    diff,
                    "--require-baseline",
                ]
            )
            == 0
        )

    def test_the_receipt_records_whether_anything_was_gated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _diff_scoped_env(tmp_path, monkeypatch)
        diff = _write(tmp_path, "t.diff", _TEST_ONLY_DIFF)
        results = _write(
            tmp_path, "r.txt", "    abicheck.diff_types.x_alpha__mutmut_1: survived\n"
        )
        receipt = tmp_path / "receipt.json"
        gate.main(
            [
                "--results-file",
                results,
                "--baseline-file",
                str(tmp_path / "absent.json"),
                "--diff-scoped",
                "--diff-file",
                diff,
                "--json",
                str(receipt),
            ]
        )
        assert json.loads(receipt.read_text())["gated"] is False

    def test_a_report_only_run_does_not_claim_to_have_gated(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No --diff-scoped and no baseline of either kind: nothing in this
        invocation can fail, so the receipt must not say it gated. The flag
        used to default to True and was only cleared inside the diff-scoped
        arm, so precisely the run that checks nothing at all reported
        ``"gated": true`` (Codex review)."""
        results = _write(
            tmp_path, "r.txt", "    abicheck.diff_types.x_alpha__mutmut_1: survived\n"
        )
        receipt = tmp_path / "receipt.json"
        rc = gate.main(
            [
                "--results-file",
                results,
                "--baseline-file",
                str(tmp_path / "absent.json"),
                "--json",
                str(receipt),
            ]
        )
        assert rc == 0
        assert json.loads(receipt.read_text())["gated"] is False

    def test_a_global_baseline_alone_counts_as_gated(self, tmp_path: Path) -> None:
        """Negative control for the above: SURVIVOR_BASELINE is a real gate on
        its own (the survivors-vs-total comparison below runs and can fail),
        so a run carrying one must not be reported as having gated nothing —
        which is what a `gated = False` default would have done."""
        results = _write(
            tmp_path, "r.txt", "    abicheck.diff_types.x_alpha__mutmut_1: survived\n"
        )
        receipt = tmp_path / "receipt.json"
        rc = gate.main(
            [
                "--results-file",
                results,
                "--baseline-file",
                str(tmp_path / "absent.json"),
                "--baseline",
                "1",
                "--json",
                str(receipt),
            ]
        )
        assert rc == 0
        assert json.loads(receipt.read_text())["gated"] is True
