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

"""``abicheck compare --no-baseline NEW`` -- ADR-068 D2, plan §6 Phase 2e.

Replaces `scan`'s audit-only mode (plan §3 #2) and ADR-047 §8's S5
("single-build audit, no baseline"). The OLD side of this run carries
ADR-065's new :attr:`~abicheck.model.scope_acquisition.AcquisitionState.
DECLARED_ABSENT` acquisition state (plan §5 P1) -- an explicit user
declaration that no prior surface exists, never inferred from a missing
argument and never confused with :attr:`~abicheck.model.scope_acquisition.
AcquisitionState.NOT_SUPPLIED` (an *unproven*, possibly-incomplete-scope
absence).

**How this reports candidate-side facts without an OLD snapshot**:
:func:`run_no_baseline_compare` calls the one comparison verb
(:func:`abicheck.checker.compare`) with ``old=None``. Baseline presence is
an *input* to that pipeline, not a different product: facts are collected,
the applicable checks run, policy is applied and a result is built exactly
as for a two-sided run -- only the *evolution* stages, the ones that need
two sides, do not run. Nothing stands in for the missing baseline.

This used to be implemented as a self-diff (``compare(new, new)``), on the
reasoning that two identical snapshots can produce no addition, removal or
modification, so the real pipeline could run unmodified. The empty
comparison half was indeed exact; the *evolution* half was not. The eleven
cross-source hygiene checks and the pattern/preprocessor pre-scan
(ADR-068 Phase 2a/2b) run per side inside ``compare()`` and fold their two
one-sided results into an OLD->NEW state -- against a copy of the
candidate, every candidate-side finding therefore came back ``persistent``
("present on both sides"), which is a claim about a baseline the run was
explicitly told does not exist. ``old=None`` separates the two axes the
self-diff conflated: the finding is a full-confidence observation about
this build (the check ran, on real candidate evidence), and its historical
evolution is :attr:`~abicheck.model.evidence_status.CrossSourceEvolution.
NOT_EVALUATED`, because no history was observable. That is now a
*structural* property of the run rather than a rule about it -- which is
why ADR-068 D3's permitted-state allowlist, a second rule set that existed
only for this mode, is gone.

:mod:`abicheck.policy.no_baseline_findings` still *partitions* the change
set into the comparison half (provably empty, still enforced -- a
non-empty one means a detector read state it cannot have) and the
candidate-side half, which is the audit's reportable content.

The one thing this module does *not* do is decide how the resulting
:class:`~abicheck.checker_types.DiffResult` gets reported: ``NO_CHANGE`` is
the *true* verdict of "new compared to itself", but the whole point of
``--no-baseline`` is that no compatibility comparison was ever intended --
so a caller (:mod:`abicheck.frontends.cli.commands.compare_no_baseline`)
must null the compatibility-shaped fields (``verdict``,
``run_outcome.compatibility``) before this reaches a report; ADR-068 D2:
"never emits an addition, removal, or compatibility verdict -- run_outcome
carries no compatibility contribution".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..checker import compare as _diff_pair
from ..model.scope_acquisition import (
    AcquisitionState,
    InventoryCompleteness,
    MemberAcquisition,
    ScopeAcquisitionRecord,
    SideInventory,
)
from ..policy.depth_evidence_contract import (
    record_no_baseline_depth_evidence_contract_error,
)
from ..policy.no_baseline_findings import (
    check_no_baseline_partition,
    partition_no_baseline_findings,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ..checker_types import DiffResult
    from ..compile_context import CompileContext
    from ..environment_matrix import EnvironmentMatrix
    from ..model import AbiSnapshot
    from ..model.change import Change
    from ..policy_file import PolicyFile
    from ..suppression import SuppressionList

__all__ = [
    "NO_BASELINE_SELECTION",
    "NoBaselineAuditInputs",
    "NoBaselineCompareResult",
    "audit_no_baseline_candidate",
    "candidate_is_live_artifact",
    "declared_absent_acquisition_record",
    "public_header_sets_for_candidate",
    "resolve_no_baseline_candidate",
    "run_no_baseline_compare",
]


def resolve_no_baseline_candidate(
    path: Path,
    *,
    headers: list[Path] | None = None,
    exclude_headers: tuple[str, ...] = (),
    includes: list[Path] | None = None,
    lang: str = "c++",
    lang_explicit: bool = False,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    sources: Path | None = None,
    build_info: Path | None = None,
    build_config: Path | None = None,
    depth: str | None = None,
    version: str = "",
    debug_roots: list[Path] | None = None,
    pdb: Path | None = None,
    enable_debuginfod: bool = False,
    debuginfod_url: str | None = None,
    dwarf_only: bool = False,
    debug_format: str | None = None,
    collect_mode: str | None = None,
    include_labels: dict[Path, str] | None = None,
    compile: CompileContext | None = None,
    include_dependencies: bool = True,
    notify: Callable[[str], None] | None = None,
) -> AbiSnapshot:
    """Resolve the sole ``--no-baseline`` operand into an
    :class:`~abicheck.model.AbiSnapshot`.

    A thin, workflows-internal call to the *same* per-side primitive both
    `dump` and each side of a two-sided `compare` resolve through
    (``workflows.artifact.execute.resolve_side_snapshot``, reached publicly
    as ``service_input_resolution.resolve_side_snapshot``). Called from here
    rather than from the CLI layer directly so the ``cli-contract``
    AI-readiness gate's "front end may not call Tier-1 core directly" rule
    is satisfied by construction: the CLI module calls this workflow, this
    workflow calls the shared resolver.

    **Why the shared primitive rather than a bare ``resolve_input``.** This
    function used to call :func:`abicheck.workflows.input_resolution.
    resolve_input` directly, which resolves L0-L2 evidence only -- so
    ``compare --no-baseline NEW --sources tree/`` silently produced a
    symbols/headers-only snapshot and ``--depth build``/``--depth source``
    had nothing at all to act on (the ``--sources``/``--build-info``/
    ``--depth`` half of this file's own "Known gaps" entry). Routing through
    ``resolve_side_snapshot`` reuses, rather than re-implements, the inline
    L3-L5 embed step a two-sided `compare` side already gets: *depth* picks
    the collect mode via the same ``collect_mode_for`` rule (already
    variadic over one input for exactly this reason -- ``dump`` asks it over
    its single operand too), and the ``InputSpec`` carries *sources*/
    *build_info* the same way a sided ``--sources new=`` does.

    The evidence *floor* a pinned ``--depth build``/``--depth source``
    implies is deliberately not enforced here -- it is
    :func:`abicheck.policy.depth_evidence_contract.
    record_no_baseline_depth_evidence_contract_error`'s job, recorded as
    ADR-064's exit-7 axis on the result rather than raised during
    resolution, mirroring exactly what the two-sided native CLI path does.

    The header backend and SYCL frontend context come from *compile*
    (``compile.frontend``/``compile.frontend_context``), which outranks the
    bare defaults this passes, exactly as on two operands. *collect_mode*,
    when given, is the caller's already-resolved one (the CLI's rule adds
    ``source.method``); ``None`` infers it from *depth* and the inputs. The
    debug settings and *pdb* are the ``debug:`` block's, which two-sided
    ``compare`` applies to each side it extracts.
    """
    from ..service_compare_evidence import collect_mode_for, resolve_side_evidence
    from .artifact.execute import resolve_side_snapshot
    from .request_inputs import InputSpec

    side = InputSpec.of(
        path,
        headers=headers or [],
        exclude_headers=exclude_headers,
        includes=includes or [],
        version=version,
        sources=sources,
        build_info=build_info,
        build_config=build_config,
        debug_roots=debug_roots or [],
        pdb=pdb,
        public_header_dirs=public_header_dirs or [],
        include_dependencies=include_dependencies,
        compile=compile,
    )
    # Built through the *shared* resolver rather than by hand: it is what
    # applies ``--depth binary``'s own clearing rules (headers and any dump
    # manifest are dropped, so a binary-only request cannot silently keep
    # running the L2 header-AST frontend). Hand-building ``SideEvidence`` here
    # skipped both, so ``compare --no-baseline lib.so -H inc --depth binary``
    # still parsed headers and could report a header-derived finding at a
    # depth documented as symbols-only -- diverging from the two-sided
    # ``compare``, which drops the ``header`` tier for the identical
    # invocation (Codex review, P1). ``pair_compile=None`` for the same reason
    # ``resolve_dump_request_evidence`` passes it: the pair-wide C++20 override
    # exists so two *sides* cannot disagree, and there is no second side here.
    # ``frontend_context`` is SYCL's device/host AST selector, not the header
    # backend: ``"host"`` is the request-level default, and a configured
    # ``compile.frontend_context`` on *compile* outranks it. Conflating the
    # two axes made every ordinary ELF audit demand a DPC++ compiler.
    evidence = resolve_side_evidence(
        side,
        depth=depth,
        collect_mode=collect_mode
        if collect_mode is not None
        else collect_mode_for(depth, side),
        pair_compile=None,
        frontend_context="host",
    )
    public_files, public_dirs = (
        ([], [])
        if depth is not None and depth.lower() == "binary"
        # ``_public_header_sets``' own rule, for the same reason: a headerless
        # dump still fingerprints these, so leaving them populated at binary
        # depth records a public-header scope the snapshot does not have.
        else (list(public_headers or []), list(public_header_dirs or []))
    )
    return resolve_side_snapshot(
        side,
        evidence,
        lang=lang,
        lang_explicit=lang_explicit,
        header_backend="auto",
        fmt=None,
        public_headers=public_files,
        public_header_dirs=public_dirs,
        enable_debuginfod=enable_debuginfod,
        debuginfod_url=debuginfod_url,
        dwarf_only=dwarf_only,
        debug_format=debug_format,
        include_labels=include_labels,
        notify=notify,
    )


def public_header_sets_for_candidate(
    headers: list[Path],
    public_headers: list[Path],
    public_header_dirs: list[Path],
) -> tuple[list[Path], list[Path]]:
    """Split ``-H``/``--header`` into the file/dir public-header sets.

    The *second* root cause of this path's "renders an empty report against a
    live binary" gap, distinct from the finding-partition one: a two-sided
    ``compare`` has always folded each side's ``--header`` values into that
    side's public-header provenance sets (``service_compare_pipeline.
    _public_header_sets``, via the same ``split_public_header_inputs``
    primitive used here), which is what gives a declaration a resolvable
    :class:`~abicheck.model.ScopeOrigin` -- and four of the eleven
    cross-source checks (``exported_not_public``, ``public_not_exported``,
    ``rtti_for_internal_type``, ``public_to_internal_dependency``)
    evidence-gate to ``NOT_EVALUATED`` without one. This path passed
    ``--header`` only as *parse* input, never as provenance, so
    ``compare --no-baseline libgreet.so --header include`` classified every
    declaration ``UNKNOWN`` and reported nothing where ``scan libgreet.so
    --header include`` reported ``exported_not_public``.

    Split before tagging, never after: an unsplit directory entry in the
    file set corrupts ``scope_fingerprint`` (the same reason
    ``_public_header_sets`` splits). Any explicit ``--public-header``/
    ``--public-header-dir`` value is unioned in on top, not replaced.
    """
    from ..header_utils import split_public_header_inputs

    split_files, split_dirs = split_public_header_inputs(headers)
    return (
        [*split_files, *public_headers],
        [*split_dirs, *public_header_dirs],
    )


def candidate_is_live_artifact(
    path: Path,
    *,
    sources: Path | None = None,
    build_info: Path | None = None,
) -> bool:
    """Whether this run performs a live extraction, rather than only reading
    an already-serialized snapshot.

    Only the depth evidence-contract carve-out reads this (see
    ``policy.depth_evidence_contract``'s "Live extraction only" note): the
    ``--depth build``/``--depth source`` floor means nothing for a run that
    extracted nothing, since it cannot have fallen short of a depth. Three
    things make a run live, and all three are checked: a native artifact
    operand, a linker script resolving to one, and raw ``--sources``/
    ``--build-info`` evidence this run collects from itself -- the last of
    which makes even a stored-snapshot operand live.

    Lives here rather than in the CLI because the question is answered by
    ``binary_utils.detect_binary_format`` -- an ``extract``-layer primitive a
    ``frontends`` module may not import (ADR-061's dependency direction;
    ``frontends -> workflows -> extract`` is the sanctioned route). Errs
    toward "live" on an unreadable path: resolution will raise a real,
    specific error about it moments later, and claiming "stored" would
    silently *suppress* the exit-7 axis.
    """
    from ..buildsource.raw_evidence import any_raw_evidence_input
    from .input_resolution import side_is_live

    # `side_is_live` owns this rule for both `compare` forms -- see its
    # docstring for the two ways a run is live, and for the narrower question
    # ("is this a native binary?") both paths used to ask instead, which
    # silently exempted every operand that is neither a binary nor a snapshot.
    return side_is_live(
        path, had_raw_evidence=any_raw_evidence_input(sources, build_info)
    )


#: The one selection value a ``--no-baseline`` run's
#: :class:`~abicheck.model.scope_acquisition.ScopeAcquisitionRecord` carries
#: -- distinct from ``"direct_pair"`` (a real two-sided scalar comparison,
#: which today never even builds a record) and from the release fan-out's
#: ``"all_expected"``/``"current_artifact"``.
NO_BASELINE_SELECTION = "no_baseline"


@dataclass(frozen=True)
class NoBaselineCompareResult:
    """What a ``--no-baseline`` run produces.

    *diff* is the candidate-side :class:`~abicheck.checker_types.DiffResult`
    the self-compare produced, carried verbatim so every candidate-side fact
    on it (evidence tiers, coverage ledger, analysis assurance, the contract
    context) stays reachable by a report unchanged.

    *findings* is the audit's reportable content: the candidate-side half of
    ``diff.changes``, already partitioned and already checked against
    ADR-068 D3 (see :mod:`abicheck.policy.no_baseline_findings`). It is a
    field rather than a property so the partition -- and therefore the
    invariant check -- happens exactly once, at construction, instead of
    once per renderer that asks. The complementary *comparison* half is
    never carried at all: it is provably empty, so there is nothing for a
    consumer to read.

    *suppressed_findings* is the same partition applied to
    ``diff.suppressed_changes`` -- the candidate-side findings a
    ``--suppress`` rule matched. ``checker.compare()`` moves a suppressed
    finding *out* of ``diff.changes``, so reading only ``changes`` made a
    suppressed audit indistinguishable from a clean one: the finding was
    detected, then vanished, with no way for a reader to tell that policy
    hid it or which rule did (Codex review, P1). ``vision.md``'s "Record
    before disposing" rule forbids exactly that -- "100 removals detected,
    100 suppressed by rule X" must stay visible on a passing run -- so the
    suppressed half is carried here and projected by every renderer,
    with each finding's own ``suppression_rule`` alongside it.
    """

    diff: DiffResult
    acquisition: ScopeAcquisitionRecord
    findings: tuple[Change, ...] = ()
    suppressed_findings: tuple[Change, ...] = ()


def declared_absent_acquisition_record(new: AbiSnapshot) -> ScopeAcquisitionRecord:
    """The one-member :class:`ScopeAcquisitionRecord` a ``--no-baseline``
    run carries: OLD is ``declared_absent`` (not "unmatched" -- the user
    said so), NEW is present. Never incomplete
    (:attr:`~abicheck.model.scope_acquisition.AcquisitionState.
    DECLARED_ABSENT` is excluded from ``UNCHECKED_STATES``) and never a
    proven removal/addition input (only ``NOT_SUPPLIED`` members are read
    by :attr:`~abicheck.model.scope_acquisition.ScopeAcquisitionRecord.
    proven_removed_members`/``proven_added_members``)."""
    member_key = new.library or "candidate"
    member = MemberAcquisition(
        member=member_key,
        state=AcquisitionState.DECLARED_ABSENT,
        old_present=False,
        new_present=True,
        reason="--no-baseline: no prior surface declared for this run",
        display_name=new.library,
    )
    return ScopeAcquisitionRecord(
        members=(member,),
        old_inventory=SideInventory(
            completeness=InventoryCompleteness.UNPROVEN,
            provenance="--no-baseline: OLD declared absent, not supplied",
        ),
        new_inventory=SideInventory(
            completeness=InventoryCompleteness.UNPROVEN,
            provenance="candidate build",
        ),
        selection=NO_BASELINE_SELECTION,
        selection_reason="abicheck compare --no-baseline: single candidate audit",
    )


def run_no_baseline_compare(
    new: AbiSnapshot,
    *,
    suppression: SuppressionList | None = None,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
    scope_to_public_surface: bool = True,
    force_public_symbols: set[str] | None = None,
    pattern_verdicts: bool = True,
    collapse_versioned_symbols: bool = False,
    contract_evaluation: bool = True,
    contract_mode: str | None = None,
    depth: str | None = None,
    candidate_is_live: bool = True,
    env_matrix: EnvironmentMatrix | None = None,
) -> NoBaselineCompareResult:
    """Audit *new* alone via a self-diff (see module docstring for why this
    is exact, not an approximation) and pair it with OLD's
    ``declared_absent`` acquisition record.

    The self-compare's change set is partitioned rather than asserted empty:
    the comparison half must be (and is still enforced to be) empty, while
    the candidate-side half -- the eleven cross-source hygiene checks, the
    pattern/preprocessor pre-scan, any ``--abi3``-style enrichment -- is the
    audit's reportable content and is returned on
    :attr:`NoBaselineCompareResult.findings`. Both invariants, including
    ADR-068 D3's permitted-evolution rule, live in
    :mod:`abicheck.policy.no_baseline_findings`.

    *depth*/*candidate_is_live* record ADR-064's exit-7 axis when a pinned
    ``--depth build``/``--depth source`` was not reached -- recorded here,
    after classification, rather than by the caller, for the same reason the
    two invariants above are: this function owns the run, so a front end
    cannot pick up the audit and forget the axis. *candidate_is_live* comes
    from :func:`candidate_is_live_artifact`; ``False`` exempts a stored
    snapshot this run never extracted.

    *env_matrix*, when given, runs the declared-runtime-floor/wheel-
    packaging checks against *new* alone via
    :func:`~abicheck.workflows.env_matrix_audit.env_matrix_candidate_findings`
    -- the same ``.abicheck.yml`` ``deployment:``-resolved
    :class:`~abicheck.environment_matrix.EnvironmentMatrix` the two-sided
    ``compare`` path threads through as ``resolved_cfg.deployment``. Folded
    into *extra_changes* (mirroring the ``--abi3`` audit's own
    ``candidate_side_enrichment`` pattern) rather than passed as this
    function's own ``_diff_pair(..., env_matrix=...)`` argument: the latter
    would also invoke ``checker._env_matrix_contract_changes``'s
    diff-reclassification half against this self-diff's empty change set
    (harmless -- there is never a version-requirement delta to reclassify
    on a self-diff) but would produce its candidate-only findings with
    neither ADR-068 D3 one-sided marker set, which
    ``policy.no_baseline_findings.partition_no_baseline_findings`` would
    then read as *identity*-half findings and fail the audit outright
    (Codex review: previously this parameter didn't exist at all, so a real
    declared ``deployment.runtime_floors`` config was silently ignored for
    a no-baseline audit of a candidate that violates it). Omitted (the
    default): behavior is unchanged from before this parameter existed.

    *env_matrix* is also, separately, stamped onto the returned
    :class:`~abicheck.checker_types.DiffResult` as
    ``env_matrix_source_sha256`` -- the same digest ``checker.compare()``
    would stamp had *env_matrix* been passed to it directly. It cannot be:
    see above for why this path never runs ``_diff_pair(..., env_matrix=
    ...)``. Without this, a typed caller reading the result back cannot tell
    a run governed by a declared ``deployment.runtime_floors`` contract from
    one with no deployment contract at all, even though the matrix changed
    this run's findings and verdict (Codex review, P2). Computed with
    ``workflows.comparison_input_receipt.env_matrix_content_digest``, the identical shared function
    ``compare()`` itself calls, as a plain post-hoc field replacement --
    not a second comparison.
    """
    import dataclasses as _dataclasses

    from .comparison_input_receipt import env_matrix_content_digest
    from .env_matrix_audit import fold as _fold_env_matrix

    extra_changes = _fold_env_matrix(None, new, env_matrix)
    diff = _diff_pair(
        None,
        new,
        suppression=suppression,
        policy=policy,
        policy_file=policy_file,
        scope_to_public_surface=scope_to_public_surface,
        force_public_symbols=force_public_symbols,
        extra_changes=extra_changes,
        pattern_verdicts=pattern_verdicts,
        collapse_versioned_symbols=collapse_versioned_symbols,
        contract_evaluation=contract_evaluation,
        contract_mode=contract_mode,
    )
    if env_matrix is not None:
        diff = _dataclasses.replace(
            diff,
            env_matrix_source_sha256=env_matrix_content_digest(env_matrix),
        )
    record_no_baseline_depth_evidence_contract_error(
        diff, depth, new, is_live=candidate_is_live
    )
    partition = partition_no_baseline_findings(diff.changes)
    check_no_baseline_partition(partition)
    # The suppressed half gets the identical partition -- a suppressed
    # comparison finding would be just as impossible without a baseline, and
    # the invariant applies whether or not policy later hid a finding, so it
    # is checked rather than waived for anything a rule happened to match.
    suppressed = partition_no_baseline_findings(
        getattr(diff, "suppressed_changes", ()) or ()
    )
    check_no_baseline_partition(suppressed)
    return NoBaselineCompareResult(
        diff=diff,
        acquisition=declared_absent_acquisition_record(new),
        findings=partition.one_sided,
        suppressed_findings=suppressed.one_sided,
    )


@dataclass(frozen=True)
class NoBaselineAuditInputs:
    """Everything one candidate's audit needs besides the candidate itself.

    The scalar ``compare --no-baseline FILE`` and the N-library ``compare
    --no-baseline DIR`` both build one of these and hand it, per candidate, to
    :func:`audit_no_baseline_candidate` -- so "audit one artifact" has exactly
    one sequence, and a directory member cannot be audited any differently
    from the same file named on its own (one-comparison-product F-23). Values
    only, already resolved by the front end: the policy documents are loaded
    *before* any candidate is resolved, which is what lets a set audit refuse
    a malformed ``--suppress`` once rather than report it as N member
    failures.
    """

    headers: tuple[Path, ...] = ()
    exclude_headers: tuple[str, ...] = ()
    includes: tuple[Path, ...] = ()
    lang: str = "c++"
    lang_explicit: bool = False
    public_headers: tuple[Path, ...] = ()
    public_header_dirs: tuple[Path, ...] = ()
    sources: Path | None = None
    build_info: Path | None = None
    build_config: Path | None = None
    depth: str | None = None
    version: str = ""
    debug_roots: tuple[Path, ...] = ()
    #: The collect mode the front end resolved (``--depth`` > ``source.
    #: method`` > inferred), and the ``debug:`` block -- both applied to the
    #: candidate exactly as two-sided ``compare`` applies them to a side.
    collect_mode: str | None = None
    pdb: Path | None = None
    enable_debuginfod: bool = False
    debuginfod_url: str | None = None
    dwarf_only: bool = False
    debug_format: str | None = None
    include_labels: dict[Path, str] | None = None
    include_dependencies: bool = False
    compile: CompileContext | None = None
    #: Where resolution progress goes; the front end's own channel.
    notify: Callable[[str], None] | None = None
    suppression: SuppressionList | None = None
    policy: str = "strict_abi"
    policy_file: PolicyFile | None = None
    scope_to_public_surface: bool = True
    #: ``scope.public_symbols``: declarations forced public, the overlay
    #: two-sided ``compare`` applies (``resolve_force_public_scope``).
    force_public_symbols: frozenset[str] = frozenset()
    collapse_versioned_symbols: bool = False
    contract_evaluation: bool = True
    contract_mode: str | None = None
    env_matrix: EnvironmentMatrix | None = None


def audit_no_baseline_candidate(
    candidate: Path, inputs: NoBaselineAuditInputs
) -> NoBaselineCompareResult:
    """Resolve *candidate* and audit it -- the one resolve-and-run sequence.

    Exactly :func:`resolve_no_baseline_candidate` followed by
    :func:`run_no_baseline_compare`, with the live/stored carve-out
    (:func:`candidate_is_live_artifact`) computed from the same candidate.
    Raises whatever those raise; classifying a failure (usage error for a
    scalar audit, a member's own acquisition state for a set) is the
    caller's job, since the two answer it differently.
    """
    snapshot = resolve_no_baseline_candidate(
        candidate,
        headers=list(inputs.headers),
        exclude_headers=inputs.exclude_headers,
        includes=list(inputs.includes),
        lang=inputs.lang,
        lang_explicit=inputs.lang_explicit,
        public_headers=list(inputs.public_headers),
        public_header_dirs=list(inputs.public_header_dirs),
        sources=inputs.sources,
        build_info=inputs.build_info,
        build_config=inputs.build_config,
        depth=inputs.depth,
        version=inputs.version,
        debug_roots=list(inputs.debug_roots),
        pdb=inputs.pdb,
        enable_debuginfod=inputs.enable_debuginfod,
        debuginfod_url=inputs.debuginfod_url,
        dwarf_only=inputs.dwarf_only,
        debug_format=inputs.debug_format,
        collect_mode=inputs.collect_mode,
        include_labels=inputs.include_labels,
        include_dependencies=inputs.include_dependencies,
        compile=inputs.compile,
        notify=inputs.notify,
    )
    return run_no_baseline_compare(
        snapshot,
        suppression=inputs.suppression,
        policy=inputs.policy,
        policy_file=inputs.policy_file,
        scope_to_public_surface=inputs.scope_to_public_surface,
        force_public_symbols=set(inputs.force_public_symbols) or None,
        # ADR-068 D4/Phase 5: pattern-verdict modulation is unconditional on
        # every `compare` path now (no `--pattern-verdicts` flag exists any
        # more) -- this audit-only path gets the identical treatment.
        pattern_verdicts=True,
        collapse_versioned_symbols=inputs.collapse_versioned_symbols,
        contract_evaluation=inputs.contract_evaluation,
        contract_mode=inputs.contract_mode,
        # ADR-064's exit-7 axis: a pinned --depth build/source that this
        # run's evidence did not reach -- recorded by `run_no_baseline_
        # compare` after classification, so no front end can forget it.
        depth=inputs.depth,
        candidate_is_live=candidate_is_live_artifact(
            candidate, sources=inputs.sources, build_info=inputs.build_info
        ),
        env_matrix=inputs.env_matrix,
    )
