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

"""Baseline-compare and dry-run-estimate helpers for :mod:`abicheck.cli_scan`.

Split out of the (near-cap) ``cli_scan`` module: these are the two ``scan``
sub-flows that stand apart from the always-on core pipeline —

* ``scan --baseline`` (:func:`_run_baseline_compare` + its native-library
  sniff :func:`_baseline_is_native_library`), and
* ``scan --estimate`` (:func:`_emit_estimate`), plus the small header-provenance
  helpers they share with the core (:func:`_public_provenance_set`,
  :func:`_expand_public_headers`) and the ``--risk-rules`` loader
  (:func:`_load_risk_rules`).

``cli_scan`` re-imports every name below so the historical import paths
(``abicheck.cli_scan._run_baseline_compare`` etc., relied on by the scan tests
and ``service_scan``) keep resolving unchanged. The heavy engine dependencies
(``service``, ``cli_buildsource``, ``errors``, ``yaml``) stay function-local
exactly as they were in ``cli_scan`` so import time is unaffected.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from .buildsource.scan_levels import EvidenceDepth, SourceMethod
from .checker_policy import ADDITION_KINDS
from .errors import SnapshotError
from .workflows.scan_config import RiskRules

if TYPE_CHECKING:
    from .environment_matrix import EnvironmentMatrix
    from .service_scan import CompileContext
    from .workflows.policy_file import PolicyFile
    from .workflows.suppression import SuppressionList


def _public_provenance_set(
    headers: list[Path], public_header_dirs: list[Path]
) -> tuple[list[Path], list[Path]]:
    """CLI alias for ``workflows.scan_config.public_provenance_set``.

    The rule moved to the engine in ADR-061 Phase 4 (``service_scan`` needed it
    and had to import upward for it); this spelling stays because several call
    sites and tests use it.
    """
    from .workflows.scan_config import public_provenance_set

    return public_provenance_set(headers, public_header_dirs)


def _expand_public_headers(headers: list[Path]) -> list[str]:
    """Expand ``-H`` inputs (files or directories) to individual header files.

    ``-H/--headers`` accepts a directory (the snapshot build expands it the same
    way); the S2 leak pass needs the individual header *files* so clang
    preprocesses each one, not a directory as a single bogus TU. Falls back to the
    raw paths if expansion fails (e.g. an empty dir) so the pass still runs.

    A thin delegate to :func:`abicheck.service_scan.expand_public_header_inputs`
    since CLI cleanup phase two's PR 3A -- the shared, engine-layer resolver
    ``scan``'s candidate now routes through needs the identical expansion, and
    an engine module may not import a ``cli_*`` sibling. Kept under this name
    so this module's own callers and tests are unchanged.
    """
    from .service_scan import expand_public_header_inputs

    return expand_public_header_inputs(headers)


def _emit_estimate(
    *,
    binary: Path,
    headers: list[Path],
    includes: list[Path],
    sources: Path | None,
    build_info: Path | None,
    mode: str,
    resolved_method: SourceMethod,
    eff_depth: EvidenceDepth,
    changed: list[str],
    seeded: bool,
    budget_s: float | None,
    lang: str,
    fmt: str,
    output: Path | None,
) -> None:
    """Render the ADR-035 D10 dry-run cost estimate (``scan --estimate``).

    A thin front-end over :func:`service.estimate_scan`: builds a
    :class:`service.ScanRequest`, probes the project (TU count, header fan-out)
    and prints the projected per-layer cost — scanning nothing, running no
    compiler. Always exits 0 (it is a probe, not a gate).
    """
    # Imported lazily (not at module top) so importing cli_scan_baseline never
    # forces cli's module-load tail — which imports cli_scan, which imports back
    # from here — to run before this module finishes (partial-init cycle).
    from .cli import _safe_write_output
    from .service import Budget, ScanRequest, estimate_scan

    req = ScanRequest(
        binaries=[binary],
        headers=headers,
        includes=includes,
        sources=sources,
        build_info=build_info,
        mode=mode,
        source_method=resolved_method.value,
        depth=eff_depth.value,
        changed_paths=list(changed),
        seeded=seeded,
        budget=Budget(total_timeout=budget_s),
        lang=lang,
    )
    # Pass the *already-resolved* level so the estimate mirrors the real scan
    # exactly — re-resolving from the round-tripped flags would re-apply the
    # source-method > depth precedence and lose a mode preset's deeper depth
    # (pr-deep = (s5, graph)); Codex review.
    estimates = estimate_scan(req, resolved_level=(resolved_method, eff_depth))
    total = sum(e.est_seconds for e in estimates)

    if fmt == "json":
        text = json.dumps(
            {
                "mode": mode,
                "estimate": [e.to_dict() for e in estimates],
                "total_est_seconds": round(total, 3),
            },
            indent=2,
        )
    else:
        lines = [
            f"abicheck scan --estimate — {mode} mode (dry run; nothing scanned)",
            "",
        ]
        lines.append(f"  {'layer':<16} {'method':<8} {'TUs':>6}  {'est_s':>8}  note")
        for e in estimates:
            lines.append(
                f"  {e.layer:<16} {(e.method or '-'):<8} {e.tus:>6}  "
                f"{e.est_seconds:>8.2f}  {e.note}"
            )
        lines.append("")
        lines.append(f"  projected total: {total:.2f}s")
        text = "\n".join(lines)

    if output:
        _safe_write_output(output, text)
        click.echo(f"Estimate written to {output}", err=True)
    else:
        click.echo(text)


def _load_risk_rules(path: Path | None) -> RiskRules:
    """CLI adapter over ``workflows.scan_config.load_risk_rules``.

    Translates the engine's ``SnapshotError`` into a plain ``ClickException``
    (**exit 1** -- operational, not a usage error: the flag was well-formed and
    the file was not). Message unchanged from before the move.
    """
    from .workflows.scan_config import load_risk_rules

    try:
        return load_risk_rules(path)
    except SnapshotError as exc:
        raise click.ClickException(str(exc)) from exc


#: Default cap on findings embedded in the ``scan --baseline`` summary so a
#: large diff cannot blow up the always-on scan text/JSON output;
#: ``--format json`` on the full ``compare`` command remains the way to see
#: everything. Overridable per run via ``scan --max-findings``/
#: ``ScanRequest.max_findings``, or globally via the
#: ``ABICHECK_MAX_BASELINE_FINDINGS`` env var when neither passes an explicit
#: value -- see :func:`_resolve_max_baseline_findings`.
_MAX_BASELINE_FINDINGS = 20

#: Env var read by :func:`_resolve_max_baseline_findings` when a caller does
#: not pass an explicit ``max_findings``. Kept distinct from a CLI/API default
#: of ``None`` so "not specified" is distinguishable from "explicitly 20".
_MAX_BASELINE_FINDINGS_ENV_VAR = "ABICHECK_MAX_BASELINE_FINDINGS"


def _resolve_max_baseline_findings(max_findings: int | None) -> int:
    """Resolve the effective findings cap: explicit override, else env, else default.

    *max_findings* is the per-call override (``scan --max-findings`` /
    ``ScanRequest.max_findings``); it wins when given. Otherwise
    ``ABICHECK_MAX_BASELINE_FINDINGS`` lets a CI job raise (or lower) the cap
    globally without a code change, matching how other numeric env-var knobs
    in this codebase work. An unset or non-positive-int env value is ignored
    (falls back to :data:`_MAX_BASELINE_FINDINGS`) rather than raising --
    this cap only ever bounds report *size*, so a malformed override should
    degrade to the safe default, not fail the scan.
    """
    if max_findings is not None:
        if max_findings < 1:
            # A plain ValueError, not click.ClickException: this shared helper
            # is also reached from the Python API (service_scan.run_scan ->
            # scan_engine.run_scan_core -> _run_baseline_compare), whose
            # callers never import click. The CLI path itself never reaches
            # this branch -- `scan --max-findings` is a `click.IntRange(min=1)`
            # option, so click already rejects a non-positive value before
            # cli_scan.scan_cmd ever calls in this deep (Codex review: a
            # click.ClickException leaking through the typed API is also the
            # wrong exception type for that boundary -- run_scan's own
            # upfront check raises errors.ValidationError instead, and this
            # is only a defensive fallback for a caller that bypasses it).
            raise ValueError(
                f"max_findings must be a positive integer, got {max_findings}"
            )
        return max_findings
    import os

    env_value = os.environ.get(_MAX_BASELINE_FINDINGS_ENV_VAR)
    if env_value:
        try:
            parsed = int(env_value)
        except ValueError:
            return _MAX_BASELINE_FINDINGS
        if parsed >= 1:
            return parsed
    return _MAX_BASELINE_FINDINGS


#: The two severity categories made up of findings ``_baseline_summary``
#: deliberately does not itemize -- they carry no verdict, so under the legacy
#: scheme they are exactly the "additions/quality noise" that comment names.
_COMPATIBLE_SEVERITY_CATEGORIES = frozenset({"addition", "quality_issues"})

#: Kind value strings that constitute new public-API surface -- the same
#: registry-sourced set `pr_comment.py` uses for `compare`'s own "Public API
#: additions" section (`ADDITION_KINDS`, not a hand-picked list -- picks up a
#: kind that doesn't end in "_added" too, e.g. `type_field_added_compatible`).
_ADDITION_KIND_VALUES = frozenset(k.value for k in ADDITION_KINDS)


def _addition_finding_dicts(
    diff: Any, cap: int
) -> tuple[list[dict[str, Any]], bool, int]:
    """Always-on itemization of new public-API surface, for a PR comment.

    ``diff.compatible`` normally contributes only its bare count to
    ``_baseline_summary`` (see ``_COMPATIBLE_SEVERITY_CATEGORIES``'s own
    docstring -- "additions/quality noise this summary was never meant to
    itemize"), and is itemized above only when severity policy made one of
    them the run's actual blocking cause. Rendering a ``scan --against``
    result as a sticky PR comment (``pr_comment_scan.from_scan``) needs more than
    that: a green "➕ Public API additions" table, the same thing `compare`'s
    own JSON report already carries via its full `changes` list -- so this
    itemizes just the addition-shaped subset of `diff.compatible`
    (`_ADDITION_KIND_VALUES`, never a quality finding) unconditionally,
    capped independently of the gating findings above so a large addition set
    can never crowd out a real gating finding from the shared budget.

    Returns ``(dicts, truncated, total)`` -- ``total`` is the exact,
    untruncated addition count (Codex review): ``diff.compatible``'s own
    scalar mixes additions and quality findings, so the PR comment has no
    other way to render an exact "N safe" header count once ``dicts`` itself
    is capped below ``total``.
    """
    addition_changes = [
        c
        for c in getattr(diff, "compatible", None) or ()
        if _change_kind_str(c) in _ADDITION_KIND_VALUES
    ]
    dicts = _baseline_finding_dicts(
        addition_changes[:cap],
        "compatible",
        policy_file=getattr(diff, "policy_file", None),
    )
    return dicts, len(addition_changes) > len(dicts), len(addition_changes)


def _quality_finding_dicts(
    diff: Any, cap: int
) -> tuple[list[dict[str, Any]], bool, int]:
    """Always-on itemization of :func:`_addition_finding_dicts`'s complement
    -- the compatible-but-non-addition subset of ``diff.compatible`` (a
    quality-category change like ``func_noexcept_added``, or a
    policy-demoted removal reclassified compatible).

    Codex review, follow-up to the exact-safe-total fix in
    ``pr_comment_scan.from_scan``: that fix corrected the *count* to read
    the full ``diff.compatible`` scalar, but a scan whose only compatible
    findings were quality-shaped still had nothing to itemize -- the
    header could say "3 safe" with an empty green section and no way to
    tell a reviewer what those three findings actually were. Mirrors
    ``_addition_finding_dicts`` exactly (same cap, same ``"compatible"``
    bucket label, same ``(dicts, truncated, total)`` return shape) with the
    membership test inverted, capped independently of both the gating
    findings and the addition findings so neither crowds this one out of
    the shared report.
    """
    quality_changes = [
        c
        for c in getattr(diff, "compatible", None) or ()
        if _change_kind_str(c) not in _ADDITION_KIND_VALUES
    ]
    dicts = _baseline_finding_dicts(
        quality_changes[:cap],
        "compatible",
        policy_file=getattr(diff, "policy_file", None),
    )
    return dicts, len(quality_changes) > len(dicts), len(quality_changes)


def _add_severity_blocking_compatible_findings(
    summary: dict[str, Any],
    diff: Any,
    gate: dict[str, Any],
    max_findings: int | None = None,
) -> None:
    """Itemize compatible findings when severity made *them* the blocking cause.

    ``_baseline_summary`` omits ``diff.compatible`` from ``findings`` because
    under the legacy scheme those findings never gate -- so naming them would
    be noise. Severity inverts that for two categories: with
    ``severity.addition: error`` a compatible diff exits 1, and the report
    then named the blocking *category* and count while giving no symbol, kind,
    or description for the finding that actually failed the scan (Codex
    review).

    Only the blocking case is added, and only for the two categories that can
    be blocking-yet-compatible, so every other run's summary is byte-identical.
    The cap is the same resolved ``--max-findings`` budget the gating buckets
    already spent from (see :func:`_resolve_max_baseline_findings`) -- an
    addition that blocks is worth naming, but not at the price of unbounded
    output. *max_findings* must be the identical value already passed to
    :func:`_baseline_summary` for this ``diff``, so the two stages agree on
    one cap rather than each resolving (and potentially disagreeing on) their
    own.
    """
    if not gate.get("blocking"):
        return
    blamed = set(gate.get("blocking_categories") or ())
    if not (blamed & _COMPATIBLE_SEVERITY_CATEGORIES):
        return
    blocking = _blocking_compatible_changes(diff, blamed)
    if not blocking:
        return
    cap = _resolve_max_baseline_findings(max_findings)
    findings: list[dict[str, Any]] = list(summary.get("findings") or [])
    added = _baseline_finding_dicts(
        blocking, "compatible", policy_file=getattr(diff, "policy_file", None)
    )
    # Both groups get a share; neither may evict the other outright --
    # appending only if there's room lets the legacy buckets starve a
    # compatible blocker that actually failed the run, while reserving the
    # whole cap for compatible blockers does the mirror image (20+
    # error-level additions alongside an ABI break exiting 4 while itemizing
    # only additions). Severity decides the *order*; a reserved floor keeps
    # both causes of the exit code represented (Codex review).
    reserved = min(len(added), max(1, cap // 4))
    head = findings[: cap - reserved]
    tail = added[: cap - len(head)]
    summary["findings"] = head + tail
    if len(findings) > len(head) or len(added) > len(tail):
        summary["findings_truncated"] = True
        # Findings evicted here were either already counted in `findings`
        # (bumped out to make room for a compatible blocker) or never made
        # it in at all (`added` beyond `tail`) -- both are real cuts, so both
        # accumulate onto the same per-kind ledger `_baseline_summary` started.
        _accumulate_kind_counts(
            summary,
            "findings_truncated_kinds",
            (d["kind"] for d in findings[len(head) :]),
        )
        _accumulate_kind_counts(
            summary,
            "findings_truncated_kinds",
            (d["kind"] for d in added[len(tail) :]),
        )


def _blocking_compatible_changes(diff: Any, blamed: set[str]) -> list[Any]:
    """The compatible findings whose own category is one severity blamed.

    Slicing all of ``diff.compatible`` spent the report's budget on the
    *non-blocking* compatible category too -- with ``severity.addition: error`` a quality finding is as compatible as an addition, but only the
    addition failed the run (Codex review). Classified through
    ``classify_change_object`` -- the same ``classify_effective_change`` the
    gate itself routed through -- so the two cannot disagree about which
    category a finding belongs to, and an ADR-027 per-finding demotion is
    honoured identically in both.

    Passes ``diff.policy_file`` through, not just ``diff._effective_kind_sets()``
    (Codex review): a kind-global `overrides:` entry is already baked into
    the kind sets, but a selector-scoped `reclassify:` rule (A: selector-
    scoped reclassification) isn't expressible as a kind set at all -- only
    the real ``PolicyFile`` object can answer "did a rule reclassify *this
    symbol*". Without it, a `reclassify:`-demoted finding that still blocks
    the gate (e.g. reclassified to a category the severity preset also
    errors on) was silently missing from the scan's own blocking-findings
    report, even though the gate itself (`_build_severity_json`, which does
    already pass `policy_file`) correctly named it as blocking.
    """
    from .workflows.gate import classify_change_object

    kept: list[Any] = []
    for change in list(getattr(diff, "compatible", ()) or ()):
        try:
            category = classify_change_object(
                change,
                policy=getattr(diff, "policy", None),
                kind_sets=diff._effective_kind_sets(),
                policy_file=getattr(diff, "policy_file", None),
            )
        except Exception:  # pragma: no cover - duck-typed stand-ins in tests
            continue
        if getattr(category, "value", str(category)) in blamed:
            kept.append(change)
    return kept


def _change_kind_str(c: Any) -> str:
    """The same tolerant ``kind`` read ``_baseline_finding_dicts`` uses, standalone.

    Shared so the per-kind truncation ledger (:func:`_accumulate_kind_counts`)
    counts a raw ``Change``/duck-typed stand-in the identical way the finding
    dicts spell its ``kind`` -- a mismatch here would make the ledger's keys
    disagree with the ``kind`` values in ``summary["findings"]`` itself.
    """
    kind = getattr(c, "kind", None)
    return str(getattr(kind, "value", str(kind)))


def _accumulate_kind_counts(summary: dict[str, Any], field: str, kinds: Any) -> None:
    """Add *kinds* (an iterable of kind strings) onto ``summary[field]``.

    Kept as a running dict (not overwritten) since both ``_baseline_summary``
    and :func:`_add_severity_blocking_compatible_findings` can each cut
    findings from the same summary -- a second pass's cuts must add to the
    first's counts, not replace them. Sorted by kind name (not count) so the
    JSON is deterministic and diff-friendly across runs of the same input.
    """
    from collections import Counter

    counter: Counter[str] = Counter(summary.get(field) or {})
    counter.update(kinds)
    if counter:
        summary[field] = dict(sorted(counter.items()))


def _pre_suppression_bucket(diff: Any, c: Any) -> str | None:
    """The verdict bucket *c* would have landed in had ``--suppress`` not
    withheld it, or ``None`` when *diff* can't answer (a lightweight
    duck-typed stub without ``_effective_verdict_for_change``, used by tests
    that don't exercise this path).

    Reuses ``DiffResult._effective_verdict_for_change`` -- the exact
    function ``breaking``/``source_breaks``/``risk``/``compatible`` are each
    already filtered through -- so a suppressed change's reported bucket can
    never disagree with what the same change would have counted as had it
    not been suppressed (policy-file overrides and frozen-namespace guards
    included).
    """
    effective_verdict_for_change = getattr(diff, "_effective_verdict_for_change", None)
    if effective_verdict_for_change is None:
        return None
    from .change_registry_types import Verdict

    verdict = effective_verdict_for_change(c)
    return {
        Verdict.BREAKING: "breaking",
        Verdict.API_BREAK: "api_break",
        Verdict.COMPATIBLE_WITH_RISK: "risk",
        Verdict.COMPATIBLE: "compatible",
        Verdict.NO_CHANGE: "compatible",
    }.get(verdict)


def _baseline_finding_dicts(
    changes: list[Any],
    bucket: str,
    *,
    pre_suppression_bucket_of: Any = None,
    policy_file: Any = None,
) -> list[dict[str, Any]]:
    """Project *changes* (one verdict bucket) into small, renderable dicts.

    Reads only duck-typed attributes (not ``DiffResult`` internals) so this
    stays safe to call against the lightweight fakes/stubs used in tests, not
    just a real ``Change``.

    The ``bucket="suppressed"`` case (Codex review, PR #657) also carries
    ``suppression_rule`` -- which ``--suppress`` rule silenced the finding
    (``Change.suppression_rule``, set by ``checker._filter_suppressed_changes``)
    -- mirroring `compare`'s own suppression audit trail
    (``reporter._suppressed_change_entry``'s
    ``impact_assessment.decision.suppression_rule``). Kept out of the other
    (breaking/api_break/risk) buckets' dicts, which never carry a
    ``suppression_rule`` value and whose exact shape existing tests and
    sibling modules (``cli_compare_release.py``, ``stack_report.py``) already
    pin.

    **ADR-049 Phase 5 §6.4.** The Gate wants the two commands' shared
    comparison findings compared *field by field*, and named the fields:
    canonical identity, ``ChangeKind``, contract relevance/reason/evidence
    side, compatibility decision, and suppression. ``kind``/``symbol``/
    ``suppression_rule`` covered three of those; ``finding_id`` (the same
    canonical identity ``reporter._change_to_dict`` emits, so the two are
    joinable rather than merely both present) and the four contract fields
    close the rest. The contract keys appear only when a finding actually
    carries a decision -- i.e. under ``scan --against --contract``
    -- exactly as ``reporter._add_contract_evaluation_fields`` gates them,
    so an ordinary scan's summary is byte-identical to before.

    *pre_suppression_bucket_of*, when given (``bucket == "suppressed"``
    only), is a callable resolving one change to the verdict bucket
    ("breaking"/"api_break"/"risk"/"compatible") it would have landed in had
    ``--suppress`` not withheld it -- "reporting must survive suppression"
    means a suppressed finding's *entry* must say more than "suppressed";
    without this a reader could not tell a suppressed ABI break apart from a
    suppressed cosmetic quality note.

    *policy_file*, when given, is the run's resolved ``PolicyFile`` (same
    object ``DiffResult.policy_file`` carries) -- passed through to stamp
    ``reclassified_by`` the identical way ``reporter._change_to_dict`` does
    for ``compare``'s own JSON report (Codex review, upstream ask #2):
    without it, a ``scan --format json`` reader saw a downgraded verdict
    with no way to tell *which* ``reclassify:`` rule produced it, unlike the
    compare/report path.
    """
    from .reporter import _reclassified_by_for_change
    from .workflows.findings import report_canonical_finding_id, report_finding_id

    findings = []
    for c in changes:
        kind = getattr(c, "kind", None)
        entry: dict[str, Any] = {
            "bucket": bucket,
            "kind": getattr(kind, "value", str(kind)),
            "symbol": getattr(c, "symbol", None),
            "description": getattr(c, "description", None),
            "source_location": getattr(c, "source_location", None),
            "finding_id": report_finding_id(c),
            # Backend-independent sibling of finding_id (schema 2.36) --
            # see finding_identity.report_canonical_finding_id's docstring.
            "canonical_finding_id": report_canonical_finding_id(c),
        }
        # ELF symbol linkage of a removed symbol (Change.symbol_binding) --
        # scan --against shares the same --suppress surface as compare, so
        # a binding-scoped suppression's match/no-match needs to be
        # auditable here too, not just in compare's JSON/SARIF (Codex
        # review, fresh evidence).
        binding = getattr(c, "symbol_binding", None)
        if binding:
            entry["symbol_binding"] = binding
        reclassified_by = _reclassified_by_for_change(c, policy_file)
        if reclassified_by:
            entry["reclassified_by"] = reclassified_by
        _add_contract_fields(entry, c)
        if bucket == "suppressed":
            entry["suppression_rule"] = getattr(c, "suppression_rule", None)
            if pre_suppression_bucket_of is not None:
                entry["pre_suppression_bucket"] = pre_suppression_bucket_of(c)
        findings.append(entry)
    return findings


def _add_contract_fields(entry: dict[str, Any], c: Any) -> None:
    """Copy *c*'s shadow contract decision into *entry*, if it has one.

    The projection of ``reporter._add_contract_evaluation_fields`` this
    summary can carry: same keys, same values, same "absent means unstamped"
    rule. Deliberately not an import of that function -- it also computes a
    ``finding_id`` fallback and is typed against report dicts -- but the key
    names are asserted equal to the reporter's by
    ``tests/test_scan_compare_parity.py``, so the two cannot drift into
    naming the same decision differently.

    Enum values are read the same tolerant way ``_baseline_finding_dicts``
    reads ``kind`` -- that function's whole contract is that it stays safe
    against the lightweight fakes the surrounding tests and sibling modules
    build, and requiring a real enum here would have broken it for exactly
    those callers (CodeRabbit review).

    Includes ADR-049 D1's ``compatibility_evaluation_status`` /
    ``compatibility_decision`` pair, and for the same reason as everything
    else here: a `scan --against --contract` row that carried
    relevance but not the decision could not be compared field-by-field with
    the `compare` finding for the same fact, which is exactly the
    cross-command divergence this projection exists to prevent (Codex
    review). ``compatibility_decision`` is JSON ``null`` for a
    ``NOT_EVALUATED`` row and must stay that way -- ``null`` records that
    policy never ran, which is not a verdict.

    Not ``gate_contribution``, deliberately: ``scan --against`` computes its
    own exit code from its own verdict and budget rules, so a per-finding
    number copied from ``compare``'s severity gate would be a claim about a
    gate this command does not run. The status/decision pair is a property of
    the finding; the contribution is a property of the gate.
    """
    relevance = getattr(c, "contract_relevance", None)
    if relevance is None:
        return
    entry["contract_relevance"] = getattr(relevance, "value", str(relevance))
    entry["contract_reason_code"] = getattr(c, "contract_reason_code", None)
    assurance = getattr(c, "contract_assurance", None)
    if assurance is not None:
        entry["contract_assurance"] = getattr(assurance, "value", str(assurance))
    from .contract_gating import evaluation_status_of

    status = evaluation_status_of(c)
    if status is not None:
        entry["compatibility_evaluation_status"] = status.value
    decision = getattr(c, "compatibility_decision", None)
    entry["compatibility_decision"] = getattr(decision, "value", None)
    refs = getattr(c, "contract_evidence_refs", None)
    if refs is not None:
        entry["contract_evidence_refs"] = list(refs)


def _baseline_is_native_library(path: Path) -> bool:
    """True if *path* is a native binary, not a JSON / ABICC-dump snapshot.

    A snapshot baseline already has its headers baked in, so the candidate-`-H`
    reuse is harmless there; only a native binary is re-parsed (and thus at risk
    of being read through the wrong headers).

    Detection is content-first to match `resolve_input`'s own native dispatch:
    magic-byte sniffing (`detect_binary_format`) catches the cases a suffix scan
    misses — an extensionless ELF (`build/foo`), a Mach-O framework binary, a
    `.pyd`/`.node` shared object (Codex review). The filename heuristic is only a
    fallback for paths that cannot be sniffed (e.g. a not-yet-existing file in a
    unit test), and the snapshot suffixes short-circuit first so a real `.json`
    on disk is never mis-sniffed.
    """
    name = path.name.lower()
    if name.endswith((".json", ".dump", ".tar.gz", ".tgz", ".xml")):
        return False
    from .workflows.extraction import detect_binary_format

    if detect_binary_format(path) is not None:
        return True
    return ".so" in name or name.endswith((".dll", ".dylib"))


def _resolve_baseline_header_scope(
    baseline: Path,
    headers: list[Path],
    includes: list[Path],
    public_headers: list[Path],
    public_header_dirs: list[Path],
    baseline_headers: list[Path] | None,
    baseline_includes: list[Path] | None,
) -> tuple[list[Path], list[Path], list[Path], list[Path]]:
    """Pick the old side's ``(headers, includes, public_headers, public_dirs)``.

    Each side is parsed with its *own* headers. ``scan`` has a single ``-H``
    (built for the candidate); for a native ``--against`` library whose public
    headers differ, ``-H old=PATH``/``-I old=PATH`` (side-aware, ADR-043 D5)
    select the old side's headers. Without them we reuse the candidate
    ``-H``/``-I`` — correct only when the headers did not change — so warn
    rather than silently read the old side through the new headers (Codex).
    """
    if not baseline_headers:
        if headers and _baseline_is_native_library(baseline):
            click.echo(
                f"warning: --against {baseline.name} is a native library parsed "
                f"with the new build's headers (-H); if its public headers differ "
                f"from the new version, pass -H old=PATH/-I old=PATH (else the old "
                f"side is read through the new headers and the diff may be "
                f"wrong/noisy).",
                err=True,
            )
        return headers, includes, public_headers, public_header_dirs

    bl_headers = list(baseline_headers)
    bl_includes = list(baseline_includes) if baseline_includes else includes
    # The old-side public boundary comes ONLY from `-H old=`: dirs in it
    # are public-header dirs, files opt in just themselves. Do NOT fall
    # back to the new side's public dirs -- a relative dir like `include/`
    # would (segment-based provenance) re-mark old private headers as
    # PUBLIC and skew the public-surface scoping (Codex review). Split by
    # file-vs-dir the same way the candidate side's `_public_provenance_
    # set` already does (Codex review, PR #624 follow-up): a lone
    # `-H old=<dir>` umbrella must feed its directory into `bl_public_dirs`
    # ONLY, not also into `bl_public_headers` as a raw directory "path" --
    # doing both fed the ADR-050 comparability gate an old side whose
    # declared scope was represented differently from the new side's (a
    # directory counted twice vs. once), a false `scope_fingerprint`
    # mismatch on an ordinary --against comparison.
    bl_public_headers = [p for p in bl_headers if not p.is_dir()]
    bl_public_dirs = [p for p in bl_headers if p.is_dir()]
    return bl_headers, bl_includes, bl_public_headers, bl_public_dirs


def _baseline_summary(
    diff: Any,
    max_findings: int | None = None,
    *,
    require_complete_analysis: bool = False,
) -> dict[str, Any]:
    """Build the always-on ``scan --against`` summary block from *diff*.

    Counts, detector provenance, the capped gating findings and the
    suppression audit trail — everything except the contract-context block,
    which :func:`_baseline_contract_block` adds separately because it also
    installs the front end's resolved configuration onto *diff*.

    *max_findings* overrides the default cap (``scan --max-findings`` /
    ``ScanRequest.max_findings``); ``None`` falls back to
    :func:`_resolve_max_baseline_findings` (env var, else the built-in
    default). Whenever a bucket is truncated, the kinds cut are also
    accumulated into ``findings_truncated_kinds``/``suppressed_truncated_kinds``
    (kind -> count cut) so the shape of what was dropped is visible without
    rerunning at a higher cap.

    *require_complete_analysis* mirrors the identically-named CLI flag: it
    is stamped into the summary's own ``analysis_assurance_exit_contribution``
    (schema 1.17) so a downstream reader -- chiefly ``abicheck aggregate``
    (``aggregate.GateInfo.from_scan_report``, which reads only the nested
    compatibility gate) -- can see whether this axis contributed to the
    exit code without recomputing it, the same way
    ``contract_coverage_exit_contribution`` already lets it read the
    orthogonal coverage axis (Codex review).
    """
    cap = _resolve_max_baseline_findings(max_findings)
    summary: dict[str, Any] = {
        "breaking": len(diff.breaking),
        "api_break": len(diff.source_breaks),
        "risk": len(diff.risk),
        "compatible": len(diff.compatible),
    }
    # Codex review: the resolved `--policy` (e.g. "strict_abi", or whatever
    # a non-default policy name/config resolved to) that actually classified
    # these buckets was never surfaced anywhere in `scan --against`'s JSON,
    # unlike `compare`'s own report (`reporter.py` always emits it) -- a
    # consumer had no way to tell which policy gated a `scan --against` run
    # without a separate `compare` invocation. `diff.policy` is the same
    # string `_build_severity_json`/`classify_change_object` above are
    # already keyed on. Duck-typed like the other optional reads in this
    # function: a real `DiffResult` always carries it, but this module is
    # also driven with lightweight stand-ins in tests that model only the
    # buckets they exercise.
    resolved_policy = getattr(diff, "policy", None)
    if resolved_policy is not None:
        summary["policy"] = resolved_policy
    # Codex review, upstream ask #2: `scan --format json` carried the
    # resolved policy *name* (above) but never the active `policy_overrides`/
    # `policy_reclassify` rule set `compare`'s own JSON report discloses
    # (`reporter._add_policy_overrides`) -- a reviewer saw a downgraded
    # verdict with no way to tell which rule produced it, unlike the
    # compare/report path. Mirrors that function's shape exactly (same key
    # names, same `ReclassifyRule.to_report_dict()` encoding) but reads
    # `policy_file` via `getattr` rather than a direct attribute access,
    # matching every other duck-typed read in this function -- `diff` here
    # is a real `DiffResult` in production but a lightweight stand-in
    # (`SimpleNamespace`) in several existing tests that don't model every
    # field.
    policy_file = getattr(diff, "policy_file", None)
    if policy_file is not None and getattr(policy_file, "overrides", None):
        summary["policy_overrides"] = {
            kind.value: verdict.value for kind, verdict in policy_file.overrides.items()
        }
        if getattr(policy_file, "source_path", None):
            summary["policy_file"] = str(policy_file.source_path)
    if policy_file is not None and getattr(policy_file, "reclassify", None):
        from .reclassify import active_reclassify_rules

        active = active_reclassify_rules(policy_file.reclassify)
        if active:
            summary["policy_reclassify"] = [rule.to_report_dict() for rule in active]
            if getattr(policy_file, "source_path", None):
                summary["policy_file"] = str(policy_file.source_path)
    # ADR-049 D9 conserves every detector fact in exactly one visible outcome.
    # The four buckets above are the *compatibility* axis, so since Phase 7
    # they exclude findings contract evaluation did not score -- and this
    # summary itemizes those buckets alone, so an excluded fact disappeared
    # from the scan report altogether rather than merely stopping gating
    # (Codex review, confirmed with a `PROVEN_OUT_OF_CONTRACT` removal:
    # `NO_CHANGE`, all counts zero, no findings at all). Emitted only when
    # non-empty; duck-typed like the other optional reads here, since this
    # module is also driven with lightweight stand-ins.
    not_evaluated = list(getattr(diff, "not_evaluated", ()) or ())
    if not_evaluated:
        summary["not_evaluated"] = len(not_evaluated)
    # Codex review: surface `coverage_warnings` the way `compare` does.
    coverage_warnings = list(getattr(diff, "coverage_warnings", ()) or ())
    if coverage_warnings:
        summary["coverage_warnings"] = coverage_warnings
    # ADR-049 Phase 5 §6.4 names *detector provenance* among the fields the
    # two commands must agree on: `compare`'s JSON report has carried it
    # since long before this Gate (`reporter._add_detectors`) while `scan
    # --against`'s summary carried nothing equivalent. Same shape and same
    # "only detectors with findings or a coverage gap" filter as the
    # reporter's, so the two are comparable rather than merely both non-empty.
    detectors = [
        {
            "name": det.name,
            "changes_count": det.changes_count,
            "enabled": det.enabled,
            "coverage_gap": det.coverage_gap,
            # ADR-067 D3, carried here for the same §6.4 parity reason the
            # comment above records: "did not run" and "ran, found nothing"
            # are different statements, and `changes_count: 0` cannot tell
            # them apart.
            "not_evaluated": getattr(det, "not_evaluated", False),
        }
        for det in getattr(diff, "detector_results", None) or []
        if det.changes_count > 0 or det.coverage_gap is not None
    ]
    if detectors:
        summary["detectors"] = detectors
    # Preserve the actual findings (kind/symbol/description/location), not just
    # their counts — a failing `scan --baseline` used to report e.g.
    # "breaking=1" with no way to tell which symbol broke without a separate
    # `compare` run. Only the gating buckets are embedded (compatible findings
    # are additions/quality noise this summary was never meant to itemize);
    # capped so a large diff cannot blow up the always-on scan output. Counts
    # (not dicts) decide truncation for each bucket so a large diff never
    # builds more finding dicts than the cap can ever keep (CodeRabbit review).
    total_gating = (
        len(diff.breaking)
        + len(diff.source_breaks)
        + len(diff.risk)
        + len(not_evaluated)
    )
    findings: list[dict[str, Any]] = []
    cut_kinds: list[str] = []
    for bucket_name, bucket_changes in (
        ("breaking", diff.breaking),
        ("api_break", diff.source_breaks),
        ("risk", diff.risk),
        # Last, and in its own bucket: these carry no verdict, so filing them
        # under one would claim a decision policy never made. `_baseline_finding_dicts`
        # already emits each one's relevance and reason, which is what says
        # why it did not gate.
        ("not_evaluated", not_evaluated),
    ):
        remaining = max(0, cap - len(findings))
        included, excluded = bucket_changes[:remaining], bucket_changes[remaining:]
        findings.extend(
            _baseline_finding_dicts(included, bucket_name, policy_file=policy_file)
        )
        # Keep tallying excluded kinds across every remaining bucket (not just
        # the one that first hit the cap) -- a bucket entirely past the cap
        # would otherwise contribute nothing to `findings_truncated_kinds`,
        # silently hiding its shape (see module-level truncation entry).
        cut_kinds.extend(_change_kind_str(c) for c in excluded)
    if findings:
        summary["findings"] = findings
        if total_gating > cap:
            summary["findings_truncated"] = True
            _accumulate_kind_counts(summary, "findings_truncated_kinds", cut_kinds)

    # Always-on itemization of new public-API surface (see
    # `_addition_finding_dicts`'s own docstring) -- separate from `findings`
    # above, which only ever names a `diff.compatible` entry when severity
    # policy made it the blocking cause.
    additions, additions_truncated, additions_total = _addition_finding_dicts(diff, cap)
    if additions:
        summary["additions"] = additions
        if additions_truncated:
            summary["additions_truncated"] = True
            summary["additions_total"] = additions_total

    # Always-on itemization of the complementary compatible-but-non-addition
    # subset (Codex review, follow-up) -- see `_quality_finding_dicts`'s own
    # docstring for why this exists alongside `additions` rather than
    # folded into it.
    quality, quality_truncated, quality_total = _quality_finding_dicts(diff, cap)
    if quality:
        summary["quality"] = quality
        if quality_truncated:
            summary["quality_truncated"] = True
            summary["quality_total"] = quality_total

    # ADR-049 Phase 5: surface the same suppression audit trail `compare`'s
    # own JSON report already exposes (`DiffResult.suppressed_changes`,
    # reporter.py's `_add_suppression`) -- without this, `scan --against`'s
    # summary silently hid which findings a `--suppress` rule removed, even
    # though the rule itself is honored (threaded into `compare_snapshots`
    # earlier in this same Phase 5 slice). Capped independently of the
    # gating-findings truncation above -- a large suppression file
    # shouldn't crowd out real gating findings from the always-on summary.
    suppressed_changes = getattr(diff, "suppressed_changes", None) or []
    if suppressed_changes:
        summary["suppressed_count"] = len(suppressed_changes)
        summary["suppressed"] = _baseline_finding_dicts(
            suppressed_changes[:cap],
            "suppressed",
            pre_suppression_bucket_of=lambda c: _pre_suppression_bucket(diff, c),
            policy_file=policy_file,
        )
        if len(suppressed_changes) > cap:
            summary["suppressed_truncated"] = True
            _accumulate_kind_counts(
                summary,
                "suppressed_truncated_kinds",
                (_change_kind_str(c) for c in suppressed_changes[cap:]),
            )
    # P0.4: `compare`'s own JSON report always carries `analysis_assurance`
    # (`reporter._add_analysis_assurance`, via the same narrowing helper),
    # regardless of whether `--require-complete-analysis` was passed --
    # `scan --against`'s summary carried nothing equivalent, so a caller
    # could not tell how complete/trustworthy a scan's own evidence was
    # (depth, TU/export accounting, header-context drift, ...) without a
    # separate `compare` invocation. `checker.compare` (reached through
    # `compare_snapshots` above) always attaches the result to *diff*, so
    # this is unconditional here too, exactly like `compare`'s report.
    from .workflows.gate import (
        analysis_assurance_exit_contribution,
        analysis_assurance_report_dict,
    )

    if (aa_block := analysis_assurance_report_dict(diff)) is not None:
        summary["analysis_assurance"] = aa_block
        # Persisted alongside the block itself, not unconditionally: a
        # `diff` carrying no real `AnalysisAssurance` (a hand-built object,
        # e.g. in a test, or an older in-memory result) has nothing to
        # report a contribution *for* either, and several existing callers
        # assert this summary's exact key set for the "nothing to report"
        # shape (back-compat with a consumer reading only the four
        # top-level counts) -- an unconditional key here would silently
        # break that contract the same way an unconditional
        # `analysis_assurance` block would. `0` covers both "the flag was
        # never given" and "given but already complete" (Codex review).
        summary["analysis_assurance_exit_contribution"] = (
            analysis_assurance_exit_contribution(
                diff, require_complete=require_complete_analysis
            )
        )
    return summary


def _baseline_contract_block(diff: Any, resolved_config: Any) -> dict[str, Any]:
    """The ADR-049 contract-context fields for the summary, or ``{}``.

    Installs this front end's own resolved configuration over the narrower
    object ``checker.compare`` reconstructs from its arguments, then emits the
    whole persisted context -- which ``scan --against --contract``
    computed and then dropped, so the receipt its per-finding decisions rest on
    was unobservable. Same encoder ``reporter._add_contract_context`` uses, so
    the block is byte-for-byte the one ``compare`` writes and
    ``replay_original_decisions`` reads back.
    """
    from .cli_scan_receipt import context_block, record_resolved_config

    if resolved_config is not None:
        record_resolved_config(diff, resolved_config)
    context = context_block(diff)
    if context is None:
        return {}
    from .contract_coverage_ledger import coverage_failures_for_context
    from .workflows.gate import coverage_exit_for_context

    # The sibling unsuppressible ledger, on the same terms `compare`
    # reports it (plan Section 6.1) -- a coverage failure is not a
    # finding, so it belongs beside the diff's findings, not among them.
    failures = coverage_failures_for_context(diff.contract_context)
    return {
        "contract_context": context,
        "contract_coverage_failures": [f.to_dict() for f in failures],
        "contract_coverage_exit_contribution": coverage_exit_for_context(
            diff.contract_context
        ),
    }


def _run_baseline_compare(
    baseline: Path,
    binary: Path,
    new_snap: Any,
    extra_changes: list[Any],
    lang: str,
    collect_mode: str,
    headers: list[Path],
    includes: list[Path],
    public_headers: list[Path],
    public_header_dirs: list[Path],
    compile_context: CompileContext | None = None,
    baseline_headers: list[Path] | None = None,
    baseline_includes: list[Path] | None = None,
    symbols_only: bool = False,
    debug_presence_only: bool = False,
    suppression: SuppressionList | None = None,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
    scope_to_public_surface: bool = True,
    force_public_symbols: set[str] | None = None,
    pattern_verdicts: bool = False,
    env_matrix: EnvironmentMatrix | None = None,
    collapse_versioned_symbols: bool = False,
    contract_evaluation: bool = False,
    contract_mode: str | None = None,
    resolved_config: Any = None,
    sev_config: Any = None,
    exit_code_scheme: str = "legacy",
    max_findings: int | None = None,
    require_complete_analysis: bool = False,
    requested_depth: str | None = None,
) -> tuple[str, int, dict[str, Any]]:
    """Compare *new_snap* against *baseline*, preserving scan authority.

    Single-version cross-source findings are reported in the scan's dedicated
    ``crosscheck`` block and stay advisory for baseline comparisons unless the
    maintainer explicitly promotes one with ``--crosscheck KEY=error``. They are
    not folded into ``extra_changes`` by default: doing so lets a candidate-side
    evidence hygiene finding such as ``header_build_context_mismatch`` turn a
    clean old/new artifact diff into an ``API_BREAK`` false positive. Real
    old/new embedded build/source drift is still diffed below via
    ``prepare_embedded_build_source``.

    *headers*/*includes* are the same scan header inputs used to build the
    candidate, threaded into the baseline parse so a native ``--baseline``
    library is header-scoped symmetrically — else the old side stays
    symbol/DWARF-only and the compare drops old type evidence or invents spurious
    API diffs (Codex review). They are inert for a JSON-snapshot baseline.

    *sev_config*/*exit_code_scheme* mirror ``compare``'s own severity gate
    (``severity.compute_exit_code``/``legacy_exit_code``): with
    ``exit_code_scheme == "severity"``, the returned exit code is the worst
    category with both a finding and an ``error``-level setting, rather than
    the verdict alone. ``exit_code_scheme == "legacy"`` (the default)
    reproduces the prior, unconditional ``verdict → {0,2,4}`` mapping. The
    caller (:func:`~abicheck.scan_engine.run_scan_core`) still folds the
    orthogonal contract-coverage floor and the cross-check severity
    promotion on top of whichever value this returns, exactly as before.

    *max_findings* overrides the default report cap (``scan --max-findings``
    / ``ScanRequest.max_findings``); ``None`` resolves through
    :func:`_resolve_max_baseline_findings` (env var, else the built-in
    default of 20). Threaded through to both :func:`_baseline_summary` and
    :func:`_add_severity_blocking_compatible_findings` so the two stages
    agree on one cap.

    The embedded L3/L4/L5 build/source packs on either snapshot are diffed via
    :func:`prepare_embedded_build_source` — the same path ``abicheck compare``
    uses — so source-only / graph findings the collected evidence reveals are
    folded into the verdict too (``checker.compare`` itself does not read
    ``build_source``).

    *require_complete_analysis* mirrors ``compare``'s own P0.4
    ``--require-complete-analysis``: ``checker.compare`` always attaches an
    ``analysis_assurance`` result to *diff* regardless of this flag; this
    parameter only controls whether an incomplete status additionally
    floors the returned exit code (folded with the same ``max`` discipline
    :func:`~abicheck.contract_coverage_exit.fold_coverage_exit` uses for
    its own orthogonal axis, immediately below).

    *requested_depth* (Codex review, fresh evidence): the caller's own
    explicitly-pinned ``--depth``/non-``auto`` ``--source-method`` (``None``
    when only inferred, mirroring ``cli_compare_helpers.
    _report_compare_result``'s "explicit override, never inferred"
    discipline for ``compare --depth``). ``checker.compare()``'s own
    internal ``compute_analysis_assurance`` call runs *before* this
    function ever sees *diff*, so it always reads
    ``DiffResult.requested_depth`` as ``None`` regardless of what the scan
    was actually pinned to -- without this, an unreached ``--depth
    source`` silently defeats ``--require-complete-analysis``. When given,
    *diff* is stamped and ``analysis_assurance`` recomputed below so the
    requested-vs-effective gate has something real to check.
    """
    from .cli_buildsource import prepare_embedded_build_source
    from .errors import AbicheckError
    from .service import collect_metadata, compare_snapshots, resolve_input

    # note_if_same_binary_compared lives in workflows.gate, not workflows.extraction (Codex review) -- see that module's own docstring for why a post-comparison coverage warning belongs there.
    from .workflows.gate import note_if_same_binary_compared

    bl_headers, bl_includes, bl_public_headers, bl_public_dirs = (
        _resolve_baseline_header_scope(
            baseline,
            headers,
            includes,
            public_headers,
            public_header_dirs,
            baseline_headers,
            baseline_includes,
        )
    )
    try:
        old_snap = resolve_input(
            baseline,
            bl_headers,
            bl_includes,
            version="",
            lang=lang,
            public_headers=bl_public_headers,
            public_header_dirs=bl_public_dirs,
            compile=compile_context,
            symbols_only=symbols_only,
            debug_presence_only=debug_presence_only,
            # Matches the candidate's own resolve_input() default in
            # scan_engine.py's run_scan_core -- a no-op for a JSON snapshot
            # baseline (already-serialized, no dumping happens), but keeps a
            # *native* --baseline library filtered consistently with the
            # candidate (Codex review).
            include_dependencies=False,
        )
    except AbicheckError as exc:
        raise click.ClickException(
            f"Failed to load --baseline {baseline}: {exc}"
        ) from exc

    # ADR-063 Phase 8 "--depth" ceiling (PR #1020): mirrors
    # `cli_compare_helpers.run_compare`'s own capped view.
    from .service_compare_pipeline import project_pair_to_depth

    old_snap, new_snap = project_pair_to_depth(old_snap, new_snap, requested_depth)

    # Preserve hard L0 removals even when the richer header/source view cannot
    # prove public-header ownership for the removed entity.  A source/full scan
    # may parse both sides' headers through different consumer macro contexts;
    # in fixtures such as case97 the old library exported a function that the
    # old header exposes only under a consumer macro, so the final public-surface
    # comparison can otherwise filter the old-only ELF fact away.  Re-reading the
    # already-loaded snapshots without public-surface scoping and carrying only
    # the hard ELF-only removal kind keeps the L0 authority while avoiding the
    # older false-positive class where advisory cross-check findings were folded
    # into the verdict wholesale.
    #
    # Shares abicheck.l0_export_delta.collect_l0_export_delta with
    # cli_helpers_compare.fold_l0_hard_removals (direct `compare`) -- ADR-049
    # Phase 5 §6.3 -- rather than each hand-copying the same
    # resolve-symbols-only-and-diff-unscoped extraction.
    l0_hard_removals: tuple[Any, ...] = ()
    if not symbols_only:
        from .l0_export_delta import collect_l0_export_delta

        l0_hard_removals = collect_l0_export_delta(baseline, binary, lang)
    # Fold embedded build-info/source (L3/L4/L5) diff findings into extra_changes
    # before comparing — mirrors the compare command (Codex review). Only engage
    # when a snapshot actually carries an embedded pack; otherwise pass
    # ``collect_mode="off"`` so the pipeline stays inert (no spurious collection
    # attempt / output noise on a plain artifact-only baseline compare).
    has_embedded = (
        old_snap.build_source is not None or new_snap.build_source is not None
    )
    merged_extra, _coverage_rows, _metrics, _ev = prepare_embedded_build_source(
        old_snap,
        new_snap,
        collect_mode if has_embedded else "off",
        [*extra_changes, *l0_hard_removals],
        None,
        None,
        None,
        None,
        policy_file=policy_file,
    )
    diff = compare_snapshots(
        old_snap,
        new_snap,
        suppression,
        policy=policy,
        policy_file=policy_file,
        extra_changes=merged_extra,
        scope_to_public_surface=scope_to_public_surface,
        force_public_symbols=force_public_symbols,
        pattern_verdicts=pattern_verdicts,
        env_matrix=env_matrix,
        collapse_versioned_symbols=collapse_versioned_symbols,
        contract_evaluation=contract_evaluation,
        contract_mode=contract_mode,
    )
    # Codex review: stamp metadata so the same-binary warning below fires here too (a no-op for JSON/Perl/symvers). Best-effort (mocked resolve_input tests may pass a path with no real file -- all-or-nothing). Hash through the full GNU ld linker-script chain to its final resolved target -- the same binary resolve_input() already followed above -- so a (possibly multi-hop) script vs. its target DSO still reads as byte-identical. Routed through `workflows.extraction`, not `binary_utils` directly -- this module is `frontends` layer under ADR-061, which may not import `extract` (where `binary_utils` lives).
    from .workflows.extraction import resolve_linker_script_chain

    def _hashable_path(p: Path) -> Path:  # skip linker-script resolution for a text snapshot/manifest, which can coincidentally match the INPUT()/GROUP() probe (Codex review)
        from .service import sniff_text_format

        return p if sniff_text_format(p) in ("json", "perl", "symvers") else resolve_linker_script_chain(p)

    try:
        old_meta = collect_metadata(_hashable_path(baseline))
        new_meta = collect_metadata(_hashable_path(binary))
    except OSError:
        pass
    else:
        diff.old_metadata, diff.new_metadata = old_meta, new_meta
        note_if_same_binary_compared(diff)
    # P0.4 (Codex review, fresh evidence): checker.compare()'s own internal
    # compute_analysis_assurance call (inside compare_snapshots above) runs
    # before this function ever sees *diff*, so it always reads
    # DiffResult.requested_depth as None regardless of what this scan was
    # actually pinned to -- mirroring the exact gap
    # cli_compare_helpers._report_compare_result already closes for
    # `compare --depth`. Stamp and recompute here so an explicit `scan
    # --against --depth source` that never reached source evidence reports
    # a genuinely incomplete status instead of silently reading "complete"
    # (nothing to compare the unset requested_depth against) and defeating
    # --require-complete-analysis.
    if requested_depth is not None:
        diff.requested_depth = requested_depth
        from .workflows.gate import compute_analysis_assurance

        diff.analysis_assurance = compute_analysis_assurance(
            diff,
            old_snap,
            new_snap,
            old_pack=getattr(old_snap, "build_source", None),
            new_pack=getattr(new_snap, "build_source", None),
        )
    summary = _baseline_summary(
        diff,
        max_findings=max_findings,
        require_complete_analysis=require_complete_analysis,
    )
    # ADR-049 Phase 5: install this front end's own resolved configuration
    # over the narrower object `checker.compare` reconstructs from its
    # arguments, then emit the whole persisted context -- which `scan
    # --against --contract` computed and then dropped, so the
    # receipt its per-finding decisions rest on was unobservable. Same
    # encoder `reporter._add_contract_context` uses, so the block is
    # byte-for-byte the one `compare` writes and `replay_original_decisions`
    # reads back.
    summary.update(_baseline_contract_block(diff, resolved_config))

    # CLI cleanup phase two, PR E: the same canonical `ExitDecision`
    # `reporter_contract_blocks.add_contract_context` persists for `compare`
    # (PR G1, #789), now also persisted here so a `scan --against` report
    # reader doesn't have to re-derive "why is this exit N" from the
    # separately-emitted `severity`/`analysis_assurance_exit_contribution`/
    # `contract_coverage_exit_contribution` fields. Nested under this
    # baseline-compare summary (`diff.exit`), matching where those same
    # constituent fields already live -- not at `ScanOutcome`'s own
    # top-level `verdict`/`exit_code`, which additionally folds the
    # scan-only budget/not-comparable/crosscheck-promotion axes
    # `exit_decision.py`'s own module docstring explicitly defers to PR
    # G2. `exit_scheme` mirrors the exact condition `base_exit` below is
    # already computed under (not bare `exit_code_scheme`), so a caller
    # that configured `exit_code_scheme="severity"` without ever resolving
    # a `sev_config` gets the identical legacy-scheme answer both places
    # agree on, rather than the resolver's severity branch's own assertion.
    from .workflows.gate import resolve_compare_exit_decision

    exit_scheme = (
        "severity"
        if exit_code_scheme == "severity" and sev_config is not None
        else "legacy"
    )
    summary["exit"] = resolve_compare_exit_decision(
        diff,
        sev_config,
        exit_scheme,
        require_complete_analysis=require_complete_analysis,
    ).to_dict()

    # CLI cleanup phase two, PR B: the same effective-config digest
    # `add_contract_context` persists for `compare`/the release fan-out --
    # same shared helper, same `exit_scheme`/`sev_config` pair the `exit`
    # block immediately above was just resolved from, so all three front
    # ends agree byte-for-byte whenever they actually resolved the same
    # configuration.
    from .reporter_contract_blocks import add_effective_config_digest

    add_effective_config_digest(
        summary,
        diff,
        severity_config=sev_config,
        exit_code_scheme=exit_scheme,
        require_complete_analysis=require_complete_analysis,
    )

    from .cli_compare_helpers import _verdict_exit_code
    from .workflows.gate import fold_coverage_exit, gate_decision_for_result

    verdict = diff.verdict.value
    # Mirrors `compare`'s own `_exit_with_severity_or_verdict` (cli.py):
    # `exit_code_scheme == "severity"` computes the worst error-level
    # category among *diff.changes* instead of mapping the overall verdict
    # straight to {0,2,4} -- e.g. `--severity-preset info-only` can leave a
    # BREAKING verdict at exit 0. Default ("legacy") is the prior,
    # unconditional verdict->exit mapping, unchanged.
    if exit_code_scheme == "severity" and sev_config is not None:
        from .reporter import _build_severity_json

        # §6.4 cross-command parity, and the reason this block is not
        # optional: under the severity scheme a *compatible* diff can exit
        # non-zero (`severity.addition: error` on an additions-only diff
        # exits 1), and without the gate block the report said `COMPATIBLE`
        # with exit 1 and no stated cause -- indistinguishable from ADR-049's
        # orthogonal contract-coverage 1 (Codex review). Built by
        # `reporter._build_severity_json` via the shared
        # `gate_decision_for_result` chokepoint (ADR-061 D9), so the two
        # commands' gate receipts are comparable field-by-field.
        computed_gate = gate_decision_for_result(diff, sev_config)
        assert computed_gate is not None  # sev_config is not None here
        gate = _build_severity_json(
            list(diff.changes),
            sev_config,
            gate=computed_gate,
            policy=diff.policy,
            kind_sets=diff._effective_kind_sets(),
            policy_file=diff.policy_file,
        )
        summary["severity"] = gate
        _add_severity_blocking_compatible_findings(
            summary, diff, gate, max_findings=max_findings
        )
        # Taken *off the emitted block* rather than computed alongside it:
        # `_build_severity_json` routes through `severity.compute_gate_decision`,
        # whose whole purpose is that an exit code and the categories blamed
        # for it cannot disagree (see its docstring -- it exists because two
        # independently-computed values did drift, twice). Calling
        # `compute_exit_code` separately here would reintroduce exactly that
        # second computation. It is also what lets `_emit_scan_report` and
        # `render_baseline_lines` recover this run's *pre-coverage* base from
        # the summary alone, instead of re-deriving a verdict-based one that
        # is wrong under this scheme (Codex review).
        gate_exit = gate["exit_code"]
        assert isinstance(gate_exit, int)  # compute_gate_decision.exit_code
        base_exit = gate_exit
    else:
        base_exit = _verdict_exit_code(diff.verdict)
    # ADR-049 §7/§6.4: the coverage axis is orthogonal to the verdict/severity
    # exit code and is folded identically here and in `compare`. Parity is
    # the point -- a ledger that gated one command and not the other would be
    # exactly the cross-command divergence §6.4's Gate exists to catch.
    exit_code = fold_coverage_exit(base_exit, diff)
    # P0.4: the analysis-assurance axis, folded the same `max` way and for
    # the same reason -- `compare`'s own `_exit_with_severity_or_verdict`
    # folds both immediately in sequence so a caller cannot pick up one
    # orthogonal axis and forget the other. `0` contribution, and this is a
    # pure no-op, whenever the flag was not passed (default False).
    from .workflows.gate import (
        assurance_floor_diagnostic,
        fold_analysis_assurance_exit,
    )

    # `compare`'s own `_exit_with_severity_or_verdict` echoes this same
    # diagnostic to stderr unconditionally (Codex review, PR #780: without
    # it, `action/run.sh`'s `_assurance_gated()` has nothing to grep for on
    # the `scan` path -- it only reads stderr, since unlike the coverage
    # ledger the JSON `analysis_assurance` block is not self-describing
    # about whether `--require-complete-analysis` was even passed this run).
    diagnostic = assurance_floor_diagnostic(
        diff, require_complete=require_complete_analysis, base_exit=exit_code
    )
    if diagnostic is not None:
        click.echo(diagnostic, err=True)
    exit_code = fold_analysis_assurance_exit(
        exit_code, diff, require_complete=require_complete_analysis
    )
    return verdict, exit_code, summary
