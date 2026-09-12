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

"""``action/run.sh`` must not publish COMPATIBLE for a result it cannot read.

The defect: ``_resolve_clean_exit_verdict`` opened with
``VERDICT="COMPATIBLE"`` and only ever *escalated* from a report it could
read, so an abicheck that exited 0 while writing no usable JSON report
published "No binary ABI break detected" -- a compatibility claim nothing had
established. The axis predicates (``_assurance_gated`` and friends) could not
close this on their own: each deliberately answers "not gated by this axis"
for an unreadable report, which ADR-063 Track T8 chose over reconstructing an
axis from forgeable stderr prose, and that choice is still right.

This file executes the *whole* of ``run.sh`` for each shape, rather than
extracting the verdict snippet. The reader's own unit-level invariants live in
``tests/test_action_report_query.py``; what is verified here is that the
verdict, the step's exit code, the Job Summary and the ``verdict`` output all
agree -- the previous fix in this area (PR #705 -> #758) shipped a defense that
asserted file *text* rather than executing the path, and this is the same
class of mistake to avoid.

Bug class: ``report.unestablished_result_reads_as_success``
(``tests/regressions/manifest.py``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _action_run_sh_harness import (
    REQUIRES_POSIX_SHELL,
    _compare_env,
    _lib,
    _run_action,
    _stub_abicheck,
    _stub_stdout_only,
)

pytestmark = REQUIRES_POSIX_SHELL

#: Each is a distinct way for an exit-0 run to leave no readable result. The
#: reported instance was ``{}``; enumerating the class is the point -- a fix
#: tested against one member forecloses one input.
UNUSABLE_PAYLOADS = (
    ("no report written", None),
    ("zero bytes", b""),
    ("truncated json", b'{"verdict": "COMPATIBLE"'),
    ("not json", b"Killed\n"),
    ("json array", b"[]"),
    ("bare string", b'"COMPATIBLE"'),
    ("json null", b"null"),
    ("empty object", b"{}"),
    ("parsed but resultless", b'{"error": "write interrupted"}'),
    # Codex's counterexamples to the presence-only recognizer: both parse, both
    # are non-empty, and neither carries a verdict anything can read.
    ("null findings, no verdict", b'{"findings": null}'),
    ("no_baseline with no findings array", b'{"no_baseline": true}'),
)


class TestExitZeroWithoutAReadableReport:
    @pytest.mark.parametrize(
        "label,payload", UNUSABLE_PAYLOADS, ids=[p[0] for p in UNUSABLE_PAYLOADS]
    )
    def test_never_publishes_compatible(
        self, tmp_path: Path, label: str, payload: bytes | None
    ) -> None:
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=payload)
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs.get("verdict") != "COMPATIBLE", f"{label}: {outputs}"
        assert outputs.get("verdict") == "REPORT_UNREADABLE", f"{label}: {outputs}"

    @pytest.mark.parametrize(
        "label,payload", UNUSABLE_PAYLOADS, ids=[p[0] for p in UNUSABLE_PAYLOADS]
    )
    def test_fails_the_step(
        self, tmp_path: Path, label: str, payload: bytes | None
    ) -> None:
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=payload)
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs["_exit"] == 1, f"{label}: {outputs}"

    @pytest.mark.parametrize(
        "label,payload", UNUSABLE_PAYLOADS, ids=[p[0] for p in UNUSABLE_PAYLOADS]
    )
    def test_is_not_waived_by_fail_on_breaking_false(
        self, tmp_path: Path, label: str, payload: bytes | None
    ) -> None:
        # `fail-on-breaking: false` says "do not fail me for an ABI break". It
        # is not a statement that an unverifiable result should be reported as
        # a pass, and every other non-compatibility axis in this script
        # (budget overflow, evidence-contract error, coverage, assurance,
        # scope) is likewise unconditional.
        env = _compare_env(tmp_path) | {
            "INPUT_FAIL_ON_BREAKING": "false",
            "INPUT_FAIL_ON_API_BREAK": "false",
        }
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=payload)
        outputs = _run_action(tmp_path, env, bindir)
        assert outputs["_exit"] == 1, f"{label}: {outputs}"

    @pytest.mark.parametrize(
        "label,payload", UNUSABLE_PAYLOADS, ids=[p[0] for p in UNUSABLE_PAYLOADS]
    )
    def test_the_summary_says_no_result_was_established(
        self, tmp_path: Path, label: str, payload: bytes | None
    ) -> None:
        # A `case` arm was added for this verdict specifically because a bash
        # `case` with no match renders *nothing* -- the failure mode that once
        # left COMPATIBLE_WITH_RISK with an empty summary. A verdict whose
        # whole job is to say "no result" must not render as silence.
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=payload)
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        summary = outputs["_summary"]
        assert "REPORT_UNREADABLE" in summary, f"{label}: {summary!r}"
        assert "no compatibility result" in summary.lower(), f"{label}: {summary!r}"
        assert "No binary ABI break detected" not in summary, f"{label}: {summary!r}"

    @pytest.mark.parametrize(
        "label,payload", UNUSABLE_PAYLOADS, ids=[p[0] for p in UNUSABLE_PAYLOADS]
    )
    def test_an_error_annotation_names_the_cause(
        self, tmp_path: Path, label: str, payload: bytes | None
    ) -> None:
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=payload)
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert "::error::" in outputs["_stdout"], f"{label}: {outputs['_stdout']!r}"
        assert "JSON report" in outputs["_stdout"], f"{label}: {outputs['_stdout']!r}"


class TestAReadableReportIsUnaffected:
    """The other half of the invariant: this must not fail working runs.

    Every assertion above is satisfiable by a script that fails
    unconditionally, which would break every real green check -- so the
    passing paths are pinned here too, including the two exit-0 tiers that
    are *not* plain COMPATIBLE.
    """

    def test_a_clean_report_still_publishes_compatible(self, tmp_path: Path) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=0,
            payload=json.dumps(
                {"report_schema_version": "4.4", "verdict": "COMPATIBLE"}
            ).encode(),
        )
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs["verdict"] == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_an_advisory_break_still_reports_the_break(self, tmp_path: Path) -> None:
        # The combination the review singled out: a real break the severity
        # policy demoted to exit 0. Both facts must survive -- the verdict says
        # BREAKING even though the step is not failed by this axis.
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=0,
            payload=json.dumps(
                {"report_schema_version": "4.4", "verdict": "BREAKING"}
            ).encode(),
        )
        env = _compare_env(tmp_path) | {"INPUT_FAIL_ON_BREAKING": "false"}
        outputs = _run_action(tmp_path, env, bindir)
        assert outputs["verdict"] == "BREAKING", outputs

    def test_a_risk_tier_is_still_reported(self, tmp_path: Path) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=0,
            payload=json.dumps(
                {"report_schema_version": "4.4", "verdict": "COMPATIBLE_WITH_RISK"}
            ).encode(),
        )
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs["verdict"] == "COMPATIBLE_WITH_RISK", outputs

    def test_a_legacy_report_without_a_schema_version_is_accepted(
        self, tmp_path: Path
    ) -> None:
        # An older abicheck's report predates both `report_schema_version`'s
        # current value and the assurance contribution. It is readable, so it
        # must still produce a verdict -- the fix is about unreadable reports,
        # not old ones.
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=0,
            payload=json.dumps({"verdict": "COMPATIBLE"}).encode(),
        )
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs["verdict"] == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs


class TestContradictoryAssuranceSchema:
    """A report claiming schema >= 2.40 while omitting the assurance axis.

    Not "cannot tell": the document's own version claim rules out the legacy
    explanation, so the axis is unreportable from a report that was required
    to report it. That is an invalid result, and
    ``_assurance_gated``'s deliberate fail-open would otherwise pass it.
    """

    def _contradictory(self, tmp_path: Path) -> dict:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=0,
            payload=json.dumps(
                {
                    "report_schema_version": "2.41",
                    "verdict": "COMPATIBLE",
                    "analysis_assurance": {"status": "complete"},
                }
            ).encode(),
        )
        return _run_action(tmp_path, _compare_env(tmp_path), bindir)

    def test_fails_the_step(self, tmp_path: Path) -> None:
        outputs = self._contradictory(tmp_path)
        assert outputs["_exit"] == 1, outputs
        assert "analysis_assurance_exit_contribution" in outputs["_stdout"], outputs[
            "_stdout"
        ]

    def test_the_published_verdict_is_not_compatible(self, tmp_path: Path) -> None:
        """The exit code is not the only thing a consumer reads.

        Regression for a real defect in this very change (Codex review, P2,
        reproduced): the contradiction was detected only at the FINAL_EXIT fold,
        which runs *after* the verdict output, the job summary and the PR comment
        are published. So the step failed while publishing
        `verdict=COMPATIBLE` and "No binary ABI break detected" -- a false clean
        result for any workflow that branches on the output or runs under
        `continue-on-error`, which is the exact failure this axis exists to
        prevent, reintroduced one layer out.

        `test_fails_the_step` above did not catch it because it asserted the exit
        code and the log text only. That is why this file's contract row is
        "verdict, gate and exit code checked independently" -- asserting one and
        assuming the others agree is how they came to disagree.
        """
        outputs = self._contradictory(tmp_path)
        assert outputs["verdict"] == "REPORT_UNREADABLE", outputs
        assert outputs["verdict"] != "COMPATIBLE", outputs

    def test_the_summary_does_not_claim_no_break(self, tmp_path: Path) -> None:
        outputs = self._contradictory(tmp_path)
        summary = outputs["_summary"]
        assert "No binary ABI break detected" not in summary, summary
        assert "REPORT_UNREADABLE" in summary, summary

    def test_a_pre_2_40_report_is_accepted(self, tmp_path: Path) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=0,
            payload=json.dumps(
                {
                    "report_schema_version": "2.39",
                    "verdict": "COMPATIBLE",
                    "analysis_assurance": {"status": "complete"},
                }
            ).encode(),
        )
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs["_exit"] == 0, outputs
        assert outputs["verdict"] == "COMPATIBLE", outputs

    def test_a_current_report_with_no_assurance_block_is_accepted(
        self, tmp_path: Path
    ) -> None:
        # The common case, and the one an earlier draft of this check failed:
        # `analysis_assurance` is attached only when the result carries a real
        # one, so a perfectly ordinary current-schema report has neither key
        # and must not be read as internally inconsistent.
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=0,
            payload=json.dumps(
                {"report_schema_version": "4.4", "verdict": "COMPATIBLE"}
            ).encode(),
        )
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs["_exit"] == 0, outputs
        assert outputs["verdict"] == "COMPATIBLE", outputs

    def test_a_current_report_carrying_the_axis_is_accepted(
        self, tmp_path: Path
    ) -> None:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=0,
            payload=json.dumps(
                {
                    "report_schema_version": "4.4",
                    "verdict": "COMPATIBLE",
                    "analysis_assurance": {"status": "complete"},
                    "analysis_assurance_exit_contribution": 0,
                }
            ).encode(),
        )
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs["_exit"] == 0, outputs
        assert outputs["verdict"] == "COMPATIBLE", outputs


class TestTheScopeOfTheCheckIsDeliberate:
    """The boundary of `_json_report_expected`, pinned as behavior.

    The check covers a JSON report the *caller* asked for (`format: json` plus
    `output-file`), not the internal `--write json=` sidecar `run.sh` injects
    for its own PR-comment/annotation rendering when the primary format is not
    json. Recording that boundary here keeps a future widening a deliberate
    decision rather than an accident -- and the widening is specifically not
    the right fix, since exit 0 is genuine evidence that abicheck's own gate
    passed (see `action/AGENTS.md`, "The residual").
    """

    def test_a_non_json_format_run_without_a_sidecar_still_reports_compatible(
        self, tmp_path: Path
    ) -> None:
        # The documented residual, asserted rather than described: at exit 0
        # with no sidecar, the tier is unverified but the run's acceptance is
        # not, so the step stays green. Closing this needs a new verdict value
        # ("accepted, tier unverified"), which is an output-contract change.
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=None)
        outputs = _run_action(
            tmp_path,
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
                "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
                "INPUT_FORMAT": "markdown",
            },
            bindir,
        )
        assert outputs["verdict"] == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_a_requested_json_report_is_covered(self, tmp_path: Path) -> None:
        # The same stub, the same missing report, differing only in whether the
        # caller asked for JSON -- so this pair isolates exactly what
        # `_json_report_expected` keys on, rather than each case proving it
        # separately against a different stub.
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=None)
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs["verdict"] == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs


class TestEveryCallerRequestedJsonModeIsCovered:
    """`format: json` is the request; where the report lands is a separate choice.

    The predicate keyed on `format: json` **plus** `output-file`, which left two
    modes the caller had explicitly asked JSON for uncovered -- the documented
    stdout mode, and a caller-supplied `extra-args --write json=PATH`. In both,
    an exit-0 run that produced nothing still published COMPATIBLE (Codex
    review, P2). The internal `--write json=` sidecar the Action injects for
    itself stays excluded; `TestTheScopeOfTheCheckIsDeliberate` pins that half.
    """

    def _env(self, tmp_path: Path, **extra: str) -> dict[str, str]:
        return {
            "INPUT_MODE": "compare",
            "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
            "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
            **extra,
        }

    def test_json_to_stdout_with_no_output_is_caught(self, tmp_path: Path) -> None:
        bindir = _stub_stdout_only(tmp_path, stdout="")
        outputs = _run_action(
            tmp_path, self._env(tmp_path, INPUT_FORMAT="json"), bindir
        )
        assert outputs["verdict"] == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs

    def test_json_to_stdout_carrying_a_real_report_still_passes(
        self, tmp_path: Path
    ) -> None:
        # The negative control for the case above: the same mode, a real report,
        # must still publish its verdict -- otherwise the fix breaks stdout mode
        # outright.
        bindir = _stub_stdout_only(
            tmp_path,
            stdout=json.dumps(
                {"report_schema_version": "4.4", "verdict": "COMPATIBLE"}
            ),
        )
        outputs = _run_action(
            tmp_path, self._env(tmp_path, INPUT_FORMAT="json"), bindir
        )
        assert outputs["verdict"] == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_json_to_stdout_that_is_resultless_is_caught(self, tmp_path: Path) -> None:
        bindir = _stub_stdout_only(tmp_path, stdout='{"error": "write interrupted"}')
        outputs = _run_action(
            tmp_path, self._env(tmp_path, INPUT_FORMAT="json"), bindir
        )
        assert outputs["verdict"] == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs

    def test_a_caller_supplied_write_json_path_is_caught(self, tmp_path: Path) -> None:
        # `format: markdown` plus the caller's own `--write json=` -- the JSON
        # request arrives through the passthrough, which makes it no less the
        # caller's. The stub honors neither, so nothing arrives.
        target = tmp_path / "caller.json"
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=None)
        outputs = _run_action(
            tmp_path,
            self._env(
                tmp_path,
                INPUT_FORMAT="markdown",
                INPUT_EXTRA_ARGS=f"--write json={target}",
            ),
            bindir,
        )
        assert outputs["verdict"] == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs


class TestAContradictoryReportAtANonZeroExit:
    """At a nonzero exit the compatibility verdict is readable — so it is kept.

    `_resolve_clean_exit_verdict` runs only at exit 0, so a self-contradictory
    report at exit 2 keeps the verdict the dispatch derived and the `FINAL_EXIT`
    check supplies the failure. Codex review raised this as the exit-0 finding
    generalized, and the code/doc mismatch it named was real — `action.yml`
    promised `REPORT_UNREADABLE` for this report shape without qualifying by
    exit path. I fixed that by scoping the documentation, not by overriding the
    verdict: at exit 2 the report's compatibility result IS established and
    readable, the two axes are orthogonal, and replacing a real break with "no
    result was established" would discard evidence — the exact opposite of what
    this value exists for.

    The decision is pinned here so a later round does not quietly reverse it:
    the step still fails and still explains itself, which is what the axis owes.
    """

    def _contradictory_at(
        self, tmp_path: Path, *, exit_code: int, verdict: str
    ) -> dict:
        bindir = _stub_abicheck(
            tmp_path,
            exit_code=exit_code,
            payload=json.dumps(
                {
                    "report_schema_version": "2.41",
                    "verdict": verdict,
                    "analysis_assurance": {"status": "complete"},
                }
            ).encode(),
        )
        return _run_action(tmp_path, _compare_env(tmp_path), bindir)

    @pytest.mark.parametrize("exit_code,verdict", ((2, "API_BREAK"), (4, "BREAKING")))
    def test_the_real_compatibility_verdict_survives(
        self, tmp_path: Path, exit_code: int, verdict: str
    ) -> None:
        outputs = self._contradictory_at(tmp_path, exit_code=exit_code, verdict=verdict)
        assert outputs["verdict"] == verdict, outputs
        assert outputs["verdict"] != "REPORT_UNREADABLE", outputs

    @pytest.mark.parametrize("exit_code,verdict", ((2, "API_BREAK"), (4, "BREAKING")))
    def test_the_step_still_fails_and_still_explains_itself(
        self, tmp_path: Path, exit_code: int, verdict: str
    ) -> None:
        # Keeping the verdict must not cost the gate or the diagnostic: those
        # are what the assurance axis owes regardless of which label is
        # published.
        outputs = self._contradictory_at(tmp_path, exit_code=exit_code, verdict=verdict)
        assert outputs["_exit"] == 1, outputs
        assert "analysis_assurance_exit_contribution" in outputs["_stdout"], outputs[
            "_stdout"
        ]

    def test_exit_zero_still_publishes_report_unreadable(self, tmp_path: Path) -> None:
        # The contrast that makes the scoping coherent: at exit 0 there is no
        # other readable result, so the fallthrough would publish COMPATIBLE and
        # the verdict override is required.
        outputs = self._contradictory_at(tmp_path, exit_code=0, verdict="COMPATIBLE")
        assert outputs["verdict"] == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs


class TestADryRunIsNotAMissingReport:
    """A dry run writes no report *by design* — that is not a failure.

    `compare --dry-run` performs no analysis and only previews the command, so
    `format: json` plus `dry-run: true` legitimately produces nothing. The
    requested-report validation added in this change turned that into
    `REPORT_UNREADABLE` for every two-sided preview — a regression this change
    introduced, because the pre-existing dry-run early return was gated on the
    audit-only shape (Codex review, P2).

    Widened to both shapes, and in the truthful direction: the two-sided case
    used to publish `COMPATIBLE`, which claimed a result no analysis produced.
    `DRY_RUN` is honest for both.
    """

    def _dry_run(self, tmp_path: Path, **extra: str) -> dict:
        # Writes nothing at all, exactly as a real `--dry-run` does.
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=None)
        return _run_action(tmp_path, _compare_env(tmp_path) | extra, bindir)

    def test_a_two_sided_dry_run_publishes_dry_run(self, tmp_path: Path) -> None:
        outputs = self._dry_run(tmp_path, INPUT_DRY_RUN="true")
        assert outputs["verdict"] == "DRY_RUN", outputs
        assert outputs["verdict"] != "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 0, outputs

    def test_a_two_sided_dry_run_does_not_publish_compatible(
        self, tmp_path: Path
    ) -> None:
        # The other half of the correction: a preview never established a
        # compatibility result, so COMPATIBLE was wrong before too.
        outputs = self._dry_run(tmp_path, INPUT_DRY_RUN="true")
        assert outputs["verdict"] != "COMPATIBLE", outputs

    def test_an_effective_dry_run_via_extra_args_is_also_exempt(
        self, tmp_path: Path
    ) -> None:
        # `extra-args --dry-run` leaves INPUT_DRY_RUN false, so the predicate
        # has to consult the passthrough as well — the same
        # nominal-versus-effective split `_effective_format` handles.
        outputs = self._dry_run(tmp_path, INPUT_EXTRA_ARGS="--dry-run")
        assert outputs["verdict"] == "DRY_RUN", outputs
        assert outputs["_exit"] == 0, outputs

    def test_a_real_run_that_writes_nothing_is_still_caught(
        self, tmp_path: Path
    ) -> None:
        # The control that keeps the exemption narrow: without the dry-run
        # flag, the identical stub (writing nothing) must still be reported.
        outputs = self._dry_run(tmp_path)
        assert outputs["verdict"] == "REPORT_UNREADABLE", outputs
        assert outputs["_exit"] == 1, outputs


#: Every operational outcome a report can name, with the verdict the Action
#: must publish for it at exit 0. None of them is a compatibility result.
OPERATIONAL_OUTCOMES = (
    ("ERROR", "ERROR"),
    ("unsupported", "ERROR"),
    ("failed", "ERROR"),
    ("UNKNOWN", "ERROR"),
    # The one with a more specific owner already: ADR-050 D2's refusal, which
    # exit 16 reports under this name and which carries its own summary arm.
    ("not_comparable", "NOT_COMPARABLE"),
)


class TestAnOperationalOutcomeIsNotACompatibilityResult:
    """A report that says "nothing was compared" must not publish COMPATIBLE.

    `_resolve_clean_exit_verdict` acts on the break and risk tiers and otherwise
    keeps its initial `COMPATIBLE`, so admitting the release operational
    sentinels into the reader's vocabulary — which is correct, they are real
    emitter values — let `{"verdict": "ERROR"}` at exit 0 publish a clean
    compatibility claim (Codex review, P2, reproduced as `ERROR COMPATIBLE 0`).

    The engine already draws this line for the same reason:
    `cli_compare_release_helpers._release_completed_compatibility_verdict`
    excludes exactly these from `run_outcome.compatibility`, because an
    operational failure and a compatibility result are separate axes.

    Labels reuse existing owners rather than adding a parallel vocabulary:
    `not_comparable` gets the established `NOT_COMPARABLE` (the same fact exit
    16 reports), and the rest take the same non-waivable `ERROR` path that the
    exit-4 operational arm already takes.
    """

    @pytest.mark.parametrize(
        "sentinel,expected",
        OPERATIONAL_OUTCOMES,
        ids=[o[0] for o in OPERATIONAL_OUTCOMES],
    )
    def test_the_legacy_verdict_sentinel_is_not_compatible(
        self, tmp_path: Path, sentinel: str, expected: str
    ) -> None:
        payload = json.dumps({"report_schema_version": "4.4", "verdict": sentinel})
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=payload.encode("utf-8"))
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs.get("verdict") != "COMPATIBLE", (sentinel, outputs)
        assert outputs.get("verdict") == expected, (sentinel, outputs)

    @pytest.mark.parametrize(
        "sentinel,expected",
        OPERATIONAL_OUTCOMES,
        ids=[o[0] for o in OPERATIONAL_OUTCOMES],
    )
    def test_the_step_fails(self, tmp_path: Path, sentinel: str, expected: str) -> None:
        payload = json.dumps({"report_schema_version": "4.4", "verdict": sentinel})
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=payload.encode("utf-8"))
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs["_exit"] != 0, (sentinel, outputs)

    @pytest.mark.parametrize(
        "operational,expected",
        (
            ("extraction_error", "ERROR"),
            ("no_comparison_completed", "ERROR"),
            ("budget_overflow", "ERROR"),
            ("not_comparable", "NOT_COMPARABLE"),
        ),
    )
    def test_the_canonical_run_outcome_axis_is_read_too(
        self, tmp_path: Path, operational: str, expected: str
    ) -> None:
        # ADR-063 D6's canonical name for the same fact. A modern report carries
        # it *and* a real compatibility verdict beside it (a release can have
        # one library break and another fail to extract), so the operational
        # axis has to win here or the failure laundered into a plain break.
        payload = json.dumps(
            {
                "report_schema_version": "4.4",
                "verdict": "ERROR",
                "run_outcome": {
                    "compatibility": "BREAKING",
                    "operational": operational,
                },
            }
        )
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=payload.encode("utf-8"))
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs.get("verdict") == expected, (operational, outputs)

    @pytest.mark.parametrize(
        "verdict",
        ("NO_CHANGE", "COMPATIBLE", "COMPATIBLE_WITH_RISK", "API_BREAK", "BREAKING"),
    )
    def test_a_real_compatibility_tier_is_unaffected(
        self, tmp_path: Path, verdict: str
    ) -> None:
        # The control. Every real tier must still resolve to itself (or to
        # COMPATIBLE for the two clean ones), or the operational branch has
        # swallowed the ordinary path.
        payload = json.dumps(
            {
                "report_schema_version": "4.4",
                "verdict": verdict,
                "run_outcome": {"compatibility": verdict, "operational": "none"},
            }
        )
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=payload.encode("utf-8"))
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs.get("verdict") not in ("ERROR", "NOT_COMPARABLE"), (
            verdict,
            outputs,
        )

    def test_operational_none_does_not_trigger_it(self, tmp_path: Path) -> None:
        # `none` is the ordinary value on every healthy modern report; treating
        # it as an operational failure would fail every run.
        payload = json.dumps(
            {
                "report_schema_version": "4.4",
                "verdict": "COMPATIBLE",
                "run_outcome": {"compatibility": "COMPATIBLE", "operational": "none"},
            }
        )
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=payload.encode("utf-8"))
        outputs = _run_action(tmp_path, _compare_env(tmp_path), bindir)
        assert outputs.get("verdict") == "COMPATIBLE", outputs
        assert outputs["_exit"] == 0, outputs
