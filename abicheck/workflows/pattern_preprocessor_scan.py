# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The lexical pattern pre-scan + preprocessor pre-scan, folded onto
``compare()``'s own pipeline (ADR-068 D3/D4/D5; ``docs/contribute/plans/
one-comparison-product.md`` §3 rows #6/#8, Phase 2b).

Before this module existed, ``buildsource.pattern_scan.scan_files`` and
``buildsource.preprocessor_scan.run_preprocessor_scan`` had exactly one
production caller anywhere under ``abicheck/``: ``scan_engine.py`` (ADR-068
§1). This module gives them a second, ``compare()``-reachable caller,
mirroring ``workflows/cross_source_evolution.py``'s own shape for the
cross-source checks: run the primitive independently on OLD and NEW, then
fold the two one-sided results into a single, evolution-stated summary
(:class:`~abicheck.checker_policy.CrossSourceEvolution` -- the same enum,
whose own docstring already names "``run_crosschecks`` and siblings" as its
scope, not just cross-source checks specifically). ``checker.compare()``
runs this automatically, on every invocation with resolvable evidence, via
its own ``pattern_preprocessor_scan`` keyword (default ``True``) -- no CLI
flag exposes a way to disable it (D5 rejects "a flag that merely enables
useful analysis"; ``preprocessor_scan.py``'s own
``ABICHECK_PREPROCESSOR_SCAN=0`` kill switch, read inside
``run_preprocessor_scan`` itself, still applies unchanged).

**Why this is a report field, not a set of ``ChangeKind`` findings.**
Unlike the six migrated cross-source checks, neither primitive here ever
produces a verdict-bearing finding: both are explicitly documented as
"advisory facts and escalation triggers only" (``pattern_scan.py``) /
"advisory facts... never a verdict on their own" (``preprocessor_scan.py``),
even under ``scan`` today (``scan``'s own JSON report carries them as plain
``pattern_scan``/``preprocessor_scan`` top-level keys, never folded into
``diff.findings``). So the compare-side migration keeps that same shape:
:func:`compute_pattern_preprocessor_scan` returns a
:class:`PatternPreprocessorScanResult` that ``checker.compare()`` attaches
to ``DiffResult.pattern_preprocessor_scan`` -- read-only report data that
never reaches the verdict, severity, or exit code, exactly like
``DiffResult.contract_context``.

**Evidence, at compare-time.** Unlike ``scan``, which has direct ``-H``/
``--sources`` CLI inputs to hand the primitives, ``compare()`` only ever
sees the two already-built :class:`~abicheck.model.AbiSnapshot` objects. So
the file/build evidence this module hands to each primitive is derived
entirely from what each snapshot already recorded:

- **Pattern-scan roots** (:func:`_pattern_scan_roots`): every declared
  entity's own ``source_header`` (functions/variables/records/enums --
  ADR-015 provenance, schema v6) plus every compile unit's ``source`` file
  from an embedded ``build_source.build_evidence`` (ADR-029 L3), if any. A
  path that no longer exists on disk (a stored baseline snapshot dumped
  elsewhere, or one the working tree has since restructured) is silently
  skipped by ``scan_files``/``iter_source_files`` itself, not raised.
- **Preprocessor-scan build context**: the same embedded
  ``build_source.build_evidence``, verbatim -- ``None`` when the snapshot
  carries no L3 evidence, which ``run_preprocessor_scan`` already reports
  as its own honest ``ran=False``/``skipped_reason`` coverage row rather
  than a fabricated clean scan.
- **Preprocessor-scan public headers**: the subset of the same declared
  ``source_header`` set whose ``origin`` resolved to
  :class:`~abicheck.model.ScopeOrigin.PUBLIC_HEADER` (the same provenance
  signal ``crosscheck.py``'s own checks already gate on).

Both are "reuse whatever evidence produced this snapshot" derivations, not
a second collection pass -- this module runs no dumper and reads no CLI
input directly, keeping the ADR-061 ``workflows -> model, storage, extract,
compare, policy`` import direction intact (``buildsource`` is ``extract``-
classified).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..buildsource.pattern_scan import PatternScanResult, scan_files
from ..buildsource.preprocessor_scan import (
    PreprocessorScanResult,
    run_preprocessor_scan,
)
from ..checker_policy import CrossSourceEvolution
from ..model import AbiSnapshot, ScopeOrigin

#: Schema version for the ``pattern_preprocessor_scan`` report block.
#: Independent of every other schema version in this codebase (see
#: ``buildsource/CLAUDE.md`` "Versioning").
PATTERN_PREPROCESSOR_SCAN_VERSION: int = 1


@dataclass(frozen=True)
class PatternPreprocessorScanResult:
    """The folded, per-side pattern + preprocessor pre-scan outcome.

    ``pattern_old``/``pattern_new`` and ``preprocessor_old``/
    ``preprocessor_new`` are each side's raw
    ``PatternScanResult.to_dict()``/``PreprocessorScanResult.to_dict()`` --
    the exact same shape ``scan``'s own report carries under its
    ``pattern_scan``/``preprocessor_scan`` keys, so a reader already
    familiar with one recognizes the other. The ``*_evolution`` maps are
    this module's own addition: one :class:`~abicheck.checker_policy.
    CrossSourceEvolution` value per identity (an escalating pattern-scan
    ``PatternKind``, a diverging preprocessor macro name, or a
    ``"public|leaked"`` header-leak pair), keyed the way
    :func:`_fold_evolution` describes.
    """

    version: int = PATTERN_PREPROCESSOR_SCAN_VERSION
    pattern_old: dict[str, Any] = field(default_factory=dict)
    pattern_new: dict[str, Any] = field(default_factory=dict)
    pattern_escalation_evolution: dict[str, str] = field(default_factory=dict)
    preprocessor_old: dict[str, Any] = field(default_factory=dict)
    preprocessor_new: dict[str, Any] = field(default_factory=dict)
    macro_divergence_evolution: dict[str, str] = field(default_factory=dict)
    header_leak_evolution: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "pattern": {
                "old": self.pattern_old,
                "new": self.pattern_new,
                "escalation_evolution": dict(self.pattern_escalation_evolution),
            },
            "preprocessor": {
                "old": self.preprocessor_old,
                "new": self.preprocessor_new,
                "macro_divergence_evolution": dict(self.macro_divergence_evolution),
                "header_leak_evolution": dict(self.header_leak_evolution),
            },
        }


def _declared_source_headers(
    snapshot: AbiSnapshot, *, public_only: bool = False
) -> set[str]:
    """Every declared entity's own ``source_header`` (ADR-015 provenance).

    ``public_only`` restricts to entities whose ``origin`` resolved to
    :data:`~abicheck.model.ScopeOrigin.PUBLIC_HEADER` -- the same public/
    internal boundary ``crosscheck.py``'s checks already gate on.
    """
    headers: set[str] = set()
    for fn in snapshot.functions:
        if fn.source_header and (
            not public_only or fn.origin == ScopeOrigin.PUBLIC_HEADER
        ):
            headers.add(fn.source_header)
    for var in snapshot.variables:
        if var.source_header and (
            not public_only or var.origin == ScopeOrigin.PUBLIC_HEADER
        ):
            headers.add(var.source_header)
    for rec in snapshot.types:
        if rec.source_header and (
            not public_only or rec.origin == ScopeOrigin.PUBLIC_HEADER
        ):
            headers.add(rec.source_header)
    for enum in snapshot.enums:
        if enum.source_header and (
            not public_only or enum.origin == ScopeOrigin.PUBLIC_HEADER
        ):
            headers.add(enum.source_header)
    return headers


def _pattern_scan_roots(snapshot: AbiSnapshot) -> list[str]:
    """Candidate file roots for the lexical pre-scan (see module docstring)."""
    roots = _declared_source_headers(snapshot)
    build_source = snapshot.build_source
    if build_source is not None and build_source.build_evidence is not None:
        for cu in build_source.build_evidence.compile_units:
            if cu.source:
                roots.add(cu.source)
    return sorted(roots)


def _run_pattern_scan(snapshot: AbiSnapshot) -> PatternScanResult:
    return scan_files(_pattern_scan_roots(snapshot))


def _run_preprocessor_scan_for(snapshot: AbiSnapshot) -> PreprocessorScanResult:
    build_source = snapshot.build_source
    build = build_source.build_evidence if build_source is not None else None
    public_headers = sorted(_declared_source_headers(snapshot, public_only=True))
    return run_preprocessor_scan(build, public_headers)


def _fold_evolution(
    *,
    old_evaluated: bool,
    new_evaluated: bool,
    old_keys: set[str],
    new_keys: set[str],
) -> dict[str, str]:
    """Fold one primitive's OLD/NEW identity sets into an evolution map.

    Per-identity logic (ADR-068 D3's four states): evaluated on both sides
    decides ``PERSISTENT``/``INTRODUCED``/``RESOLVED``; whenever *either*
    side is incomplete, every identity either side flagged folds to
    ``NOT_EVALUATED`` -- the correctness crux that keeps a pre-existing
    construct from reading as newly introduced (or resolved) merely
    because one side (e.g. a snapshot with no embedded build evidence)
    lacked the evidence to confirm or deny it.

    CodeRabbit review, fresh evidence: an earlier revision folded
    ``NOT_EVALUATED`` only when the *hit itself* was on the evaluated
    side (``new_hit and not old_evaluated`` / ``old_hit and not
    new_evaluated``) -- the reverse orientation (a hit only on the
    *incomplete* side, with the evaluated side silent) fell through to a
    bare ``continue`` and the identity vanished from the result entirely,
    silently discarding evidence instead of reporting the honest
    "can't tell" state. Since incompleteness on either side already
    means neither PERSISTENT/INTRODUCED/RESOLVED can be trusted for any
    identity in the union, this is now unconditional: every key in
    ``old_keys | new_keys`` folds to ``NOT_EVALUATED`` whenever the two
    sides aren't both fully evaluated, regardless of which side (or
    both) actually flagged it. An identity absent on both sides never
    appears in the result at all, since it never enters the union.
    """
    result: dict[str, str] = {}
    for key in sorted(old_keys | new_keys):
        if old_evaluated and new_evaluated:
            old_hit = key in old_keys
            new_hit = key in new_keys
            if old_hit and new_hit:
                evolution = CrossSourceEvolution.PERSISTENT
            elif new_hit:
                evolution = CrossSourceEvolution.INTRODUCED
            else:
                evolution = CrossSourceEvolution.RESOLVED
        else:
            evolution = CrossSourceEvolution.NOT_EVALUATED
        result[key] = evolution.value
    return result


def _pattern_scan_fully_covered(result: PatternScanResult) -> bool:
    """True only when *result* scanned at least one file and skipped none.

    ``files_scanned > 0`` alone (the pre-CodeRabbit-review check) also holds
    when some files were skipped as unreadable -- a real, if partial, scan
    ran, but a pattern kind absent from that partial result could simply be
    hiding in the unscanned files, not genuinely absent. Folding that as
    evaluated let ``pattern_escalation_evolution`` report ``introduced``/
    ``resolved`` from incomplete evidence; requiring zero skips makes an
    incomplete side fold as ``not_evaluated`` instead, per
    :func:`_fold_evolution`'s own completeness contract.
    """
    return result.files_scanned > 0 and result.files_skipped == 0


def _preprocessor_scan_fully_covered(result: PreprocessorScanResult) -> bool:
    """True only when *result* ran with every probe attempted, succeeding,
    and none truncated by the probe-count cap.

    ``ran and not all_failed`` alone (the pre-CodeRabbit-review check) also
    holds when some -- but not all -- clang invocations failed, or when the
    probe cap (``ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES``) truncated the unit
    set: real coverage gaps a divergence/leak could simply be missing from,
    not genuinely absent on that side. Requiring full success with no
    truncation makes a partially-covered side fold as ``not_evaluated``
    instead of ``introduced``/``resolved``, matching
    :func:`_pattern_scan_fully_covered`'s same completeness contract for the
    sibling primitive.
    """
    return (
        result.ran
        and not result.all_failed
        and result.succeeded == result.attempted
        and result.probes_truncated == 0
    )


def compute_pattern_preprocessor_scan(
    old: AbiSnapshot, new: AbiSnapshot
) -> PatternPreprocessorScanResult:
    """Run the pattern + preprocessor pre-scans on *old*/*new* independently
    and fold each into an evolution-stated summary (see module docstring)."""
    old_pattern = _run_pattern_scan(old)
    new_pattern = _run_pattern_scan(new)
    pattern_evaluated_old = _pattern_scan_fully_covered(old_pattern)
    pattern_evaluated_new = _pattern_scan_fully_covered(new_pattern)
    pattern_evolution = _fold_evolution(
        old_evaluated=pattern_evaluated_old,
        new_evaluated=pattern_evaluated_new,
        old_keys={f.kind.value for f in old_pattern.facts if f.escalates},
        new_keys={f.kind.value for f in new_pattern.facts if f.escalates},
    )

    old_preproc = _run_preprocessor_scan_for(old)
    new_preproc = _run_preprocessor_scan_for(new)
    preproc_evaluated_old = _preprocessor_scan_fully_covered(old_preproc)
    preproc_evaluated_new = _preprocessor_scan_fully_covered(new_preproc)
    macro_evolution = _fold_evolution(
        old_evaluated=preproc_evaluated_old,
        new_evaluated=preproc_evaluated_new,
        old_keys={d.macro for d in old_preproc.divergences},
        new_keys={d.macro for d in new_preproc.divergences},
    )
    leak_evolution = _fold_evolution(
        old_evaluated=preproc_evaluated_old,
        new_evaluated=preproc_evaluated_new,
        old_keys={
            f"{leak.public_header}|{leak.leaked_header}" for leak in old_preproc.leaks
        },
        new_keys={
            f"{leak.public_header}|{leak.leaked_header}" for leak in new_preproc.leaks
        },
    )

    return PatternPreprocessorScanResult(
        pattern_old=old_pattern.to_dict(),
        pattern_new=new_pattern.to_dict(),
        pattern_escalation_evolution=pattern_evolution,
        preprocessor_old=old_preproc.to_dict(),
        preprocessor_new=new_preproc.to_dict(),
        macro_divergence_evolution=macro_evolution,
        header_leak_evolution=leak_evolution,
    )
