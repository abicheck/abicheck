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

"""Workstream D-S1 (vision-api-abi-evolution.md "D. Optional prebuilt-
consumer lifecycle") regression: a supplied ``--used-by``/``--required-
symbol(s)`` consumer's own result must **enrich** the global compatibility
result, never substitute for it or narrow it.

Two layers, per this repo's bug-class regression contract
(``tests/regressions/manifest.py``, id ``gating.consumer_scope_enrichment``):

* :class:`TestExitDecisionInvariantToConsumerScope` -- a property test at
  the primitive that decided the *old*, now-reverted behavior
  (``policy.exit_decision.resolve_compare_exit_decision``): for ANY
  full-library verdict/severity combination, stamping arbitrary (including
  wildly different) ``scoped_verdict``/``scoped_exit_code``/``gate_scope``
  values onto the same ``DiffResult`` must never change the resolved
  compatibility contribution. This is the actual mechanism the bug lived
  in -- a single fixed example proved nothing about the general rule.
* :class:`TestCliExitCodeInvariantToConsumerScope` -- the same invariant
  one level up, through the real ``compare --used-by`` CLI path (stubbed
  dumper + stubbed ``appcompat.scope_diff_to_app``, the same fixture shape
  ``tests/test_cov95_cli.py::TestUsedByScoping`` uses): the process's exit
  code for a given OLD/NEW pair does not depend on whether ``--used-by`` was
  passed, or on what verdict the supplied consumer's own scoped result
  reports.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from click.testing import CliRunner
from hypothesis import given, strategies as st

from abicheck.checker import DiffResult
from abicheck.checker_policy import Verdict
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.policy.exit_decision import resolve_compare_exit_decision

# ---------------------------------------------------------------------------
# Layer 1: the primitive that used to special-case `scoped_exit_code`
# ---------------------------------------------------------------------------

_VERDICTS = (
    Verdict.NO_CHANGE,
    Verdict.COMPATIBLE,
    Verdict.COMPATIBLE_WITH_RISK,
    Verdict.API_BREAK,
    Verdict.BREAKING,
)

_SCOPES = (None, "used_by", "required_symbol", "anything_else")


def _diff_result(verdict: Verdict) -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so",
        changes=[],
        verdict=verdict,
    )


@st.composite
def _consumer_stamp(draw: st.DrawFn) -> dict[str, object]:
    """A random (possibly contradictory) set of consumer-scope attributes.

    ``scoped_verdict``/``scoped_exit_code`` are drawn independently of the
    full-library verdict passed to the test -- the whole point is that they
    may disagree completely, and the resolved compatibility contribution
    must not care.
    """
    has_scope = draw(st.booleans())
    if not has_scope:
        return {}
    stamp: dict[str, object] = {
        "scoped_verdict": draw(st.sampled_from(_VERDICTS)),
        "gate_scope": draw(st.sampled_from(_SCOPES)),
    }
    has_exit_code = draw(st.booleans())
    if has_exit_code:
        exit_code = draw(st.sampled_from([0, 1, 2, 4]))
        stamp["scoped_exit_code"] = exit_code
        stamp["scoped_compatibility_contribution"] = exit_code
    return stamp


class TestExitDecisionInvariantToConsumerScope:
    """`resolve_compare_exit_decision` must ignore `scoped_*`/`gate_scope`."""

    @given(verdict=st.sampled_from(_VERDICTS), stamp=_consumer_stamp())
    def test_compatibility_contribution_ignores_consumer_scope_stamp(
        self, verdict: Verdict, stamp: dict[str, object]
    ) -> None:
        baseline = resolve_compare_exit_decision(_diff_result(verdict), None, "legacy")

        stamped = _diff_result(verdict)
        for key, value in stamp.items():
            setattr(stamped, key, value)
        decision = resolve_compare_exit_decision(stamped, None, "legacy")

        assert decision.compatibility_contribution == (
            baseline.compatibility_contribution
        )
        assert decision.code == baseline.code
        assert decision.reasons == baseline.reasons

    def test_scoped_gate_reason_is_never_produced(self) -> None:
        """`ExitReason.SCOPED_GATE` is kept only for historical/persisted
        data -- this resolver must never produce it going forward."""
        from abicheck.policy.exit_decision import ExitReason

        for verdict in _VERDICTS:
            result = _diff_result(verdict)
            result.scoped_verdict = Verdict.BREAKING
            result.scoped_exit_code = 4
            result.scoped_compatibility_contribution = 4
            result.gate_scope = "used_by"
            decision = resolve_compare_exit_decision(result, None, "legacy")
            assert ExitReason.SCOPED_GATE not in decision.reasons


# ---------------------------------------------------------------------------
# Layer 2: the real `compare --used-by` CLI path
# ---------------------------------------------------------------------------


def _snap(version: str, funcs: list[Function] | None = None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so",
        version=version,
        functions=funcs or [],
    )


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        visibility=Visibility.PUBLIC,
    )


def _invoke(*args: str):
    return CliRunner().invoke(main, list(args))


class TestCliExitCodeInvariantToConsumerScope:
    """The real `compare` process's exit code for a given OLD/NEW pair does
    not depend on whether `--used-by` was passed, nor on what verdict the
    supplied consumer's own scoped result reports.

    Uses ``unittest.mock.patch`` context managers rather than the pytest
    ``monkeypatch`` fixture: a function-scoped fixture is not reset between
    Hypothesis-generated inputs and Hypothesis's own health check rejects
    it outright, whereas a context manager entered and exited within the
    test body is fully independent per example.
    """

    def _write_binaries(self, tmp_path):
        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old = tmp_path / "old.so"
        old.write_bytes(b"\x7fELF" + b"\x00" * 200)
        new = tmp_path / "new.so"
        new.write_bytes(b"\x7fELF" + b"\x00" * 200)
        return app, old, new

    @given(
        removed=st.booleans(),
        consumer_verdict=st.sampled_from(
            (Verdict.COMPATIBLE, Verdict.API_BREAK, Verdict.BREAKING)
        ),
        consumer_missing=st.booleans(),
    )
    def test_exit_code_matches_the_unscoped_run(
        self,
        tmp_path_factory,
        removed: bool,
        consumer_verdict: Verdict,
        consumer_missing: bool,
    ) -> None:
        """For a fixed OLD/NEW pair (real removal, or none), the exit code
        `compare --used-by app` reports is exactly what plain `compare`
        would report -- whatever the stubbed consumer's own scoped verdict
        says, and whether or not it reports a missing symbol of its own.
        """
        from unittest.mock import patch

        from abicheck.appcompat import AppCompatResult

        old_funcs = [_fn("foo", "_Z3foov"), _fn("bar", "_Z3barv")]
        new_funcs = [_fn("bar", "_Z3barv")] if removed else list(old_funcs)

        app, old, new = self._write_binaries(tmp_path_factory.mktemp("unscoped"))
        with patch(
            "abicheck.dumper.dump",
            MagicMock(side_effect=[_snap("1.0", old_funcs), _snap("2.0", new_funcs)]),
        ):
            unscoped = _invoke("compare", str(old), str(new))

        app, old, new = self._write_binaries(tmp_path_factory.mktemp("scoped"))
        consumer_result = AppCompatResult(
            app_path=str(app),
            old_lib_path=str(old),
            new_lib_path=str(new),
            required_symbols={"_Z3foov"},
            required_symbol_count=1,
            missing_symbols=(["needed_elsewhere"] if consumer_missing else []),
            verdict=consumer_verdict,
            symbol_coverage=0.0,
        )
        with (
            patch(
                "abicheck.dumper.dump",
                MagicMock(
                    side_effect=[_snap("1.0", old_funcs), _snap("2.0", new_funcs)]
                ),
            ),
            patch(
                "abicheck.appcompat.scope_diff_to_app",
                lambda *a, **k: consumer_result,
            ),
        ):
            scoped = _invoke("compare", str(old), str(new), "--used-by", str(app))

        assert scoped.exit_code == unscoped.exit_code, (
            unscoped.output,
            scoped.output,
        )


class TestConsumerImpactSummary:
    """Workstream D-S1: "N of M consumers affected" over every supplied
    ``--used-by``/``--used-by-manifest`` consumer, and ``--used-by-manifest``
    parity with a bare ``--used-by`` path."""

    def _write_binaries(self, tmp_path):
        old = tmp_path / "old.so"
        old.write_bytes(b"\x7fELF" + b"\x00" * 200)
        new = tmp_path / "new.so"
        new.write_bytes(b"\x7fELF" + b"\x00" * 200)
        app1 = tmp_path / "app1"
        app1.write_bytes(b"\x7fELF" + b"\x00" * 200)
        app2 = tmp_path / "app2"
        app2.write_bytes(b"\x7fELF" + b"\x00" * 200)
        return old, new, app1, app2

    def test_n_of_m_affected_across_used_by_and_manifest(self, tmp_path, monkeypatch):
        import json as json_mod

        from abicheck.appcompat import AppCompatResult

        old, new, app1, app2 = self._write_binaries(tmp_path)
        manifest = tmp_path / "consumers.json"
        manifest.write_text(
            json_mod.dumps({"consumers": [{"path": str(app2)}]}), encoding="utf-8"
        )

        results_by_app = {
            str(app1): AppCompatResult(
                app_path=str(app1),
                old_lib_path=str(old),
                new_lib_path=str(new),
                verdict=Verdict.BREAKING,
                symbol_coverage=0.0,
            ),
            str(app2): AppCompatResult(
                app_path=str(app2),
                old_lib_path=str(old),
                new_lib_path=str(new),
                verdict=Verdict.COMPATIBLE,
                symbol_coverage=100.0,
            ),
        }

        def _fake_scope(diff, app, old_lib, new_lib, **kwargs):
            from abicheck.model.consumer_spec import as_consumer_spec

            return results_by_app[str(as_consumer_spec(app).path)]

        monkeypatch.setattr("abicheck.appcompat.scope_diff_to_app", _fake_scope)
        monkeypatch.setattr(
            "abicheck.dumper.dump",
            MagicMock(side_effect=[_snap("1.0"), _snap("2.0")]),
        )
        result = _invoke(
            "compare",
            str(old),
            str(new),
            "--used-by",
            str(app1),
            "--used-by-manifest",
            str(manifest),
            "-o",
            "json=-",
        )
        assert result.exit_code in (0, 2, 4), result.output
        # A missing-header warning may precede the JSON payload on stdout;
        # the payload itself is always the trailing `{...}` document.
        payload = json_mod.loads(result.output[result.output.index("{") :])
        summary = payload["consumer_impact_summary"]
        assert summary["total"] == 2
        assert summary["evaluated"] == 2
        assert summary["affected"] == 1
        assert summary["unreadable_advisory"] == 0
        used_by_apps = {entry["app"]: entry for entry in payload["used_by"]}
        assert used_by_apps[str(app1)]["verdict"] == "BREAKING"
        assert used_by_apps[str(app2)]["verdict"] == "COMPATIBLE"
        # A bare --used-by/--used-by-manifest consumer with no provenance
        # carries none of the new optional fields (byte-identical shape to
        # before Workstream D-S1).
        assert "platform" not in used_by_apps[str(app2)]
        assert "requirement" not in used_by_apps[str(app2)]
