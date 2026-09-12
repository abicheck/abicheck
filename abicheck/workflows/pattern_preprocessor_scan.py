# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The lexical pattern pre-scan + preprocessor pre-scan, folded onto
``compare()``'s own pipeline (ADR-068 D3/D4/D5; ``docs/contribute/plans/
one-comparison-product.md`` §3 rows #6/#8, Phase 2b).

Before this module existed, ``buildsource.pattern_facts.find_pattern_facts`` and
``buildsource.preprocessor_facts.collect_preprocessor_facts`` had exactly one
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
useful analysis"; ``preprocessor_facts.py``'s own
``ABICHECK_PREPROCESSOR_SCAN=0`` kill switch, read inside
``collect_preprocessor_facts`` itself, still applies unchanged).

**Why this is a report field, not a set of ``ChangeKind`` findings.**
Unlike the six migrated cross-source checks, neither primitive here ever
produces a verdict-bearing finding: both are explicitly documented as
"advisory facts and escalation triggers only" (``pattern_facts.py``) /
"advisory facts... never a verdict on their own" (``preprocessor_facts.py``),
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
  from an embedded ``build_source.build_evidence`` (ADR-029 L3), if any.

- **Preprocessor-scan build context** (only under a licence -- ``clang -E``
  resolves ``#include``s against the *current* filesystem, so an unlicensed
  side is never probed): the same embedded
  ``build_source.build_evidence``, verbatim -- ``None`` when the snapshot
  carries no L3 evidence, which ``collect_preprocessor_facts`` already reports
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

**The source-read licence (the P1 fix).** Those roots are *paths a snapshot
recorded*, and until this module gained :func:`snapshot_source_licence` it
simply ``read_text()``-ed them. For a stored OLD snapshot that meant the
historical side was re-derived from whatever lives at the same path on
today's runner -- a different checkout, a since-edited header, or an
unrelated file that happens to share the path. The contract now, stated in
``buildsource/source_inputs.py`` and enforced here:

    **Comparing stored snapshots uses stored facts. A path recorded in a
    snapshot is provenance, not a licence to re-read the current filesystem
    for historical facts.**

A side is read only under a :class:`~abicheck.buildsource.source_inputs.
SourceReadLicence`: granted when the snapshot came from a live extraction in
this run (``AbiSnapshot.live_source_evidence``, a runtime-only flag the
storage codec never writes, so a loaded snapshot cannot claim it), or when
the caller supplies an explicit, provenance-verified one. Otherwise nothing
is stat'd or opened for that side and the result says the historical
evaluation was **not possible** -- every identity folds to ``not_evaluated``
rather than being characterised against a same-looking path on the current
runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..buildsource.pattern_facts import PatternFactsResult, find_pattern_facts
from ..buildsource.preprocessor_facts import (
    PreprocessorFactsResult,
    collect_preprocessor_facts,
)
from ..buildsource.preprocessor_probe_families import HEADER_PROBES, MACRO_PROBES
from ..buildsource.source_inputs import (
    WITHHELD_FOR_STORED_SNAPSHOT,
    SourceReadLicence,
    extraction_read_source_inputs,
)
from ..model import AbiSnapshot, ScopeOrigin
from ..policy.evidence_status import CrossSourceEvolution

#: Schema version for the ``pattern_preprocessor_scan`` report block.
#: Independent of every other schema version in this codebase (see
#: ``buildsource/CLAUDE.md`` "Versioning").
PATTERN_PREPROCESSOR_SCAN_VERSION: int = 1


#: The three checks this module folds, each with its own evidence and so its
#: own sufficiency answer (requirement: sufficiency is answered per
#: check-or-area, never by one global ``files_scanned > 0``).
CHECK_PATTERN_ESCALATION = "pattern_escalation"
CHECK_MACRO_DIVERGENCE = "macro_divergence"
CHECK_HEADER_LEAK = "header_leak"


@dataclass(frozen=True)
class Sufficiency:
    """Whether one side's evidence for one check supports an *absence* claim.

    ``established`` false does not mean "nothing was found" -- it means the
    question could not be answered, and :attr:`reason` says why (no licence to
    read a stored snapshot's paths, a declared root that is gone, a clang
    probe that failed, a probe cap that truncated the unit set).
    """

    established: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"established": self.established, "reason": self.reason}


def grant_live_source_licence(snapshot: AbiSnapshot) -> AbiSnapshot:
    """Stamp the source-read licence on a snapshot a front end extracted itself.

    ``service.run_dump`` grants it for everything that funnels through it, but a
    front end that calls ``dumper.dump`` directly -- the ABICC-compatible CLI
    does, deliberately, to skip ``run_dump``'s dependency-scope wrapper -- has to
    grant it here, or its genuinely live comparison reports the source-derived
    facts as withheld for a stored snapshot (Codex review).

    Lives in this layer because ADR-061 forbids a ``frontends`` module importing
    ``extract``: the licence *policy* for a snapshot belongs to the workflow that
    consumes it, and a front end asks rather than reaching past it. Conditional
    on the same predicate as every other grant, so a headerless/DWARF-only
    descriptor dump is still correctly denied.
    """
    snapshot.live_source_evidence = extraction_read_source_inputs(snapshot)
    return snapshot


def snapshot_source_licence(
    snapshot: AbiSnapshot, override: SourceReadLicence | None = None
) -> SourceReadLicence:
    """The licence under which *snapshot*'s recorded source paths may be read.

    Deny-by-default. ``override`` is the "explicitly supplied,
    provenance-verified context" escape hatch: a caller that has independently
    established that the recorded tree is the one on disk (a CI job that just
    checked the baseline's own commit out, a test harness) passes
    :meth:`SourceReadLicence.verified_context`. Everything else gets a licence
    only from ``AbiSnapshot.live_source_evidence`` -- set by ``dumper.dump``
    for an extraction performed in this run, never written by the storage
    codec, so a snapshot loaded from disk cannot grant itself one.
    """
    if override is not None:
        return override
    if getattr(snapshot, "live_source_evidence", False):
        return SourceReadLicence.live_extraction()
    return WITHHELD_FOR_STORED_SNAPSHOT


@dataclass(frozen=True)
class PatternPreprocessorScanResult:
    """The folded, per-side pattern + preprocessor pre-scan outcome.

    ``pattern_old``/``pattern_new`` and ``preprocessor_old``/
    ``preprocessor_new`` are each side's raw
    ``PatternFactsResult.to_dict()``/``PreprocessorFactsResult.to_dict()`` --
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
    #: Per-check, per-side sufficiency: ``{check: {"old": Sufficiency,
    #: "new": Sufficiency}}``. There is deliberately no single global
    #: "the scan was complete" flag -- the three checks answer to different
    #: evidence (a lexical file set, per-TU macro probes, per-header include
    #: probes), so one aggregate would report the weakest as the verdict for
    #: all three. See :func:`_check_coverage`.
    coverage: dict[str, dict[str, Sufficiency]] = field(default_factory=dict)

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
            "coverage": {
                check: {side: suf.to_dict() for side, suf in sides.items()}
                for check, sides in self.coverage.items()
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


def _run_pattern_scan(
    snapshot: AbiSnapshot, licence: SourceReadLicence
) -> PatternFactsResult:
    """Run the lexical pre-scan for one side, under *licence*.

    Without a licence ``find_pattern_facts`` stats nothing and opens nothing:
    the roots are accounted for as ``not_licensed`` and the result reports the
    scan as not possible (see :mod:`abicheck.buildsource.source_inputs`).
    """
    return find_pattern_facts(_pattern_scan_roots(snapshot), licence=licence)


def _run_preprocessor_scan_for(
    snapshot: AbiSnapshot, licence: SourceReadLicence
) -> PreprocessorFactsResult:
    """Run the S2 preprocessor pre-scan for one side, under *licence*.

    ``clang -E`` resolves the recorded compile units' ``#include``s against the
    filesystem it is run on, so it is exactly as unlicensed as the lexical
    scan for a stored snapshot -- and worse, since an include that resolves
    differently today produces a *different macro value*, not merely a missing
    file. An unlicensed side is therefore never probed at all; it returns the
    primitive's own honest ``ran=False`` shape with the licence's reason.
    """
    if not licence.permitted:
        return PreprocessorFactsResult(
            ran=False,
            skipped_reason=(f"S2 preprocessor pre-scan not possible: {licence.reason}"),
        )
    build_source = snapshot.build_source
    build = build_source.build_evidence if build_source is not None else None
    public_headers = sorted(_declared_source_headers(snapshot, public_only=True))
    return collect_preprocessor_facts(build, public_headers)


def _fold_evolution(
    *,
    old: Sufficiency,
    new: Sufficiency,
    old_keys: set[str],
    new_keys: set[str],
) -> dict[str, str]:
    """Fold one check's OLD/NEW identity sets into an evolution map.

    The four ADR-068 D3 states are decided **per identity**, from what is
    *established* for that identity on each side -- not from one global
    "the scan was complete" flag:

    - **Presence is established by observation.** A construct the OLD side
      actually flagged is present in OLD whatever else that side failed to
      read; an incomplete scan cannot un-see a hit.
    - **Absence is established only by sufficiency.** "This side does not have
      it" is a claim about everything that side was supposed to look at, so it
      needs :attr:`Sufficiency.established` for that side and that check.

    That distinction is the P1 fix. ``INTRODUCED`` asserts absence in OLD, so
    it now requires OLD's coverage *for that identity* to be established --
    a missing, unreadable, or unlicensed OLD input can no longer let a
    long-standing construct read as newly introduced. ``RESOLVED``
    symmetrically asserts absence in NEW. ``PERSISTENT`` asserts presence on
    both sides, which both observations already establish, so it needs no
    sufficiency at all -- the strongest statement this fold can make from the
    weakest evidence, and the one it is always safe to make. Anything not
    established folds to ``NOT_EVALUATED``: an honest "could not tell", never
    a silent drop (an identity absent on both sides never enters the union and
    so never appears at all).
    """
    result: dict[str, str] = {}
    for key in sorted(old_keys | new_keys):
        old_hit = key in old_keys
        new_hit = key in new_keys
        # Presence: observed. Absence: needs this side's sufficiency.
        old_known = old_hit or old.established
        new_known = new_hit or new.established
        if not (old_known and new_known):
            evolution = CrossSourceEvolution.NOT_EVALUATED
        elif old_hit and new_hit:
            evolution = CrossSourceEvolution.PERSISTENT
        elif new_hit:
            evolution = CrossSourceEvolution.INTRODUCED
        else:
            evolution = CrossSourceEvolution.RESOLVED
        result[key] = evolution.value
    return result


def _pattern_sufficiency(result: PatternFactsResult) -> Sufficiency:
    """Sufficiency of one side's lexical pattern scan.

    Delegates to the expected-input set rather than to ``files_scanned > 0 and
    files_skipped == 0``. The old signal was computed from what the discovery
    walk *found*: a declared root that no longer existed was silently skipped,
    contributing to neither tally, so a side whose headers had all been
    relocated could still report full coverage off one surviving file. The
    expected-input set accounts for every declared root, so ``missing``,
    ``unreadable``, ``unsupported`` and ``not_licensed`` are each a gap that
    keeps the side insufficient (see :class:`~abicheck.buildsource.
    source_inputs.SourceInputSet`).
    """
    return Sufficiency(
        established=result.sufficient, reason=result.insufficiency_reason
    )


def _probe_family_gaps(result: PreprocessorFactsResult, family: str) -> str:
    """Coverage gaps for one **probe family**, or ``""`` when it is complete.

    Answered from that family's own tallies, never from the run-wide
    ``attempted``/``succeeded``/``probes_truncated`` aggregates. Two reasons,
    both found by review:

    - The aggregates mix the two families, so a truncated compile-unit set
      marked the *header-leak* check insufficient even when every public
      header was probed successfully, and vice versa. Answering sufficiency
      per check is the point; sharing one predicate quietly undid it.
    - ``probe_tallies.attempted`` is what distinguishes "probed and found nothing"
      from "never probed". A successful ``-E -dM`` probe of a unit defining
      none of the curated ABI macros contributes no entry to ``abi_macros``,
      so ``tus_scanned`` is ``0`` for an ordinary build with no ABI-toggle
      macros. Gating on ``tus_scanned`` therefore reported "no translation
      unit was probed" for a fully-covered run, and its evolution could never
      leave ``not_evaluated``.
    """
    if not result.ran:
        return result.skipped_reason or "S2 preprocessor pre-scan did not run"
    tallies = result.probe_tallies
    attempted = tallies.attempted.get(family, 0)
    succeeded = tallies.succeeded.get(family, 0)
    truncated = tallies.truncated.get(family, 0)
    if attempted == 0:
        # Nothing of this family was ever run: no compile unit to probe, or no
        # public header declared. Honest "not established", not a failure.
        return f"no {family} probe was run"
    if succeeded == 0:
        return f"every {family} probe failed"
    if succeeded != attempted:
        return f"{attempted - succeeded} of {attempted} {family} probes failed"
    if truncated:
        return (
            f"{truncated} {family} probe(s) truncated by "
            "ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES"
        )
    return ""


def _macro_divergence_sufficiency(result: PreprocessorFactsResult) -> Sufficiency:
    """Sufficiency for the macro-divergence check: its own probes only."""
    gaps = _probe_family_gaps(result, MACRO_PROBES)
    return Sufficiency(established=not gaps, reason=gaps)


def _header_leak_sufficiency(result: PreprocessorFactsResult) -> Sufficiency:
    """Sufficiency for the private-header-leak check: its own probes only.

    Splitting this from :func:`_macro_divergence_sufficiency` is the point of
    answering sufficiency per check rather than per run: a build with compile
    units but no declared public headers fully establishes macro divergence
    while establishing nothing about leaks.
    """
    gaps = _probe_family_gaps(result, HEADER_PROBES)
    return Sufficiency(established=not gaps, reason=gaps)


def compute_pattern_preprocessor_scan(
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    old_source_licence: SourceReadLicence | None = None,
    new_source_licence: SourceReadLicence | None = None,
) -> PatternPreprocessorScanResult:
    """Run the pattern + preprocessor pre-scans on *old*/*new* independently
    and fold each into an evolution-stated summary (see module docstring).

    Each side is read only under its own licence, resolved by
    :func:`snapshot_source_licence`; ``old_source_licence``/
    ``new_source_licence`` are the explicit, provenance-verified override. A
    side without one is not read at all and folds to ``not_evaluated``.
    """
    old_licence = snapshot_source_licence(old, old_source_licence)
    new_licence = snapshot_source_licence(new, new_source_licence)

    old_pattern = _run_pattern_scan(old, old_licence)
    new_pattern = _run_pattern_scan(new, new_licence)
    pattern_old_suf = _pattern_sufficiency(old_pattern)
    pattern_new_suf = _pattern_sufficiency(new_pattern)
    pattern_evolution = _fold_evolution(
        old=pattern_old_suf,
        new=pattern_new_suf,
        old_keys={f.kind.value for f in old_pattern.facts if f.escalates},
        new_keys={f.kind.value for f in new_pattern.facts if f.escalates},
    )

    old_preproc = _run_preprocessor_scan_for(old, old_licence)
    new_preproc = _run_preprocessor_scan_for(new, new_licence)
    macro_old_suf = _macro_divergence_sufficiency(old_preproc)
    macro_new_suf = _macro_divergence_sufficiency(new_preproc)
    macro_evolution = _fold_evolution(
        old=macro_old_suf,
        new=macro_new_suf,
        old_keys={d.macro for d in old_preproc.divergences},
        new_keys={d.macro for d in new_preproc.divergences},
    )
    leak_old_suf = _header_leak_sufficiency(old_preproc)
    leak_new_suf = _header_leak_sufficiency(new_preproc)
    leak_evolution = _fold_evolution(
        old=leak_old_suf,
        new=leak_new_suf,
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
        coverage={
            CHECK_PATTERN_ESCALATION: {"old": pattern_old_suf, "new": pattern_new_suf},
            CHECK_MACRO_DIVERGENCE: {"old": macro_old_suf, "new": macro_new_suf},
            CHECK_HEADER_LEAK: {"old": leak_old_suf, "new": leak_new_suf},
        },
    )
