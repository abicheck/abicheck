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
from typing import Any

from ..checker_types import validate_check_id, validate_evidence_depth
from ..schemas import REPORT_SCHEMA_VERSION, SCAN_SCHEMA_VERSION

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

#: ``resolve-baseline``'s five failure outcomes (ADR-047 §6) that are never
#: a compatibility verdict -- distinct from ``not_found`` + bootstrap, which
#: is an advisory pass, not a failure.
RESOLVE_FAILURE_OUTCOMES = frozenset(
    {"not_found", "ambiguous", "wrong_profile", "stale_schema", "incompatible_evidence"}
)

GATE_MODES = ("local", "deferred", "advisory")


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


#: Ladder order (shallow -> deep), matching EVIDENCE_DEPTH_VALUES.
_DEPTH_RANK = {"binary": 0, "headers": 1, "build": 2, "source": 3}


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
        achieved = old_d if _DEPTH_RANK[old_d] <= _DEPTH_RANK[new_d] else new_d
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


def augment_report(
    report: dict[str, Any],
    *,
    name: str,
    profile_id: str,
    baseline_channel: str,
    requested_depth: str,
    gate_mode: str,
    project: str | None = None,
    head_sha: str | None = None,
    base_ref: str | None = None,
    action_version: str | None = None,
) -> dict[str, Any]:
    """Layer ADR-047 §7's identity/new fields onto a real analysis report.

    *report* is the already-parsed JSON dict a ``compare``/``scan`` run
    produced (root ``action.yml``'s ``report-path`` output). Returns a new
    dict -- *report* itself is never mutated.
    """
    if gate_mode not in GATE_MODES:
        raise ValueError(f"gate_mode must be one of {GATE_MODES}, got {gate_mode!r}")
    out = dict(report)
    check_id = build_check_id(name, profile_id, baseline_channel, requested_depth)
    effective_depth, coverage = derive_effective_depth(report, requested_depth)
    if "scan_schema_version" in report:
        # A scan report (baseline-channel: none) has its own schema marker
        # and shape (level/risk/coverage/... -- no library/old_file/summary/
        # changes/...) -- bump it to the latest version for this envelope's
        # new additive fields instead of also stamping report_schema_version
        # (the *compare*-report schema's marker), which would make a
        # downstream validator select compare_report.schema.json for a
        # report that structurally can never satisfy it (Codex review).
        out["scan_schema_version"] = SCAN_SCHEMA_VERSION
    elif "libraries" in report and "old_dir" in report:
        # A kind: bundle / directory-package compare report (the per-library
        # release fan-out's own summary shape: verdict/old_dir/new_dir/
        # libraries/... -- no singular library/old_file/new_file/summary/
        # changes/... either) has never had a schema of its own; leave it
        # unversioned here too rather than falsely claiming the single-pair
        # compare schema (same rationale as the scan case above, Codex
        # review). ADR-047 §7's identity/policy-gate fields below still
        # apply regardless of report shape.
        pass
    else:
        out["report_schema_version"] = REPORT_SCHEMA_VERSION
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
    real_exit_code = _real_exit_code(report)
    out["policy_gate_decision"] = "fail" if real_exit_code != 0 else "pass"
    if raw_verdict in LEGACY_VERDICT_VALUES:
        out["compatibility_verdict"] = raw_verdict
    if raw_verdict == OPERATIONAL_ERROR_VERDICT:
        out["operational_errors"] = [
            {
                "kind": "analysis_error",
                "message": str(report.get("error") or "the analysis step failed"),
            }
        ]
    else:
        out.setdefault("operational_errors", [])
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
