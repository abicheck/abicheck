# Copyright 2026 Nikolay Petrov
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

"""Report-envelope construction for ``actions/check-target`` (G30 P1.3,
ADR-047 §7).

``check-target`` composes ``resolve-baseline`` + root ``action.yml`` +
``collect-facts`` (ADR-047 §4) and always emits the report envelope (§7),
regardless of whether the baseline resolved, was a bootstrap "no baseline
yet" pass, or failed outright. This module is the pure logic backing
``actions/check-target/report_envelope.py``'s thin CLI wrapper (mirroring
how ``abicheck.buildsource.baseline_set`` backs
``actions/resolve-baseline/resolve_baseline.py``):

- :func:`build_check_id` — the unconditional
  ``target@profile#baseline_channel@requested_depth`` identity (§7's
  "always includes ``requested_depth``, not only on collision" correction).
- :func:`resolve_effective_depth` — the ``check_evidence_coverage``
  degrade-to-``headers`` calculation when the requested build/source
  evidence wasn't actually available.
- :func:`augment_report` — the common path: layer §7's identity/new fields
  onto an already-produced ``compare``/``scan`` JSON report, dual-writing
  the legacy ``verdict``/``severity`` fields ``abicheck/aggregate.py``
  already parses (§7's dual-write requirement) and neutralizing the legacy
  gate for ``gate-mode: advisory`` (§7's third required sub-task) — but
  *not* for ``deferred``, whose whole point is that ``aggregate``'s own
  ``exit_code()`` (a ``max()`` over each report's real ``severity.exit_code``)
  is what computes the gate centrally; neutralizing ``deferred`` reports too
  would make that computation blind to the real finding.
- :func:`build_operational_error_report` / :func:`build_bootstrap_report` —
  synthesize a full envelope from scratch when ``resolve-baseline`` failed
  or bootstrapped, so a report always exists even when no comparison ever
  ran (§7: "a report can be fully computed and still fail to publish" — the
  converse also matters here: a check can fail to ever start comparing and
  must still produce a typed, consumable report).
- :func:`final_exit_code` — ``check-target``'s own composite exit code:
  ``gate-mode: local`` reflects the real outcome; ``deferred``/``advisory``
  are 0 unless an operational error occurred (operational errors always
  fail the job, regardless of gate-mode — resolve-baseline's failure
  taxonomy is never silently degraded to a compatibility verdict).

Pure: no file I/O, no subprocess. The CLI wrapper handles reading/writing
JSON and printing ``GITHUB_OUTPUT`` lines.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..checker_types import validate_check_id, validate_evidence_depth
from ..evidence_depth import DEPTH_RANK, weaker_depth
from ..policy.outcome import OperationalStatus, PolicyGateDecision, TargetLifecycle
from ..schemas import REPORT_SCHEMA_VERSION, SCAN_SCHEMA_VERSION
from .baseline_set import ALL_OUTCOMES, ResolveOutcome
from .check_report_exit_backfill import backfill_exit_block_fields
from .check_report_run_outcome import backfill_run_outcome, synthetic_run_outcome

#: Safe identifier charset shared by every ``check_id`` component (ADR-047
#: §7's delimiter-unambiguity fix) -- target/bundle names, profile ids, and
#: baseline channel names all validate against this.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: The five real ``Verdict`` enum values (``abicheck.change_registry_types.
#: Verdict``), duplicated here as plain strings rather than importing the
#: enum -- this module only ever compares/serializes the wire string, never
#: constructs a ``Verdict`` instance, and a bare frozenset avoids a second
#: import path into ``checker_policy``'s dependency graph for a five-item
#: membership check.
LEGACY_VERDICT_VALUES = frozenset(
    {"NO_CHANGE", "COMPATIBLE", "COMPATIBLE_WITH_RISK", "API_BREAK", "BREAKING"}
)

#: ``aggregate.py``'s own operational-failure sentinel (``_load_report_file``
#: special-cases this exact string before ever parsing ``severity``) --
#: reused here, not reinvented, so ``check-target``'s operational-failure
#: reports are recognized by the *existing* aggregate parser unchanged.
OPERATIONAL_ERROR_VERDICT = "ERROR"

#: A bootstrap ("no baseline published yet") pass is deliberately **not** a
#: ``Verdict`` member and **not** ``"ERROR"`` either -- ADR-047 §6 requires
#: it stay "an advisory pass ... never a compatibility verdict." Any string
#: outside ``LEGACY_VERDICT_VALUES``/``OPERATIONAL_ERROR_VERDICT`` already
#: fails ``aggregate.parse_report_verdict``'s ``Verdict(raw)`` parse (caught,
#: returns ``None``), which is exactly the "unavailable, not a verdict"
#: behavior a bootstrap check wants -- it is expected to be paired with
#: ``required: false`` in the run-plan, so not contributing a parsed verdict
#: never opens a coverage gap.
BOOTSTRAP_VERDICT = "NO_BASELINE"

#: A ``new_target`` resolution (a target genuinely absent from an otherwise-
#: resolved baseline-set, on a check that opted into ``allow_new_target``) is
#: the same shape of "advisory pass, never a compatibility verdict" as
#: :data:`BOOTSTRAP_VERDICT` above, and is kept as its own distinct sentinel
#: rather than reusing that one: ``NO_BASELINE`` means "no baseline-set could
#: be resolved at all," while a ``new_target`` check DID resolve a real,
#: healthy baseline-set -- it just doesn't (yet) cover this particular
#: target, a materially different fact a report reader should not have to
#: infer from ``check_evidence_coverage.reasons`` alone. Same
#: ``LEGACY_VERDICT_VALUES``/``OPERATIONAL_ERROR_VERDICT``-exclusion
#: reasoning, and the same pairing convention, apply as for
#: :data:`BOOTSTRAP_VERDICT`: this parses to ``None`` under
#: ``aggregate.parse_report_verdict``, so a ``new_target`` check is
#: ``TargetReport.analyzed is False`` the same way a bootstrap one is --
#: expected to be paired with ``required: false`` in the run-plan, or a
#: required-coverage gate would otherwise block every release that
#: introduces a genuinely new target. See
#: ``abicheck.buildsource.baseline_set.ResolveOutcome.NEW_TARGET``'s own
#: docstring for the full lifecycle-state rationale.
NEW_TARGET_VERDICT = "NEW_TARGET"

#: ``resolve-baseline``'s failure outcomes (ADR-047 §6, plus
#: ``wrong_project_ref``) that are never a compatibility verdict -- distinct
#: from ``not_found`` + bootstrap, which is an advisory pass, not a failure.
#: Derived from :data:`abicheck.buildsource.baseline_set.ALL_OUTCOMES`
#: (the canonical outcome registry) rather than hand-duplicated, so a new
#: outcome added there can never silently fall out of sync with what
#: ``check-target`` recognizes as an operational failure here (Codex
#: review -- ``wrong_project_ref`` itself was missing from this set,
#: which made a real ``check-target`` propagation of that outcome fall
#: through to a usage error instead of a structured ``ERROR`` report).
RESOLVE_FAILURE_OUTCOMES = ALL_OUTCOMES - {ResolveOutcome.RESOLVED}

GATE_MODES = ("local", "deferred", "advisory")

#: ``cli_compare_release_helpers._exit_compare_release``'s dedicated
#: ``--fail-on-removed-library`` exit code -- applied "in preference to the
#: severity code," so it's the one value the analysis step's real exit code
#: can carry that the persisted ``severity`` block never independently
#: reflects.
_REMOVED_LIBRARY_EXIT_CODE = 8


def validate_identifier(field_name: str, value: str) -> None:
    """Reject a ``target``/``profile``/``baseline_channel`` outside the safe
    identifier charset (ADR-047 §7's delimiter-unambiguity fix)."""
    if not _IDENTIFIER_RE.match(value):
        raise ValueError(
            f"{field_name}: {value!r} is not a valid identifier -- must match "
            r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
        )


def build_check_id(
    name: str, profile_id: str, baseline_channel: str, requested_depth: str
) -> str:
    """Build the unconditional ``target@profile#baseline_channel@depth`` id.

    Always includes the ``@requested_depth`` suffix -- ADR-047 §7's
    corrected rule, not only when a collision would occur (no run-plan-level
    collision detection is available to a standalone ``check-target`` call).
    """
    validate_identifier("target/bundle", name)
    validate_identifier("profile_id", profile_id)
    validate_identifier("baseline_channel", baseline_channel)
    validate_evidence_depth("requested_depth", requested_depth)
    check_id = f"{name}@{profile_id}#{baseline_channel}@{requested_depth}"
    validate_check_id(check_id)
    return check_id


#: Ladder order (shallow -> deep). Owned by ``evidence_depth.DEPTH_RANK``,
#: which derives it from ``scan_levels.USER_DEPTHS`` -- this used to be a
#: fourth independent copy of the same four rungs (ADR-061 Phase 3).
_DEPTH_RANK = DEPTH_RANK


def derive_effective_depth(
    report: dict[str, Any], requested_depth: str
) -> tuple[str, dict[str, Any]]:
    """Compute ``effective_depth``/``check_evidence_coverage`` (ADR-047 §7).

    Reads the depth the underlying ``compare``/``scan`` run *actually*
    achieved straight from its own JSON output -- ``old_evidence_depth``/
    ``new_evidence_depth`` (``compare``, always present for ``--format
    json`` via ``cli_compare_helpers._fold_evidence_depth_into_json``) or
    ``level.depth`` (``scan``, ``ScanOutcome.to_dict``) -- rather than
    inferring it from which collect-facts producer step ran. This is the
    authoritative signal: it's correct for every way a caller can supply
    build/source evidence (a composed ``collect-facts`` producer, or a
    direct out-of-band ``build-info``/``sources`` input with no producer at
    all -- a case an earlier, producer-based heuristic here got wrong,
    reporting a real build/source-depth result as "degraded" purely because
    no ``collect-facts`` step ran, flagged by review). For ``compare``, the
    shallower of the two sides is the check's own achieved depth (a
    build/source result on only one side isn't a build/source-depth
    *comparison*). Reports deeper than requested (e.g. real headers given
    for a ``binary``-depth request) are reported honestly as achieved, not
    capped down to the request.
    """
    validate_evidence_depth("requested_depth", requested_depth)
    old_d = report.get("old_evidence_depth")
    new_d = report.get("new_evidence_depth")
    achieved: str | None = None
    source = ""
    if (
        isinstance(old_d, str)
        and isinstance(new_d, str)
        and old_d in _DEPTH_RANK
        and new_d in _DEPTH_RANK
    ):
        achieved = weaker_depth(old_d, new_d)
        source = "compare"
    else:
        level = report.get("level")
        scan_depth = level.get("depth") if isinstance(level, dict) else None
        if isinstance(scan_depth, str) and scan_depth in _DEPTH_RANK:
            achieved = scan_depth
            source = "scan"
    if achieved is None:
        # Neither signal is present -- shouldn't happen for real compare/scan
        # --format json output, but trust the request rather than silently
        # guessing "complete" for whatever this report actually is.
        return requested_depth, {
            "state": "unknown",
            "reasons": ["no_depth_signal_in_report"],
        }
    if _DEPTH_RANK[achieved] >= _DEPTH_RANK[requested_depth]:
        return achieved, {"state": "complete", "reasons": []}
    return achieved, {
        "state": "degraded",
        "reasons": [f"{source}_achieved_{achieved}"],
    }


def _real_exit_code(report: dict[str, Any]) -> int:
    """Read whichever real gate exit code the underlying report carries.

    ``compare``-shaped reports carry a ``severity`` block; ``scan``-shaped
    reports carry a top-level ``exit_code`` alongside ``scan_schema_version``.
    Returns 0 (pass) when neither shape is present.
    """
    severity = report.get("severity")
    if isinstance(severity, dict):
        exit_code = severity.get("exit_code")
        if isinstance(exit_code, int):
            return int(exit_code)
    if "scan_schema_version" in report:
        exit_code = report.get("exit_code")
        if isinstance(exit_code, int):
            return int(exit_code)
    return 0


def _neutralize_gate(report: dict[str, Any]) -> None:
    """Zero the legacy gate in place for ``gate-mode: advisory`` (§7).

    Only ``advisory`` reports are rewritten this way -- ``deferred`` reports
    keep their real ``severity``/``exit_code`` untouched, since
    ``check-project.yml``'s trailing ``aggregate`` job computes the actual
    gate from exactly that real value (``abicheck/aggregate.py``'s
    ``exit_code()`` is a ``max()`` over each report's real gate).
    """
    severity = report.get("severity")
    if isinstance(severity, dict):
        report["severity"] = {
            **severity,
            "exit_code": 0,
            "blocking": False,
            "blocking_categories": [],
        }
    elif "scan_schema_version" in report and "exit_code" in report:
        report["exit_code"] = 0
    # ADR-063 Phase 7: zero `run_outcome.gate` too, never `.operational` (see `final_exit_code`'s invariant).
    run_outcome = report.get("run_outcome")
    if isinstance(run_outcome, dict):
        report["run_outcome"] = {**run_outcome, "gate": PolicyGateDecision.NONE.value}
    # A severity-scheme `scan --against` (scan schema 1.9+) publishes a real
    # gate at `diff.severity`, and `aggregate.GateInfo.from_scan_report`
    # *prefers* it over the top-level `exit_code` zeroed just above -- so
    # zeroing only that left an explicitly advisory check blocking the
    # trailing aggregate anyway (Codex review). Same shape, same remedy, and
    # deliberately the same shared-path discipline as the coverage axis
    # below: the traversal is imported, never re-derived here, because a
    # local copy is precisely what let the scan-shaped block slip through
    # once already.
    _zero_nested_severity_gates(report)
    # ADR-049 Phase 7's contract-coverage axis is a *second* way this report
    # can raise an exit code, orthogonal to the compatibility gate above and
    # folded separately by `aggregate` -- so zeroing only the gate left an
    # advisory report still driving the trailing aggregate to exit 1 (Codex
    # review, reproduced end to end). "Advisory" means this check gates
    # nothing; that has to hold on every axis it can contribute to, not just
    # the one that existed when this function was written.
    #
    # Only the *contribution* is zeroed. `contract_coverage_failures` stays
    # exactly as the run recorded it: the ledger is deliberately
    # unsuppressible, and advisory mode is about not gating, not about
    # hiding what was found -- the same split `contract.unresolved=warn`
    # already makes.
    # Every block the aggregate *reads* it from, not just the document root:
    # a `scan --against` report carries these fields under `diff` (and under
    # `report.diff` for a service envelope), so zeroing only the root left an
    # advisory scan's nested contribution intact and the trailing aggregate
    # folded it back into the CI exit anyway (Codex review, reproduced).
    #
    # The path set is imported rather than re-derived. A local copy is what
    # produced that bug: it agreed with the reader for the compare shape and
    # silently disagreed for the scan one. Imported inside the function to
    # keep this module's import graph a leaf (`run_plan` already reaches into
    # `..aggregate` the same way); `aggregate` never imports back.
    #
    # Each container along a path is *rebound to a copy* before anything is
    # written. `augment_report` copies only the top level, so writing through
    # a nested mapping in place reached back into the caller's own report and
    # silently rewrote its authoritative contribution -- breaking this
    # module's documented "*report* itself is never mutated" contract (Codex
    # review). Copying per path is cheap and, unlike a blanket deepcopy,
    # leaves the report's large `changes` payload untouched.
    from ..workflows.aggregate import contract_coverage_block_paths

    for path in contract_coverage_block_paths(report):
        node: dict[str, Any] = report
        for key in path:
            # Copied into a real `dict` whatever Mapping flavour the block
            # was, then rebound. Skipping a non-`dict` Mapping instead was
            # wrong: the aggregate reads *any* Mapping, so a
            # `MappingProxyType` block kept its contribution and an advisory
            # check still gated CI (CodeRabbit review, reproduced). The copy
            # is also what makes this safe -- an immutable mapping cannot be
            # written through, and the caller's own nested container must not
            # be touched either way.
            #
            # No type guard here: `contract_coverage_block_paths` only emits
            # a path whose every node it has already checked is a `Mapping`,
            # so a guard would be unreachable by construction -- and an
            # unreachable branch is a worse guarantee than the single
            # definition that actually enforces it.
            copied = dict(node[key])
            node[key] = copied
            node = copied
        if _is_valid_coverage_contribution(
            node.get("contract_coverage_exit_contribution")
        ):
            node["contract_coverage_exit_contribution"] = 0
        # P0.4's orthogonal analysis-assurance contribution is the exact
        # sibling of the contract-coverage one just above -- a second,
        # independent way this report can raise an exit code, so "advisory
        # means this check gates nothing" has to hold on this axis too, not
        # just the coverage one. Same block, same validity check (both are
        # plain 0/1 floors), same fail-open reasoning.
        if _is_valid_coverage_contribution(
            node.get("analysis_assurance_exit_contribution")
        ):
            node["analysis_assurance_exit_contribution"] = 0
        # CLI cleanup phase two, PR G1/PR E: the canonical `exit` block
        # (`exit_decision.ExitDecision`) lives at exactly the same
        # locations as the two contributions above -- the document root for
        # a `compare` report, `diff`/`report.diff` for a scan one -- so the
        # same `contract_coverage_block_paths` traversal already finds it;
        # no separate path-finder is needed (Codex review, fresh evidence:
        # an earlier revision neutralized every axis this block summarizes
        # except the block itself, so an advisory report could still
        # publish a nonzero `exit.code` -- and nonzero contributions -- that
        # a consumer adopting the new canonical field would read as
        # blocking). Replaced wholesale with the "clean" decision, the same
        # way the `severity` gate above is replaced rather than
        # conditionally rewritten: advisory mode means every *deferrable*
        # axis gates nothing, so the persisted explanation has to say so
        # outright rather than being left to disagree with the axes it
        # summarizes. The four "comparison never completed" axes --
        # operational_error/evidence_contract_error/budget_overflow/
        # not_comparable_contribution (`_classify_verdict` treats those
        # verdicts identically to an operational error; mutually exclusive
        # per `resolve_scan_exit_decision`'s own docstring, so at most one
        # is ever nonzero) -- are carried over, not zeroed with the rest
        # (Codex review, fresh evidence, two rounds: round one only kept
        # `operational_error_contribution`, leaving the same "exit.code: 0
        # but the job still fails" gap for `NOT_COMPARABLE`). Every one of
        # these fails every gate mode per `final_exit_code`, so zeroing any
        # would make `exit.code` claim a clean pass the job doesn't give.
        old_exit = node.get("exit")
        if isinstance(old_exit, Mapping):
            from ..exit_decision import resolve_exit_decision

            def _int_or_zero(key: str) -> int:
                value = old_exit.get(key, 0)
                return value if isinstance(value, int) else 0

            node["exit"] = resolve_exit_decision(
                compatibility_contribution=0,
                operational_error_contribution=_int_or_zero("operational_error_contribution"),
                evidence_contract_error_contribution=_int_or_zero("evidence_contract_error_contribution"),
                budget_overflow_contribution=_int_or_zero("budget_overflow_contribution"),
                not_comparable_contribution=_int_or_zero("not_comparable_contribution"),
            ).to_dict()


def _zero_nested_severity_gates(report: dict[str, Any]) -> None:
    """Zero a scan report's nested ``diff.severity`` gate for advisory mode.

    The compatibility-axis counterpart of the coverage loop in
    :func:`_neutralize_gate`, and written the same way for the same two
    reasons that loop documents:

    * the path set is **imported** from ``aggregate``
      (:func:`~abicheck.aggregate.scan_severity_gate_paths`) rather than
      re-derived, so the writer here and the reader there cannot disagree
      about where the block lives; and
    * each container along a path is **rebound to a copy** before anything is
      written, because ``augment_report`` copies only the top level -- writing
      through a nested mapping in place would reach back into the caller's own
      report and rewrite its authoritative gate, breaking this module's
      "*report* itself is never mutated" contract.

    Unlike the coverage contribution, the gate's fields are *replaced* rather
    than conditionally rewritten: an advisory check gates nothing, so a
    published gate must say so outright, exactly as the root ``severity``
    branch above already does for a ``compare`` report.
    """
    from ..workflows.aggregate import scan_severity_gate_paths

    for path in scan_severity_gate_paths(report):
        node: dict[str, Any] = report
        for key in path:
            copied = dict(node[key])
            node[key] = copied
            node = copied
        gate = node.get("severity")
        if isinstance(gate, Mapping):
            node["severity"] = {
                **gate,
                "exit_code": 0,
                "blocking": False,
                "blocking_categories": [],
            }


def _is_valid_coverage_contribution(raw: object) -> bool:
    """Whether *raw* is a contribution this function should rewrite.

    Mirrors ``aggregate._is_valid_contribution``: a ``bool`` is excluded
    before the ``int`` check, since ``True`` is an ``int`` in Python. An
    absent or unusable value is left untouched rather than replaced with a
    ``0`` the run never stated -- the aggregate already reads it as "says
    nothing", and inventing a value here would make it look like an
    advisory run had answered the question.
    """
    return not isinstance(raw, bool) and isinstance(raw, int) and raw in (0, 1)


def _stamp_schema_version(out: dict[str, Any], report: dict[str, Any]) -> None:
    """Stamp the schema marker matching *report*'s actual shape.

    A scan report (baseline-channel: none) has its own schema marker and shape
    (level/risk/coverage/... -- no library/old_file/summary/changes/...) -- bump
    it to the latest version for this envelope's new additive fields instead of
    also stamping ``report_schema_version`` (the *compare*-report schema's
    marker), which would make a downstream validator select
    ``compare_report.schema.json`` for a report that structurally can never
    satisfy it (Codex review).

    A ``kind: bundle`` / directory-package compare report (the per-library
    release fan-out's own summary shape: verdict/old_dir/new_dir/libraries/...)
    has never had a schema of its own; it is left unversioned rather than
    falsely claiming the single-pair compare schema (same rationale). ADR-047
    §7's identity/policy-gate fields still apply regardless of report shape.
    """
    if "scan_schema_version" in report:
        out["scan_schema_version"] = SCAN_SCHEMA_VERSION
    elif not ("libraries" in report and "old_dir" in report):
        out["report_schema_version"] = REPORT_SCHEMA_VERSION


def _escalate_removed_library_severity(out: dict[str, Any]) -> None:
    """Fold ``--fail-on-removed-library``'s exit 8 into the severity block.

    Exit 8 is the *only* value ``cli_compare_release_helpers.
    _exit_compare_release`` applies "in preference to the severity code" --
    every other severity-aware exit path there emits ``severity_exit_code``
    directly, so 8 is the sole case where the real outcome can diverge from
    what is already persisted. Escalating ``policy_gate_decision``/
    ``real_exit_code`` is not enough on its own: ``gate-mode: deferred`` relies
    on ``check-project.yml``'s trailing aggregate job, and
    ``abicheck.aggregate.GateInfo.from_report_data`` reads ONLY the persisted
    ``severity.exit_code`` -- it cannot see ``policy_gate_decision`` or
    ``analysis_exit_code`` at all. Without also updating severity here, a
    removed-library gate on a deferred bundle check would still be silently
    missed by aggregate even though the check's own local exit code is correct
    (Codex review, second pass).

    A whole library disappearing is unambiguously an ABI break, so it is
    encoded as the ``abi_breaking`` tier (exit_code 4 -- the ceiling of
    ``aggregate.py``'s ``_VALID_GATE_EXIT = {0, 1, 2, 4}``; 8 itself is not a
    legal ``severity.exit_code`` and would raise ``_MalformedGate`` there).
    Only escalates -- never downgrades an already->=4 severity block, though 4
    is already that ceiling so there is nothing to downgrade from.
    """
    severity = out.get("severity")
    if not isinstance(severity, dict) or severity.get("exit_code", 0) >= 4:
        return
    cats = list(severity.get("blocking_categories") or [])
    if "abi_breaking" not in cats:
        cats.append("abi_breaking")
    out["severity"] = {
        **severity,
        "exit_code": 4,
        "blocking": True,
        "blocking_categories": cats,
    }
    # ADR-063 Phase 7: fold the identical escalation into `run_outcome.gate` (no-op if absent).
    run_outcome = out.get("run_outcome")
    if isinstance(run_outcome, dict):
        out["run_outcome"] = {**run_outcome, "gate": PolicyGateDecision.ABI_BREAKING.value}


def _classify_verdict(
    out: dict[str, Any], report: dict[str, Any], raw_verdict: Any
) -> None:
    """Split *raw_verdict* into a compatibility verdict or an operational error.

    Any verdict string a scan run can produce that is neither a legacy
    compatibility verdict nor the explicit operational-error sentinel (e.g.
    ``"BUDGET_OVERFLOW"``/``"EVIDENCE_CONTRACT_ERROR"`` -- ``service_scan.py``'s
    guard sentinels) is not a compatibility finding either: it is the scan
    never completing its comparison at all, the same class of problem as an
    analysis CLI error, not something ADR-047 §7's "deferred only defers the
    *compatibility* verdict" rule was meant to cover. Treated as operational so
    ``gate-mode: deferred``/``advisory`` cannot turn a guard failure into a
    quiet pass (Codex review).
    """
    if raw_verdict in LEGACY_VERDICT_VALUES:
        out["compatibility_verdict"] = raw_verdict
        out.setdefault("operational_errors", [])
        return
    if raw_verdict == OPERATIONAL_ERROR_VERDICT:
        out["operational_errors"] = [
            {
                "kind": "analysis_error",
                "message": str(report.get("error") or "the analysis step failed"),
            }
        ]
        return
    out["operational_errors"] = [
        {
            "kind": "scan_guard_triggered",
            "message": str(
                report.get("error")
                or f"the analysis reported a non-compatibility verdict: {raw_verdict!r}"
            ),
        }
    ]


def augment_report(
    report: dict[str, Any],
    *,
    name: str, profile_id: str, baseline_channel: str, requested_depth: str, gate_mode: str,
    project: str | None = None, head_sha: str | None = None, base_ref: str | None = None,
    action_version: str | None = None, analysis_exit_code: int | None = None,
) -> dict[str, Any]:
    """Layer ADR-047 §7's identity/new fields onto a real analysis report.

    *report* is the already-parsed JSON dict a ``compare``/``scan`` run
    produced (root ``action.yml``'s ``report-path`` output). Returns a new
    dict -- *report* itself is never mutated.

    *analysis_exit_code*, when given, is the nested root Action's own real
    process exit code (its ``exit-code`` output) -- folded into the gate
    decision via ``max()`` alongside whatever ``_real_exit_code`` reads from
    the report body itself. Needed because at least one root-Action gate,
    ``--fail-on-removed-library`` (release/bundle compares), takes effect as
    a dedicated exit code (8) that overrides the persisted severity scheme
    rather than feeding into it -- ``compare_release_cmd``'s own
    ``_exit_compare_release`` applies it "in preference to the severity
    code," so a bundle report's own ``severity.exit_code`` can read 0 even
    though the real process exited 8. Reading only the report body would
    silently pass a removed-library gate the caller explicitly asked for
    (Codex review).
    """
    if gate_mode not in GATE_MODES:
        raise ValueError(f"gate_mode must be one of {GATE_MODES}, got {gate_mode!r}")
    out = dict(report)
    check_id = build_check_id(name, profile_id, baseline_channel, requested_depth)
    effective_depth, coverage = derive_effective_depth(report, requested_depth)
    backfill_run_outcome(out)
    backfill_exit_block_fields(out)
    _stamp_schema_version(out, report)
    out["check_id"] = check_id
    out["target_id"] = check_id
    out["profile_id"] = profile_id
    out["baseline_channel"] = baseline_channel
    out["requested_depth"] = requested_depth
    out["effective_depth"] = effective_depth
    out["check_evidence_coverage"] = coverage
    if project is not None:
        out["project"] = project
    if head_sha is not None:
        out["head_sha"] = head_sha
    if base_ref is not None:
        out["base_ref"] = base_ref
    if action_version is not None:
        out["action_version"] = action_version

    raw_verdict = report.get("verdict")
    real_exit_code = max(_real_exit_code(report), analysis_exit_code or 0)
    out["policy_gate_decision"] = "fail" if real_exit_code != 0 else "pass"
    if analysis_exit_code == _REMOVED_LIBRARY_EXIT_CODE:
        _escalate_removed_library_severity(out)
    _classify_verdict(out, report, raw_verdict)
    # check-target's own nested analysis step always disables add-job-summary/
    # pr-comment/upload-sarif (action.yml's "Run analysis" step), and the
    # finalize step itself only writes the report JSON to disk + sets
    # GITHUB_OUTPUT values -- neither is a "publication" in ADR-047 §7's
    # sense (surfaced to a human/dashboard via a real channel). Defaulting
    # to state: "published"/channels: ["job_summary"] here was simply false
    # for every real check-target run and could make a downstream consumer
    # believe a report had actually been surfaced when it hadn't (Codex
    # review). Nothing today computes a real publication state for this
    # path, so the honest default is "nothing was published."
    out.setdefault("publication", {"state": "skipped", "channels": []})

    if gate_mode == "advisory":
        _neutralize_gate(out)
    return out


def build_operational_error_report(
    *,
    name: str,
    profile_id: str,
    baseline_channel: str,
    requested_depth: str,
    resolve_outcome: str,
    resolve_message: str,
    project: str | None = None,
    head_sha: str | None = None,
    base_ref: str | None = None,
    tool_version: str | None = None,
    action_version: str | None = None,
) -> dict[str, Any]:
    """Synthesize a full report envelope for a ``resolve-baseline`` failure.

    ``verdict: "ERROR"`` matches ``abicheck/aggregate.py:_load_report_file``'s
    existing special case (checked *before* it ever reads a ``severity``
    block), so no ``severity`` block is written here at all -- omitting it
    is the ADR-047 §7-documented choice, not an oversight.
    """
    check_id = build_check_id(name, profile_id, baseline_channel, requested_depth)
    report: dict[str, Any] = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "check_id": check_id,
        "target_id": check_id,
        "target": name,
        "profile_id": profile_id,
        "baseline_channel": baseline_channel,
        "requested_depth": requested_depth,
        "check_evidence_coverage": {"state": "unknown", "reasons": [resolve_outcome]},
        # compatibility_verdict is omitted, not written as null: the schema
        # declares it a plain string enum with no null alternative -- an
        # operational failure has no compatibility result to report at all
        # (§7: "ERROR" is the deliberate exception living in the legacy
        # `verdict` field instead, never in this new one).
        "policy_gate_decision": "fail",
        "operational_errors": [{"kind": resolve_outcome, "message": resolve_message}],
        "publication": {"state": "skipped", "channels": []},
        "verdict": OPERATIONAL_ERROR_VERDICT,
        "run_outcome": synthetic_run_outcome(operational=OperationalStatus.EXTRACTION_ERROR),
    }
    if project is not None:
        report["project"] = project
    if head_sha is not None:
        report["head_sha"] = head_sha
    if base_ref is not None:
        report["base_ref"] = base_ref
    if tool_version is not None:
        report["tool_version"] = tool_version
    if action_version is not None:
        report["action_version"] = action_version
    return report


def build_bootstrap_report(
    *,
    name: str,
    profile_id: str,
    baseline_channel: str,
    requested_depth: str,
    resolve_message: str,
    project: str | None = None,
    head_sha: str | None = None,
    base_ref: str | None = None,
    tool_version: str | None = None,
    action_version: str | None = None,
) -> dict[str, Any]:
    """Synthesize the "no baseline published yet" advisory pass (§6)."""
    check_id = build_check_id(name, profile_id, baseline_channel, requested_depth)
    report: dict[str, Any] = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "check_id": check_id,
        "target_id": check_id,
        "target": name,
        "profile_id": profile_id,
        "baseline_channel": baseline_channel,
        "requested_depth": requested_depth,
        "check_evidence_coverage": {
            "state": "bootstrap",
            "reasons": ["no_baseline_published_yet"],
        },
        "baseline_bootstrap": True,
        # compatibility_verdict omitted, not null -- same reasoning as
        # build_operational_error_report above: a bootstrap pass never
        # produced a compatibility result either.
        "policy_gate_decision": "pass",
        "operational_errors": [],
        "publication": {"state": "skipped", "channels": []},
        "verdict": BOOTSTRAP_VERDICT,
        "message": resolve_message,
        "run_outcome": synthetic_run_outcome(lifecycle=TargetLifecycle.BOOTSTRAP),
    }
    if project is not None:
        report["project"] = project
    if head_sha is not None:
        report["head_sha"] = head_sha
    if base_ref is not None:
        report["base_ref"] = base_ref
    if tool_version is not None:
        report["tool_version"] = tool_version
    if action_version is not None:
        report["action_version"] = action_version
    return report


def build_new_target_report(
    *,
    name: str,
    profile_id: str,
    baseline_channel: str,
    requested_depth: str,
    resolve_message: str,
    project: str | None = None,
    head_sha: str | None = None,
    base_ref: str | None = None,
    tool_version: str | None = None,
    action_version: str | None = None,
) -> dict[str, Any]:
    """Synthesize the "target new to this baseline-set" advisory pass.

    Mirrors :func:`build_bootstrap_report` structurally -- see
    :data:`NEW_TARGET_VERDICT`'s own docstring for why this is a distinct
    lifecycle state rather than reusing ``baseline_bootstrap``/
    ``BOOTSTRAP_VERDICT``: the baseline-set itself resolved cleanly here, it
    simply carries no artifact for this particular target yet.
    """
    check_id = build_check_id(name, profile_id, baseline_channel, requested_depth)
    report: dict[str, Any] = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "check_id": check_id,
        "target_id": check_id,
        "target": name,
        "profile_id": profile_id,
        "baseline_channel": baseline_channel,
        "requested_depth": requested_depth,
        "check_evidence_coverage": {
            "state": "new_target",
            "reasons": ["target_not_in_baseline_set"],
        },
        "baseline_new_target": True,
        # compatibility_verdict omitted, not null -- same reasoning as
        # build_bootstrap_report above: this check never produced a
        # compatibility result to report.
        "policy_gate_decision": "pass",
        "operational_errors": [],
        "publication": {"state": "skipped", "channels": []},
        "verdict": NEW_TARGET_VERDICT,
        "message": resolve_message,
        "run_outcome": synthetic_run_outcome(lifecycle=TargetLifecycle.NEW_TARGET),
    }
    if project is not None:
        report["project"] = project
    if head_sha is not None:
        report["head_sha"] = head_sha
    if base_ref is not None:
        report["base_ref"] = base_ref
    if tool_version is not None:
        report["tool_version"] = tool_version
    if action_version is not None:
        report["action_version"] = action_version
    return report


def final_exit_code(
    gate_mode: str, *, real_exit_code: int, operational_error: bool
) -> int:
    """``check-target``'s own composite exit code (ADR-047 §7).

    Operational errors (a hard ``resolve-baseline`` failure, or the analysis
    step itself erroring out on bad config -- never a genuine ABI/API
    finding) always fail the job regardless of ``gate-mode`` -- "``deferred``
    only defers the *compatibility* verdict's effect on exit code, never
    operational errors" (§7), applied identically to ``advisory`` since
    resolve-baseline's failure taxonomy is never silently degraded to a
    passing/neutral outcome either.
    """
    if gate_mode not in GATE_MODES:
        raise ValueError(f"gate_mode must be one of {GATE_MODES}, got {gate_mode!r}")
    if operational_error:
        return 1
    if gate_mode == "local":
        return real_exit_code
    return 0
