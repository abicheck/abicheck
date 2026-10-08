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

"""Consumer-scoped comparison — ADR-005 application/plugin compatibility.

Answers "will my application (or plugin host) still work with the new
library version?" by intersecting a consumer's requirements with an
already-computed library diff. This module is the workflow glue:
``extract.consumer_imports``/``extract.library_export_facts`` read the facts,
``policy.consumer_requirements`` evaluates them, and this module adds the
suppressible consumer-overlay findings, the disposition-ledger bookkeeping
and the result types. ``abicheck.appcompat`` is the documented public import
path for the same names.

See docs/contribute/adr/005-application-compat-check.md for the design.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..appcompat_consumer_impact import (
    attach_consumer_impact,
    consumer_impact_explanations,
    enrich_covered_changes,
)
from ..checker_types import DiffResult
from ..diff_helpers import make_change
from ..extract.consumer_imports import read_consumer_imports
from ..extract.library_export_facts import (
    dlsym_export_names,
    read_library_export_facts,
)
from ..impact.engine import assess_change
from ..model import AbiSnapshot
from ..model.availability import FactStatus
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.consumer_requirements import (
    AppRequirements,
    ConsumerImportFacts,
    LibraryExportFacts,
)
from ..model.consumer_spec import (
    ConsumerAppInput,
    ConsumerSpec,
    ConsumerUnreadableError,
    as_consumer_spec,
    verify_digest,
)
from ..model.evidence_status import ReachabilityState
from ..policy.classification import Verdict
from ..policy.consumer_requirements import (
    appcompat_verdict,
    evaluate_consumer_requirements,
    missing_app_versions,
    partition_app_changes,
    symbol_coverage,
    uncovered_missing_symbols,
)
from ..policy.disposition_close import (
    ledger_for,
    record_and_maybe_suppress_overlay,
)

__all__ = [
    "AppCompatResult",
    "PluginHostContractResult",
    "check_against",
    "check_against_facts",
    "parse_app_requirements",
    "read_consumer_facts",
    "scope_diff_to_app",
    "scope_diff_to_consumer_facts",
    "scope_diff_to_required_symbols",
    # Re-exported for frontends, which may not import `policy` directly.
    "uncovered_missing_symbols",
]

if TYPE_CHECKING:
    from ..policy.disposition_ledger import DispositionLedger
    from ..policy_file import PolicyFile
    from ..suppression import SuppressionList


@dataclass
class AppCompatResult:
    """Result of checking app compatibility with a library update."""

    app_path: str
    old_lib_path: str
    new_lib_path: str

    # App's requirements
    required_symbols: set[str] = field(default_factory=set)
    required_symbol_count: int = 0

    # Filtered results
    breaking_for_app: list[Change] = field(default_factory=list)
    irrelevant_for_app: list[Change] = field(default_factory=list)
    missing_symbols: list[str] = field(default_factory=list)
    missing_versions: list[str] = field(default_factory=list)

    # Full library diff (for reference)
    full_diff: DiffResult | None = None

    # App-specific verdict
    verdict: Verdict = Verdict.COMPATIBLE

    # Coverage
    symbol_coverage: float = 100.0  # % of app's required symbols present in new lib

    # Consumer-specification provenance (Workstream D-S1: a ConsumerSpec's
    # optional identity, e.g. via --used-by-manifest), reported purely as
    # provenance -- see model.consumer_spec.
    platform: str | None = None
    profile: str | None = None
    provider_baseline: str | None = None
    digest: str | None = None
    requirement: str = "required"

    # Set only for an ADVISORY consumer that could not be read (a REQUIRED
    # one raises instead); left at its NO_CHANGE/empty defaults and excluded
    # from a scoped gate's worst-wins (cli_helpers_compare._apply_used_by_scoping).
    unreadable: bool = False
    unreadable_reason: str | None = None


@dataclass
class PluginHostContractResult:
    """Result of checking a plugin upgrade against a host's load contract.

    The dlopen() failure mode is two-sided: a host resolves a fixed set of
    entry-point symbols (``dlsym``) from each plugin it loads. This is the
    plugin-load direction of :class:`AppCompatResult` — "does plugin v2 still
    satisfy host H's required entrypoints?" (ADR-005 / gap G5).
    """

    old_plugin: str
    new_plugin: str

    #: entry-point symbols the host resolves from the plugin (the contract).
    required_entrypoints: set[str] = field(default_factory=set)
    #: required entrypoints the *new* plugin no longer exports → host load break.
    missing_entrypoints: list[str] = field(default_factory=list)
    #: library diff changes that touch a required entrypoint.
    breaking_for_host: list[Change] = field(default_factory=list)
    #: full plugin v1→v2 diff (for reference / reporting).
    full_diff: DiffResult | None = None
    #: host-scoped verdict (BREAKING when an entrypoint is dropped/incompatible).
    verdict: Verdict = Verdict.COMPATIBLE
    #: % of the host's required entrypoints still provided by the new plugin.
    coverage: float = 100.0


def read_consumer_facts(spec: ConsumerSpec, library_name: str) -> ConsumerImportFacts:
    """Verify *spec*'s digest and read its imports from *library_name*.

    Raises :class:`ConsumerUnreadableError` (or its
    ``ConsumerDigestMismatchError`` subclass) when the digest mismatches, the
    binary format can't be detected, or the import table could not be read
    (a ``FAILED`` fact) -- the one point where an unreadable consumer fact
    becomes the caller's error. A failed read must not reach evaluation as
    an empty requirement set: that would report a consumer that "requires
    nothing" as compatible with 100% coverage (root ``AGENTS.md``: weaker
    evidence narrows conclusions, it never upgrades to a clean claim).
    """
    verify_digest(spec)
    consumer = read_consumer_imports(spec.path, library_name)
    if not consumer.is_readable or consumer.status is FactStatus.FAILED:
        raise ConsumerUnreadableError(consumer.failure_reason or str(spec.path))
    return consumer


def parse_app_requirements(
    app_path: ConsumerAppInput,
    library_name: str,
) -> AppRequirements:
    """Extract app's requirements for a specific library.

    *app_path* is a bare Path (ELF/PE/Mach-O), or a
    :class:`~abicheck.model.consumer_spec.ConsumerSpec` carrying one plus
    optional digest/platform/profile/provider-baseline provenance
    (Workstream D-S1) -- a supplied digest is verified against the real file
    first. *library_name* is the SONAME/DLL name/dylib path to filter by.

    Raises :class:`ConsumerUnreadableError` (or its
    ``ConsumerDigestMismatchError`` subclass) when the format can't be
    detected or the digest mismatches -- both subclass ``ValueError``, so an
    existing bare ``except ValueError``/``except Exception`` is unaffected.
    """
    return read_consumer_facts(as_consumer_spec(app_path), library_name).requirements


def scope_diff_to_app(
    diff: DiffResult,
    app_path: ConsumerAppInput,
    old_lib: Path | AbiSnapshot,
    new_lib: Path | AbiSnapshot,
    *,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
    suppression: SuppressionList | None = None,
    old_snapshot: AbiSnapshot | None = None,
) -> AppCompatResult:
    """Scope an already-computed library diff to one application's actual usage.

    This is the generic-scoped-comparison core ``compare --used-by`` calls
    (ADR-043): the full old/new library comparison runs exactly once (by the
    caller, typically ``compare``'s own pipeline); this function only parses
    the application's requirements and intersects them with that diff. It does
    NOT re-dump or re-compare the libraries — see :func:`check_appcompat` for
    the standalone (single-app, no pre-existing diff) convenience wrapper.

    *old_lib*/*new_lib* may be a real library binary path, or an already-
    loaded :class:`~abicheck.model.AbiSnapshot` (e.g. from a saved JSON dump)
    -- a snapshot's ``elf``/``pe``/``macho`` fields already carry the SONAME,
    export table, ELF version list, and PE ordinal table this function needs,
    so no re-parse of a real binary is required for those lookups. *app_path*
    is unaffected: the application itself always needs a real binary to read
    its DT_NEEDED/import table from.

    *suppression* (ADR-044 P2, Codex review): the same rule set already used
    to compute *diff* — evaluated again here because
    :data:`ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED` is synthesized fresh
    from ``missing_symbols`` *after* the pipeline's own suppression pass has
    already run, so it would otherwise be unsuppressible even by an exact
    ``symbol:``/``change_kind:`` rule that matches it precisely.

    *old_snapshot* (ADR-057) is used **only** to find the old library's L5
    source graph for the consumer-impact join, and only matters when *old_lib*
    is a real binary ``Path``: a caller that resolved OLD to both a path and a
    snapshot should pass the snapshot here so the join can explain *why* a
    consumer required a removed symbol. It never affects which symbols,
    exports, or versions are read — see
    :func:`~abicheck.appcompat_consumer_impact._library_source_graph`.

    *app_path* (Workstream D-S1) may be a bare :class:`~pathlib.Path` or a
    :class:`~abicheck.model.consumer_spec.ConsumerSpec` (digest/platform/
    profile/provider-baseline provenance, advisory/required). An unreadable
    REQUIRED consumer (the default) raises
    :class:`~abicheck.model.consumer_spec.ConsumerUnreadableError`, as an
    unreadable bare path always has; ADVISORY instead returns a
    ``NO_CHANGE``/``unreadable=True`` result, excluded from a scoped gate's
    worst-wins.
    """
    spec = as_consumer_spec(app_path)
    old_facts = read_library_export_facts(old_lib)
    try:
        consumer = read_consumer_facts(spec, old_facts.soname)
    except ConsumerUnreadableError as exc:
        if not spec.is_advisory:
            raise
        return AppCompatResult(
            app_path=str(spec.path),
            old_lib_path=old_facts.label,
            new_lib_path=str(new_lib) if isinstance(new_lib, Path) else new_lib.library,
            full_diff=diff,
            verdict=Verdict.NO_CHANGE,
            symbol_coverage=0.0,
            platform=spec.platform,
            profile=spec.profile,
            provider_baseline=spec.provider_baseline,
            digest=spec.digest,
            requirement=spec.requirement.value,
            unreadable=True,
            unreadable_reason=str(exc),
        )
    return scope_diff_to_consumer_facts(
        diff,
        consumer,
        old_facts,
        read_library_export_facts(new_lib),
        spec=spec,
        old_lib=old_lib,
        policy=policy,
        policy_file=policy_file,
        suppression=suppression,
        old_snapshot=old_snapshot,
    )


def scope_diff_to_consumer_facts(
    diff: DiffResult,
    consumer: ConsumerImportFacts,
    old_facts: LibraryExportFacts,
    new_facts: LibraryExportFacts,
    *,
    spec: ConsumerSpec | None = None,
    old_lib: Path | AbiSnapshot | None = None,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
    suppression: SuppressionList | None = None,
    old_snapshot: AbiSnapshot | None = None,
) -> AppCompatResult:
    """:func:`scope_diff_to_app` over already-read facts.

    *spec* supplies the result's provenance fields (default: a bare spec of
    ``consumer.path``); *old_lib*/*old_snapshot* are consulted only for the
    ADR-057 consumer-impact graph join.
    """
    spec = spec if spec is not None else as_consumer_spec(consumer.path)
    app_path = consumer.path
    evaluation = evaluate_consumer_requirements(
        consumer, old_facts, new_facts, diff.changes
    )
    app_reqs = evaluation.requirements
    missing_symbols = evaluation.missing_symbols
    breaking_for_app = list(evaluation.relevant)
    # ADR-067 C-S1: the consumer overlay is the one recording call site that
    # runs *after* `compare()` closed the ledger, so it resolves the diff's
    # own ledger once here. `ledger_for` deliberately never attaches on its
    # own -- a report projection must not mutate what it renders -- and the
    # attachment to *diff* is deferred to `_finalize_consumer_scope_diff`
    # (ADR-063 T10): every record below goes through the local
    # `overlay_ledger` reference.
    overlay_ledger = ledger_for(diff)
    suppressed_missing: set[str] = set()
    # ADR-044 P2 item 1: promote a missing symbol not already represented by a
    # library-diff Change (e.g. FUNC_REMOVED) into a first-class, suppressible
    # CONSUMER_REQUIRED_SYMBOL_REMOVED finding, scoped to the genuinely-
    # uncovered subset so a symbol already covered by a real diff Change is
    # never double-reported as both that Change and this overlay.
    uncovered = list(uncovered_missing_symbols(missing_symbols, breaking_for_app))
    # G29 Phase 4 (ADR-057): one joined consumer/source-graph walk, computed
    # before the loop so the restricted BFS runs once per comparison. Every
    # missing symbol, not just the uncovered ones: a removed export normally
    # produces its own FUNC_REMOVED, so scoping the walk to `uncovered` left
    # the common case with no explanation at all (ADR-057 D8, Codex review).
    joined_graph, consumer_impact = consumer_impact_explanations(
        app_path,
        app_reqs,
        old_lib if old_lib is not None else Path(old_facts.label),
        missing_symbols,
        old_snapshot,
    )
    for sym in uncovered:
        # public_reachable=True (Codex review, fresh evidence): this overlay
        # only ever exists because a real --used-by consumer binary's own
        # undefined-symbol requirement genuinely resolved to nothing in the
        # new library, so a broad namespace/source_location suppression
        # rule's default "unreachable-only" reachability must not read it as
        # unreachable.
        overlay_change = make_change(
            ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
            symbol=sym,
            name=app_path.name,
            public_reachable=True,
            reachability_kind="consumer_proven",
            reachability_state=ReachabilityState.PROVEN_REACHABLE,
        )
        # Attached *before* assess_change: the cached assessment is built from
        # these very fields, so enriching afterwards would leave the cache
        # describing the pre-enrichment change.
        explained = consumer_impact.get(sym)
        if explained is not None and joined_graph is not None:
            attach_consumer_impact(overlay_change, explained, joined_graph)
        # ADR-052 D2 follow-up: safe to cache now -- suppression evaluation
        # below is a pure read of the change's fields.
        overlay_change.impact_assessment = assess_change(overlay_change)
        # A suppressed overlay must also drop its raw string from
        # missing_symbols: that list independently forces Verdict.BREAKING and
        # feeds the scoped exit-code floor (Codex review, fresh evidence).
        # ADR-067 C-S2: the evaluate/record/overreach sequence is shared with
        # scope_diff_to_required_symbols.
        kept, overreach = record_and_maybe_suppress_overlay(
            overlay_ledger,
            overlay_change,
            diff,
            suppression=suppression,
        )
        if not kept:
            suppressed_missing.add(sym)
            continue
        breaking_for_app.append(overlay_change)
        if overreach is not None:
            breaking_for_app.append(overreach)
    # After the overlay loop: an overlay already carries its own explanation,
    # so only the shared library-diff findings are enriched here.
    if joined_graph is not None and consumer_impact:
        enrich_covered_changes(breaking_for_app, consumer_impact, joined_graph)
    if suppressed_missing:
        missing_symbols = [s for s in missing_symbols if s not in suppressed_missing]
    verdict = appcompat_verdict(
        missing_symbols,
        evaluation.missing_versions,
        breaking_for_app,
        evaluation.required_count,
        policy,
        policy_file,
    )
    _finalize_consumer_scope_diff(
        diff, overlay_ledger, breaking_for_app, policy=policy, policy_file=policy_file
    )
    # ADR-067: the ledger is *not* closed here. This runs once per `--used-by`
    # consumer and `apply_scope` only ever demotes, so closing per consumer
    # would intersect the consumers' relevant sets instead of unioning them.
    # The scoped gate's orchestrator (`cli_helpers_compare.
    # _apply_used_by_scoping`, or `check_appcompat` below) makes the single
    # `close_consumer_scope` call.
    return AppCompatResult(
        app_path=str(app_path),
        old_lib_path=old_facts.label,
        new_lib_path=new_facts.label,
        required_symbols=app_reqs.undefined_symbols,
        required_symbol_count=evaluation.required_count,
        breaking_for_app=breaking_for_app,
        irrelevant_for_app=evaluation.irrelevant,
        missing_symbols=missing_symbols,
        missing_versions=evaluation.missing_versions,
        full_diff=diff,
        verdict=verdict,
        symbol_coverage=evaluation.coverage,
        platform=spec.platform,
        profile=spec.profile,
        provider_baseline=spec.provider_baseline,
        digest=spec.digest,
        requirement=spec.requirement.value,
    )


def check_against(
    app_path: Path,
    new_lib_path: Path,
) -> AppCompatResult:
    """Check if a library provides everything the app needs (weak mode).

    No old library required — just checks symbol availability.
    """
    new_facts = read_library_export_facts(new_lib_path)
    consumer = read_consumer_facts(as_consumer_spec(app_path), new_facts.soname)
    return check_against_facts(consumer, new_facts)


def check_against_facts(
    consumer: ConsumerImportFacts,
    new_facts: LibraryExportFacts,
) -> AppCompatResult:
    """:func:`check_against` over already-read facts."""
    app_reqs = consumer.requirements
    missing_symbols = sorted(
        s for s in app_reqs.undefined_symbols if s not in new_facts.export_names
    )
    missing_versions = missing_app_versions(app_reqs, new_facts)
    required_count = len(app_reqs.undefined_symbols)
    return AppCompatResult(
        app_path=str(consumer.path),
        old_lib_path="",
        new_lib_path=new_facts.label,
        required_symbols=app_reqs.undefined_symbols,
        required_symbol_count=required_count,
        missing_symbols=missing_symbols,
        missing_versions=missing_versions,
        verdict=(
            Verdict.BREAKING
            if (missing_symbols or missing_versions)
            else Verdict.COMPATIBLE
        ),
        symbol_coverage=symbol_coverage(
            new_facts.export_names, required_count, len(missing_symbols)
        ),
    )


def _promote_scoped_contract(
    changes: list[Change],
    *,
    policy: str | None,
    policy_file: PolicyFile | None,
    diff: DiffResult | None = None,
) -> None:
    """Apply ADR-049 §4.3's explicit-scope evidence to a scoped finding set.

    A finding this module has just decided is relevant to a concrete
    consumer's imports (or to an explicitly required entrypoint) *is* that
    ADR's strongest in-contract evidence -- stronger than, and independent
    of, the header/export-derived relevance ``compare()`` reached. Since
    ADR-049 Phase 7 made relevance authoritative, that has to be applied
    here, at the point the relevance is decided, rather than by a later
    stamping pass: both the CLI and the MCP tool compute their *scoped exit
    code* from these lists immediately, so a promotion that ran afterwards
    left the gate scoring a weaker ``UNKNOWN_UNRESOLVED`` while the same run
    rendered the finding as ``IN_CONTRACT`` with a ``BREAKING`` decision
    (Codex review, confirmed via ``--required-symbol`` under
    ``--contract exports``).

    Deliberately limited to findings that *already carry* a contract
    relevance: that is true exactly when the caller opted into
    ``contract_evaluation=True``, so a run that did not opt in keeps every
    finding unstamped -- and an unstamped finding already gates, per
    ``contract_gating``'s documented default. Synthetic scoped-only findings
    this module creates are likewise left alone here; they gate for the same
    reason, and the later aggregate pass gives them their decision for the
    report.
    """
    from ..contract_scoped_promotion import (
        recompute_verdict_after_promotion,
        stamp_scoped_changes,
    )
    from ..model.contract_finding_relevance import is_evaluated

    already_classified = [
        c for c in changes if getattr(c, "contract_relevance", None) is not None
    ]
    if not already_classified:
        return
    # Whether this promotion actually moves anything back onto the
    # compatibility axis decides whether the full verdict has gone stale --
    # checked before, since the promotion is what changes the answer.
    promoted_onto_axis = any(not is_evaluated(c) for c in already_classified)
    stamp_scoped_changes(already_classified, policy=policy, policy_file=policy_file)
    if promoted_onto_axis and diff is not None:
        recompute_verdict_after_promotion(diff, policy=policy, policy_file=policy_file)


def _finalize_consumer_scope_diff(
    diff: DiffResult,
    overlay_ledger: DispositionLedger,
    breaking_for_app: list[Change],
    *,
    policy: str | None,
    policy_file: PolicyFile | None,
) -> None:
    """The one finalization boundary for :func:`scope_diff_to_app`'s and
    :func:`scope_diff_to_required_symbols`'s mutation of the already-returned,
    already-finalized *diff* each was handed (ADR-063 T10; shared between
    both call sites since ADR-067 C-S2, the same rationale that moved their
    overlay-loop body into :func:`~abicheck.policy.disposition_close.
    record_and_maybe_suppress_overlay`).

    Both functions run after ``checker.compare()`` already finalized ``diff``
    and handed it back to their caller. Attaching this run's disposition
    ledger, promoting consumer-proven findings onto the compatibility axis,
    and recomputing the verdict that promotion leaves stale are all instances
    of the same thing: a second producer joining ``diff`` after
    ``compare()``'s own close -- exactly what
    :func:`~abicheck.policy.disposition_close.close_consumer_scope`'s own
    docstring names for the *aggregate* union each function's caller later
    assembles from every consumer's/host's own relevant-changes list. This
    function collects the *per-consumer* (or *per-host*) half of that same
    concern into one named, called-once boundary, rather than a
    ``diff.disposition_ledger = ...`` assignment before the overlay loop and
    a bare ``_promote_scoped_contract(...)`` call after it, as two
    free-standing statements with the dependency between them left implicit.

    Order is fixed here rather than left to the call site: the ledger must
    be attached before promotion runs (a later reader of
    ``diff.disposition_ledger`` must see the same object this consumer/host
    recorded into), and promotion must run before the verdict recompute
    (the recompute reads the very ``contract_relevance``/verdict-bucket
    fields promotion just stamped). *overlay_ledger* is the exact object
    every ``record_consumer_overlay``/``record_and_maybe_suppress_overlay``
    call above already recorded into -- passed in rather than re-resolved via
    ``ledger_for(diff)`` here, since ``diff.disposition_ledger`` may still be
    unset at this point and a fresh resolve would build a second,
    disconnected ledger instead of publishing the one this run actually
    populated. Without this attachment, a later ``ledger_for(diff)`` call
    (e.g. ``check_plugin_host_contract``'s own closing ``close_consumer_
    scope`` call) would rebuild yet another fresh, disconnected ledger from
    *diff*'s own change buckets and silently lose every overlay finding this
    run recorded (review finding on the PR that added the required-symbol
    call site).
    """
    if getattr(diff, "disposition_ledger", None) is None:
        diff.disposition_ledger = overlay_ledger
    _promote_scoped_contract(
        breaking_for_app, policy=policy, policy_file=policy_file, diff=diff
    )


def scope_diff_to_required_symbols(
    diff: DiffResult,
    old_plugin: AbiSnapshot,
    new_plugin: AbiSnapshot,
    required_entrypoints: Iterable[str],
    *,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
    suppression: SuppressionList | None = None,
) -> PluginHostContractResult:
    """Scope an already-computed diff to an explicit required-symbol contract.

    This is the generic-scoped-comparison core ``compare --required-symbol(s)``
    calls (ADR-043) — the plugin-host mirror of :func:`scope_diff_to_app`. The
    full old/new comparison runs exactly once (by the caller); this function
    only intersects the given ``required_entrypoints`` with that diff. See
    :func:`check_plugin_host_contract` for the standalone convenience wrapper
    that also runs the comparison itself.

    *suppression* (ADR-067 C-S2, mirrors :func:`scope_diff_to_app`'s own
    *suppression* parameter — ADR-044 P2): the same rule set already used to
    compute *diff*, evaluated again here because the missing-entrypoint
    overlay below is synthesized fresh *after* that pass already ran, so it
    would otherwise be unsuppressible even by an exact rule.
    """
    required = set(required_entrypoints)
    new_exports = dlsym_export_names(new_plugin)
    missing = sorted(e for e in required if e not in new_exports)
    # Coverage is an objective fact about the export table, computed from the
    # raw (pre-suppression) missing count -- mirrors scope_diff_to_app's own
    # coverage computation call above its overlay loop
    # (see that call's comment). A suppressed missing entrypoint must not
    # make the reported coverage number lie by shrinking the denominator.
    raw_missing_count = len(missing)

    # Reuse the app-scoping machinery: the contract is a set of required
    # ("undefined") symbols, identical in shape to an app's symbol needs.
    host_reqs = AppRequirements(undefined_symbols=set(required))
    breaking_for_host, _ = partition_app_changes(diff.changes, host_reqs)

    # ADR-067 C-S2: the host-contract mirror of scope_diff_to_app's own
    # CONSUMER_REQUIRED_SYMBOL_REMOVED overlay loop (ADR-044 P2 item 1) --
    # promote a missing entrypoint not already represented by a diff Change
    # into the same first-class, suppressible, ledger-recorded finding,
    # instead of leaving it as a bespoke string only special-cased by the
    # CLI's own `_apply_required_symbol_scoping`/reporter code. Without this
    # the ledger's raw-versus-effective totals never accounted for a
    # required-symbol contract's missing entrypoints at all -- exactly the
    # gap the C-S1 slice already closed for `--used-by`.
    overlay_ledger = ledger_for(diff)
    suppressed_missing: set[str] = set()
    for sym in uncovered_missing_symbols(missing, breaking_for_host):
        # public_reachable=True/PROVEN_REACHABLE, mirroring
        # scope_diff_to_app's identical overlay: this finding only ever
        # exists because a real declared entrypoint contract's own required
        # symbol genuinely resolved to nothing in the new plugin -- there is
        # no "maybe internal, maybe not" ambiguity a broad namespace/
        # source_location suppression rule's default "unreachable-only"
        # reachability should be allowed to read as unreachable.
        overlay_change = make_change(
            ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
            symbol=sym,
            name=new_plugin.library or "new",
            public_reachable=True,
            reachability_kind="consumer_proven",
            reachability_state=ReachabilityState.PROVEN_REACHABLE,
        )
        overlay_change.impact_assessment = assess_change(overlay_change)
        kept, overreach = record_and_maybe_suppress_overlay(
            overlay_ledger,
            overlay_change,
            diff,
            suppression=suppression,
            application_point="required_symbol_overlay",
        )
        if not kept:
            suppressed_missing.add(sym)
            continue
        breaking_for_host.append(overlay_change)
        if overreach is not None:
            breaking_for_host.append(overreach)
    if suppressed_missing:
        missing = [s for s in missing if s not in suppressed_missing]

    verdict = appcompat_verdict(
        missing,
        [],
        breaking_for_host,
        len(required),
        policy,
        policy_file,
    )
    coverage = symbol_coverage(new_exports, len(required), raw_missing_count)
    _finalize_consumer_scope_diff(
        diff, overlay_ledger, breaking_for_host, policy=policy, policy_file=policy_file
    )

    return PluginHostContractResult(
        old_plugin=old_plugin.library or "old",
        new_plugin=new_plugin.library or "new",
        required_entrypoints=required,
        missing_entrypoints=missing,
        breaking_for_host=breaking_for_host,
        full_diff=diff,
        verdict=verdict,
        coverage=coverage,
    )
