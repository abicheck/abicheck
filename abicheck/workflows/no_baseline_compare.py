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

**How this reports candidate-side facts without a real OLD snapshot**:
:func:`run_no_baseline_compare` diffs *new* against itself. Two identical
:class:`~abicheck.model.AbiSnapshot` objects can never produce an addition,
a removal, or a modification -- so the real detector/suppression/policy
pipeline (:func:`abicheck.checker.compare`) runs completely unmodified, and
every candidate-side fact the resulting :class:`~abicheck.checker_types.
DiffResult` carries (evidence tiers, coverage, analysis assurance) is
genuine evidence about *new*, not a synthesized stand-in. The empty
*comparison* change set falls out of the identity comparison by
construction, rather than from a change-kind filter that could silently
drift out of sync with the detector registry (a filter is a second place a
new ``ChangeKind`` would need to be taught about "no-baseline mode";
identity needs no such maintenance).

**What identity does *not* make empty.** ADR-068 Phase 2a/2b moved all
eleven cross-source hygiene checks and the pattern/preprocessor pre-scan
into ``compare()`` itself, and those stages read one snapshot's evidence
sources *against each other* -- they carry no baseline, so a self-compare
fires them exactly as a real comparison would, and their findings are the
entire reason a single-build audit exists. This module therefore
*partitions* rather than asserts emptiness: :mod:`abicheck.policy.
no_baseline_findings` separates the comparison findings (still absolutely
empty, still enforced) from the candidate-side ones (reported), and
enforces ADR-068 D3's rule that with OLD ``declared_absent`` a one-sided
finding may only ever read ``persistent`` or ``not_evaluated``, never
``introduced``/``resolved``. Before that split, every stored-snapshot
audit aborted on the old blanket ``assert not diff.changes`` and every
live one rendered an empty document.

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

    from ..checker_types import Change, DiffResult
    from ..compile_context import CompileContext
    from ..model import AbiSnapshot
    from ..policy_file import PolicyFile
    from ..suppression import SuppressionList

__all__ = [
    "NO_BASELINE_SELECTION",
    "NoBaselineCompareResult",
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
    header_backend: str = "auto",
    frontend_context: str = "host",
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
    """
    from ..api_types import InputSpec
    from ..service_compare_evidence import collect_mode_for, resolve_side_evidence
    from .artifact.execute import resolve_side_snapshot

    side = InputSpec.of(
        path,
        headers=headers or [],
        includes=includes or [],
        version=version,
        sources=sources,
        build_info=build_info,
        build_config=build_config,
        debug_roots=debug_roots or [],
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
    # *frontend_context* is SYCL's device/host AST selector (``DumpRequest``'s
    # own ``"host"`` default), not the header backend -- they are separate
    # axes, and conflating them makes every ordinary ELF audit demand a
    # DPC++ compiler.
    evidence = resolve_side_evidence(
        side,
        depth=depth,
        collect_mode=collect_mode_for(depth, side),
        pair_compile=None,
        frontend_context=frontend_context.lower(),
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
        header_backend=header_backend,
        fmt=None,
        public_headers=public_files,
        public_header_dirs=public_dirs,
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
    from ..binary_utils import detect_binary_format, resolve_linker_script_chain
    from ..buildsource.raw_evidence import any_raw_evidence_input

    # Raw `--sources`/`--build-info` makes the run live even when the
    # *artifact* operand is a stored snapshot: the run collects L3-L5
    # evidence itself, so a pinned depth is something it can genuinely fall
    # short of. Testing the operand's path alone exempted exactly that case
    # -- `compare --no-baseline case143.abi.json --sources <empty dir>
    # --depth source` exited 0 with `elf, header` evidence where the
    # equivalent two-sided invocation exited 7 (Codex review, P1). The
    # two-sided path already encoded this as `old_fmt is not None or
    # old_had_raw_evidence`; `buildsource.raw_evidence` is now the one owner
    # of the predicate's other half, so the two sides share the rule rather
    # than each carrying a copy of it.
    if any_raw_evidence_input(sources, build_info):
        return True

    try:
        # Follow a GNU ld INPUT()/GROUP() script to its target first: the
        # script itself is text, so `detect_binary_format` answers `None` for
        # it -- but the resolver *does* follow it and extracts the DSO it
        # names, so treating the operand as stored exempted a genuinely live
        # extraction from the depth floor. `compare --no-baseline alias.so
        # --depth build` exited 0 where the very same library, named
        # directly, exited 7 (Codex review, P1). Chain-resolving matches what
        # `resolve_input` does with the same operand, so the two agree by
        # construction rather than by coincidence.
        return detect_binary_format(resolve_linker_script_chain(path)) is not None
    except OSError:
        return True


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
    pattern_verdicts: bool = False,
    collapse_versioned_symbols: bool = False,
    contract_evaluation: bool = False,
    contract_mode: str | None = None,
    depth: str | None = None,
    candidate_is_live: bool = True,
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
    """
    diff = _diff_pair(
        new,
        new,
        suppression=suppression,
        policy=policy,
        policy_file=policy_file,
        scope_to_public_surface=scope_to_public_surface,
        force_public_symbols=force_public_symbols,
        pattern_verdicts=pattern_verdicts,
        collapse_versioned_symbols=collapse_versioned_symbols,
        contract_evaluation=contract_evaluation,
        contract_mode=contract_mode,
    )
    record_no_baseline_depth_evidence_contract_error(
        diff, depth, new, is_live=candidate_is_live
    )
    partition = partition_no_baseline_findings(diff.changes)
    check_no_baseline_partition(partition)
    # The suppressed half gets the identical partition -- a suppressed
    # comparison finding would be just as impossible on a self-diff, and D3
    # applies to a finding's evolution state whether or not policy later
    # hid it, so the same invariants are checked rather than waived for
    # anything a rule happened to match.
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
