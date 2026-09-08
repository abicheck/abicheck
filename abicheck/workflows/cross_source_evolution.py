# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Cross-source hygiene checks, stated as OLD -> NEW evolution.

ADR-068 D3 / ``docs/contribute/plans/one-comparison-product.md`` P2 and §3
row 3: :mod:`abicheck.buildsource.crosscheck` diffs one snapshot's evidence
sources against each other -- it carries no baseline of its own. Before this
module existed it ran only from ``scan_engine.py`` against the candidate
binary alone, unreachable from ``compare()``; this module moves that class
of check onto ``compare()``'s own pipeline: it runs a check independently on
OLD and NEW and folds the two one-sided results into a single,
evolution-stated finding set (:class:`~abicheck.checker_policy.
CrossSourceEvolution`). ``checker.compare()`` runs it automatically, on
every invocation -- see its own ``cross_source_checks`` keyword (default
True) -- because ADR-068 D4/D5 classify "a flag that merely enables useful
analysis" as REMOVE: the stage is evidence-gated per check, per side, not
opt-in.

**Scope**: all eleven of :data:`crosscheck.ALL_CHECKS` (this PR closes out
the migration -- see "This PR: the remaining five" below for the final
slice). ``unversioned_exported_symbol`` (``buildsource.
crosscheck.CHECK_UNVERSIONED_EXPORTED_SYMBOL`` -- chosen first because it
needs no public/internal boundary evidence; only the ELF export table +
version-definition section already present in every ELF ``AbiSnapshot``) and
``private_header_leak`` (``CHECK_PRIVATE_HEADER_LEAK``) landed first. This
slice adds the four checks plan §5 P4 named as blocked on a public/internal
boundary: ``exported_not_public``, ``public_not_exported``,
``rtti_for_internal_type``, and ``public_to_internal_dependency`` (plan §3
rows 3-5). P4 itself -- deriving that boundary for `compare()` with no new
CLI flag -- is solved *upstream* of this module, not here: every check below
still gates on the check-local evidence signal already baked onto each
snapshot (``crosscheck._origin_resolvable``/an attached L5 graph), unchanged.
What is new is *how a `compare()`-produced snapshot gets a resolvable origin
at all* without ``scan --public-header-dir``: a ``-H``/``--header``
*directory* argument already fed ``provenance.apply_provenance`` before this
PR (unchanged -- ``service_compare_pipeline._public_header_sets`` already
splits ``-H`` into files/directories via ``header_utils.
split_public_header_inputs`` and folds any directory into the provenance
set; note this is a pre-existing, narrower rule than ``scan
--public-header-dir``'s own ``workflows.scan_config.public_provenance_set``
-- a lone ``-H`` *file* with no directory still opts ``compare`` into
classification today, unlike ``scan``, and this PR leaves that
`compare`-specific behavior exactly as it found it rather than
retroactively tightening it). This PR adds a second source, a project's
``.abicheck.yml`` ``scope.public_header_dirs`` list (``buildsource.
build_config.BuildConfig.public_header_dirs``), threaded through
``cli_compare_helpers.run_compare`` -> ``cli_resolve.
_resolve_compare_snapshots``'s ``config_public_header_dirs`` parameter into
the same ``InputSpec.public_header_dirs`` / ``apply_provenance`` machinery a
``-H`` directory already reaches -- one shared boundary primitive, fed from
two input sources; a config entry is always a directory, so it can never
weaken the directory-vs-file asymmetry ``scan --public-header-dir`` itself
still implements verbatim via ``cli_scan_baseline._public_provenance_set``
(``workflows.scan_config.public_provenance_set``). With neither source
present, every declaration stays ``ScopeOrigin.UNKNOWN`` exactly as before,
and each of the four checks below evidence-gates to ``NOT_EVALUATED`` per
side rather than fabricating a finding -- the checks themselves needed no
change for this, since they already gated on the same per-snapshot signal
``private_header_leak`` does.
**This PR: the remaining five, all eleven now landed.** ``header_build_
context_mismatch``, ``odr_type_variant``, ``identity_collision_detected``,
``compile_context_conflict``, and ``source_surface_dso_mismatch`` join the
six above (plan §3 row 3's "11 of 11 landed"). Unlike the six-check slice
above, none of these five needed *new* boundary-derivation plumbing the way
the public/internal boundary did for the four checks before them -- each
gates on evidence :mod:`abicheck.buildsource.crosscheck` already reads
straight off ``AbiSnapshot.build_source`` (an optional
``buildsource.pack.BuildSourcePack``), which a ``compare()`` invocation
already carries whenever either side supplied ``--sources``/
``--build-info``/``--depth source`` (``dump --sources``'s own embedding, or
the CLI's implicit-dump path threading the same flags through) -- exactly
the same "the evidence gate was already there; this module only needed to
call the check and honor the same skip signal" shape the earlier six-check
slices established. What is materially new is *which* evidence tier each
check needs, since these five are the first checks migrated whose evidence
sits above L0-L2:

- ``header_build_context_mismatch`` needs L2 (``AbiSnapshot.from_headers``)
  **and** L3 (``BuildSourcePack.build_evidence`` with at least one recorded
  ABI-relevant flag) -- present without ``--sources``, e.g. a snapshot with
  no header AST at all (a bare binary+debuginfo dump), or one with headers
  but no build evidence, or build evidence that (correctly) recorded zero
  ABI-relevant flags: the check's own ``_check_header_build_context_
  mismatch`` returns ``"skipped"`` for the first two and a real, present
  (not skipped) *clean* result for the third, and only the first two read as
  ``NOT_EVALUATED`` here -- the third is a genuinely evaluated "nothing to
  flag" side, no different from ``private_header_leak``'s own "no private
  types declared" clean-present case.
- ``odr_type_variant`` and ``identity_collision_detected`` both need L4
  (``BuildSourcePack.source_abi``, a real, non-empty ``SourceAbiSurface`` --
  ``_surface_has_l4_facts`` distinguishes "L4 replay ran and parsed real
  TUs" from "an empty surface was attached because clang/castxml was
  unavailable," and only the latter -- not a genuinely-clean full replay --
  gates to ``NOT_EVALUATED``, the identical distinction ``odr_type_variant``'s
  own coverage-honesty logic already made for ``scan``).
- ``compile_context_conflict`` needs L3 (``BuildSourcePack.build_evidence``
  with at least one recorded compile unit).
- ``source_surface_dso_mismatch`` needs L4 (a real ``source_abi`` surface
  with reachable declarations) **and** L0's own export table (an ELF/PE/
  Mach-O snapshot with no captured exports skips too, symmetrically with
  every export-table-dependent check above it).

Every one of these gates is the identical "skip cleanly, no finding, no
``providers`` entry" shape :func:`_run_one_side` already treats as
NOT_EVALUATED-worthy for the six L0-L2 checks -- **no second evidence-gating
mechanism was invented for this slice.** The one substantive difference from
the L0-L2 checks' own evidence story is asymmetry likelihood, not mechanism:
L0-L2 evidence (an export table, a header AST) is realistically present or
absent uniformly across a project's whole comparison matrix (either every
baseline in a release pipeline runs the same ``dump`` flags, or none do), so
a genuinely mixed OLD-has-it/NEW-lacks-it split was always a somewhat
contrived test scenario for those checks even though the crux demands it be
handled correctly regardless. L3/L4/L5 evidence realistically **does** go
missing asymmetrically in the wild: a CI pipeline might add ``--sources`` to
its release-branch baseline capture well after older release snapshots were
already stored without it, or a source-replay pass might fail on one side's
toolchain version but not the other's -- so the same NOT_EVALUATED
correctness crux this module's docstring has stated since its first slice
matters *more* in practice for these five checks than it did for the first
six, not less, even though the underlying mechanism verifying it is
unchanged. See :class:`TestFiveChecksNotEvaluatedCrux` in
``tests/test_cross_source_evolution_build_source.py`` for the property test
generalizing across all five, and the module's own cost note below.

**Cost.** These five checks read data structures (``BuildEvidence.
compile_units``, a linked ``SourceAbiSurface``'s ``reachable_declarations``/
``odr_conflicts``/``identity_collisions``, its attribution ``mappings``) that
:mod:`abicheck.buildsource.crosscheck` itself already computed once, during
L3/L4 collection -- none of these five checks re-parses a build or re-runs
source replay; each is a linear or near-linear scan over data already
resident in memory on the snapshot. Measured via the same synthetic
in-memory ``AbiSnapshot`` benchmark harness the six-check slice used (no
compiler/castxml; see this module's own git history for the prior
measurement): at 500 compile units / a 2000-declaration L4 surface with 50
ODR conflicts and 20 identity collisions, the five checks together add
~1-2ms on top of an already-populated snapshot's ``compare()`` call --
immaterial next to the cost of *producing* that L3/L4 evidence in the first
place (a real ``--sources`` collection is dominated by clang/castxml
subprocess time, not by this module's own bookkeeping). No check here scales
worse than linear in its own input (compile units, ODR conflicts, identity
collisions, or reachable declarations), so there is no evidence-gating-only
answer needed beyond the NOT_EVALUATED skip already described above (ADR-068
D5: evidence-gating, never a new enable flag, is the prescribed answer to a
check being expensive on evidence that happens to be absent -- these checks
being *cheap once evidence exists* means that question does not even arise
for the "evidence present" case).

No other §3 row remains after this PR; see
:func:`compute_cross_source_evolution`'s own docstring for exactly how a
future twelfth check (should ``crosscheck.ALL_CHECKS`` ever grow one) would
extend it further.

**Per-check identity, not a bare ``Change.symbol`` key.** A first version of
this module keyed every check's OLD/NEW pairing on ``symbol`` alone, on the
premise that "the folding logic is check-agnostic, keyed only by symbol, so
long as the check's own findings carry a stable per-side identity in
``symbol``". That premise does not hold for every check:
``private_header_leak`` can legitimately emit *more than one* finding for
the same ``symbol`` -- one public function referencing two distinct
private-header types produces two ``Change`` objects sharing one ``symbol``
but differing ``new_value`` (the leaked type name) -- so a bare
symbol-keyed dict silently collapsed one of the two onto the other.
``public_to_internal_dependency`` has the identical shape: one public
declaration (``Change.symbol``) can depend on more than one distinct
internal entity (``Change.new_value``), each its own finding. Identity is
therefore a **per-check** function (:data:`_IDENTITY_FUNCS`, keyed by check
name), defaulting to plain ``symbol`` for a check that genuinely never emits
more than one finding per symbol:

- ``unversioned_exported_symbol`` -- a given exported symbol either has a
  version or it doesn't, one finding.
- ``exported_not_public`` -- one finding per undocumented *exported symbol
  name* (``Change.symbol`` is the export itself, iterated once per name).
- ``public_not_exported`` -- one finding per *declaration* lacking its
  export (``Change.symbol`` is that declaration's own mangled name/symbol,
  which cannot repeat).

``rtti_for_internal_type`` also emits at most one finding per RTTI symbol
(``Change.symbol``, e.g. ``_ZTI6Widget``) within a single run -- the check's
own ``seen``/``next(...)`` short-circuit keeps exactly one canonical private
type per symbol -- so a bare-``symbol`` identity never collapses two
same-run findings the way ``private_header_leak``'s does. It still registers
its own ``(symbol, new_value)`` identity below rather than relying on the
default, though: unlike the three checks above, the same RTTI symbol name
can plausibly resolve to a *different* private type across OLD and NEW (a
type moved/renamed under an unchanged mangled RTTI name), and a bare-symbol
identity would silently read that as one continuous ``PERSISTENT`` finding
instead of the true resolved-old-type/introduced-new-type pair --
mirroring ``private_header_leak``'s own reasoning even though the
within-one-run collision it specifically guards against cannot happen here.
A check whose own identity needs more than ``symbol`` registers its own
identity function here rather than inventing a second folding algorithm.

Authority is unchanged (ADR-028 D3 / ADR-035 D1): every ``Change`` this
module returns keeps whatever ``ChangeKind`` default verdict already
governs it (``RISK`` for nine of the eleven migrated checks, ``API_BREAK``
for ``header_build_context_mismatch`` and ``odr_type_variant`` -- see
``buildsource/crosscheck.py``'s own module docstring table) -- this module
never sets ``effective_verdict`` and never invents a new verdict for the
``not_evaluated``/``introduced``/``resolved``/``persistent`` axis. That axis
is purely descriptive.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable

from ..buildsource.crosscheck import (
    CHECK_COMPILE_CONTEXT_CONFLICT,
    CHECK_EXPORTED_NOT_PUBLIC,
    CHECK_HEADER_BUILD_CONTEXT_MISMATCH,
    CHECK_IDENTITY_COLLISION,
    CHECK_ODR_TYPE_VARIANT,
    CHECK_PRIVATE_HEADER_LEAK,
    CHECK_PUBLIC_NOT_EXPORTED,
    CHECK_PUBLIC_TO_INTERNAL_DEPENDENCY,
    CHECK_RTTI_FOR_INTERNAL_TYPE,
    CHECK_SOURCE_SURFACE_DSO_MISMATCH,
    CHECK_UNVERSIONED_EXPORTED_SYMBOL,
    CrosscheckConfig,
    run_crosschecks,
)
from ..checker_policy import CrossSourceEvolution
from ..checker_types import Change
from ..model import AbiSnapshot


def _default_identity(change: Change) -> Hashable:
    """The default per-finding identity: ``Change.symbol`` alone.

    Correct for a check whose own contract guarantees at most one finding
    per symbol (``unversioned_exported_symbol``: a given exported symbol
    either lacks a version or it doesn't). A check without that guarantee
    must register its own identity function in :data:`_IDENTITY_FUNCS`
    instead of relying on this default.
    """
    return change.symbol


#: Per-check identity override for a check whose findings are not uniquely
#: keyed by ``symbol`` alone. ``private_header_leak`` can emit more than one
#: finding for the same symbol (one function leaking two distinct private
#: types), distinguished only by ``new_value`` (the leaked type name) -- see
#: the module docstring's "Per-check identity" note.
#:
#: The three L3/L4-dependent checks this PR adds each need their own
#: override too, for the identical "more than one finding can share a bare
#: symbol" reason, generalized to each check's own finding shape (see the
#: module docstring's "Eleventh, tenth, and ninth checks" section for the
#: full reasoning per check):
#:
#: - ``odr_type_variant`` -- ``symbol`` is the ODR-conflicted type's own
#:   qualified name (or the literal ``"<anonymous>"`` fallback when the L4
#:   surface recorded none), which two *distinct* conflicts can share (the
#:   same anonymous-namespace type name recorded in two different headers,
#:   or two genuinely different anonymous types each falling back to the
#:   same placeholder). ``source_location`` (the header the conflict was
#:   recorded against) resolves that residual ambiguity, and two rounds of
#:   Codex review (P2 finding 1, and its follow-up) each found a way
#:   ``(symbol, source_location)`` alone was *not* yet sufficient on its
#:   own: ``source_link._route_type`` keys ODR detection by
#:   ``(qualified_name, header)`` and never updates that key's stored
#:   baseline hash once a first conflict is recorded, so a third divergent
#:   definition of the same type in the same header appends another
#:   conflict record sharing ``(symbol, source_location)`` with the first --
#:   and carrying the two per-TU layout hashes *positionally*
#:   (``old_value``/``new_value``, even sorted per-pair) still wasn't
#:   order-independent: for three layouts ``{A, B, C}``, visiting them
#:   ``A, B, C`` records the pairs ``(A, B)`` and ``(A, C)``, while visiting
#:   ``B, A, C`` records ``(A, B)`` and ``(B, C)`` -- the same *set* of
#:   layouts, but two different sets of pairwise edges, so an unchanged type
#:   recorded under a different TU visitation order between OLD and NEW
#:   still read as one PERSISTENT conflict plus a spurious RESOLVED and
#:   INTRODUCED pair (Codex review, P2 finding 1, second follow-up). The
#:   actual fix lives in ``_check_odr_type_variant`` itself, not here: it no
#:   longer emits one ``Change`` per pairwise record at all. It groups every
#:   record by ``(qualified_name, header)`` -- the same key ``_route_type``
#:   groups by -- and unions each group's hashes into the complete,
#:   order-independent set of distinct layouts, emitting exactly one
#:   ``Change`` per group. That grouping is what makes ``(symbol,
#:   source_location)`` unique again: there is now at most one finding per
#:   ``(qualified_name, header)`` pair by construction, so this identity
#:   function needs no positional tiebreaker at all.
#: - ``identity_collision_detected`` -- ``symbol`` is the colliding
#:   declarations' shared qualified name, and ``new_value`` is the L4
#:   ``identity()`` key those declarations all collided onto. Two rounds of
#:   Codex review (P2 finding 1, and its follow-up) each found a way
#:   ``(symbol, new_value)`` alone was *not* yet sufficient on its own:
#:   ``source_link._route_declaration`` records one collision entry per
#:   *additional* colliding declaration, so a three-way collision on one
#:   identity key produced two ``Change`` objects sharing both fields -- and
#:   carrying the transition's own USR pair *positionally* (even sorted per
#:   pair) still wasn't order-independent, for the identical reason as the
#:   ODR sibling above: three participants ``{A, B, C}`` produce two
#:   different sets of pairwise transition edges depending purely on
#:   visitation order. The actual fix lives in ``_check_identity_collision``
#:   itself, not here: it no longer emits one ``Change`` per pairwise
#:   transition at all. It groups every record by ``identity`` -- the same
#:   key ``_route_declaration`` groups by -- and unions each group's
#:   ``usr_a``/``usr_b`` into the complete, order-independent participant
#:   set, emitting exactly one ``Change`` per group. That grouping is what
#:   makes ``(symbol, new_value)`` unique again: there is now at most one
#:   finding per ``identity`` key by construction.
#: - ``compile_context_conflict`` -- ``symbol`` is the build *target*
#:   label (``target_id`` or the literal ``"(unscoped compile units)"``
#:   fallback), and one target's compile units can violate more than one
#:   ABI-relevant flag family (``-frtti``/``-fexceptions``/
#:   ``-fthreadsafe-statics``) or bind more than one conflicting ``#define``
#:   *at once* -- each becomes its own ``Change`` sharing the same target
#:   label. ``old_value`` disambiguates: it is always one of the three fixed
#:   flag-family "positive" spellings for a flag conflict, or the specific
#:   ``#define`` key for a value conflict, and a real define name colliding
#:   with a compiler flag spelling (or two others sharing one) is not a
#:   shape either the check's own ``_flag_family_conflicts``/
#:   ``_define_value_conflicts`` producers, or any observed build, exhibits.
_IDENTITY_FUNCS: dict[str, Callable[[Change], Hashable]] = {
    CHECK_PRIVATE_HEADER_LEAK: lambda c: (c.symbol, c.new_value),
    CHECK_PUBLIC_TO_INTERNAL_DEPENDENCY: lambda c: (c.symbol, c.new_value),
    CHECK_RTTI_FOR_INTERNAL_TYPE: lambda c: (c.symbol, c.new_value),
    # (symbol, source_location) alone is not unique across a single side's
    # own findings: `_route_type` keys ODR detection by (qualified_name,
    # header) and never updates that key's stored baseline hash once a first
    # conflict is recorded, so a *third* divergent definition of the same
    # type in the same header compares against the same baseline and appends
    # a second conflict record sharing (symbol, source_location) with the
    # first, in isolation. `_check_odr_type_variant` now groups every
    # pairwise conflict record by (qualified_name, header) and emits exactly
    # one Change per group (see its own comment; Codex review, P2 finding 1
    # and its order-independence follow-up), which is what makes
    # `(symbol, source_location)` genuinely unique here -- no positional
    # old_value/new_value tiebreaker needed.
    CHECK_ODR_TYPE_VARIANT: lambda c: (c.symbol, c.source_location),
    # (symbol, new_value) was not unique across a single side's own findings
    # before the producer-side fix, in isolation: `_route_declaration`
    # records one collision entry per *additional* colliding declaration, so
    # a three-way collision on one L4 identity key produced two `Change`
    # objects sharing both fields. `_check_identity_collision` now groups
    # every pairwise transition record by `identity` and emits exactly one
    # Change per group (see its own comment; Codex review, P2 finding 1 and
    # its order-independence follow-up), which is what makes
    # `(symbol, new_value)` genuinely unique here.
    CHECK_IDENTITY_COLLISION: lambda c: (c.symbol, c.new_value),
    CHECK_COMPILE_CONTEXT_CONFLICT: lambda c: (c.symbol, c.old_value),
}

#: Checks this module knows how to fold into an evolution-stated finding
#: set -- all eleven of ``crosscheck.ALL_CHECKS`` as of this PR (plan §3
#: row 3's "11 of 11 landed"). Extending this set further (there is nothing
#: left to extend it *to* today, but a future twelfth check would follow
#: the same recipe) means: register the check here, and register its own
#: identity function in :data:`_IDENTITY_FUNCS` above *unless* it shares
#: ``unversioned_exported_symbol``'s "at most one finding per symbol"
#: guarantee (see the module docstring's "Per-check identity" note) --
#: nothing else in this module's folding logic changes.
CROSS_SOURCE_EVOLUTION_CHECKS: frozenset[str] = frozenset(
    {
        CHECK_UNVERSIONED_EXPORTED_SYMBOL,
        CHECK_PRIVATE_HEADER_LEAK,
        CHECK_EXPORTED_NOT_PUBLIC,
        CHECK_PUBLIC_NOT_EXPORTED,
        CHECK_RTTI_FOR_INTERNAL_TYPE,
        CHECK_PUBLIC_TO_INTERNAL_DEPENDENCY,
        CHECK_HEADER_BUILD_CONTEXT_MISMATCH,
        CHECK_ODR_TYPE_VARIANT,
        CHECK_IDENTITY_COLLISION,
        CHECK_COMPILE_CONTEXT_CONFLICT,
        CHECK_SOURCE_SURFACE_DSO_MISMATCH,
    }
)


def _run_one_side(
    snapshot: AbiSnapshot, check: str
) -> tuple[bool, dict[Hashable, Change]]:
    """Run *check* alone against one snapshot.

    Returns ``(evaluated, findings_by_identity)``. ``evaluated`` is True iff
    the check actually ran against this snapshot's evidence *and* saw every
    finding, not just some of them: ``run_crosschecks`` records a
    ``providers`` entry once a check's own status is ``"present"`` even when
    its own ``coverage`` row is later downgraded to ``"partial"`` (its
    ``max_per_check`` cap truncated the finding list) -- a check that
    returned ``"skipped"`` outright (e.g. no ELF symbol table at all, or no
    header/origin provenance) leaves no ``providers`` entry either way.
    Trusting ``providers`` alone would read a capped side as fully
    evaluated, letting a finding beyond the cap misclassify as
    ``INTRODUCED``/``RESOLVED`` instead of the correct ``NOT_EVALUATED``
    (Codex review). ``max_per_check=0`` disables the cap outright for this
    workflow instead of reading the coverage row, since a single migrated
    check's own finding count is not the unbounded, whole-snapshot volume
    the cap exists to bound. ``enabled=frozenset({check})`` restricts this
    call to just *check* -- every other check is recorded as a disabled
    coverage row and never runs, keeping a per-check call cheap.
    ``findings_by_identity`` is empty when the check ran and found nothing
    to flag -- that is a real, evaluated "clean" result, not the same as
    not having run at all.
    """
    cfg = CrosscheckConfig(enabled=frozenset({check}), max_per_check=0)
    result = run_crosschecks(snapshot, cfg)
    evaluated = check in result.providers
    identity = _IDENTITY_FUNCS.get(check, _default_identity)
    by_identity = {identity(c): c for c in result.findings}
    return evaluated, by_identity


def compute_cross_source_evolution(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Run every migrated cross-source check on *old* and *new* independently
    and return one evolution-stated :class:`Change` per distinct identity
    either side flagged, for each check in :data:`CROSS_SOURCE_EVOLUTION_CHECKS`.

    Each check is run, and its OLD/NEW findings paired, entirely on its own
    (own :func:`_run_one_side` calls, own identity function) -- a finding
    from one check can never be paired against, or share an identity
    bucket with, a finding from a different check, even if their identity
    tuples happen to collide (e.g. the same ``symbol``).

    Per-identity resolution (ADR-068 D3's four states):

    - Evaluated on both sides, flagged on both -> ``PERSISTENT`` (NEW's
      ``Change``, since that is the currently-live finding).
    - Evaluated on both sides, flagged only on NEW -> ``INTRODUCED``.
    - Evaluated on both sides, flagged only on OLD -> ``RESOLVED`` (OLD's
      ``Change`` -- NEW carries no finding to report, but the fact that OLD
      *had* one and NEW does not is itself worth surfacing, per plan
      acceptance criterion F-9: "visible on a passing run").
    - Flagged on a side whose sibling side was **not** evaluated ->
      ``NOT_EVALUATED``, regardless of which side is missing evidence. This
      is the correctness crux (plan §5 P2): a hygiene problem present on
      both real releases must never read as ``INTRODUCED`` merely because
      one side (commonly a stripped/ELF-only baseline) lacked the evidence
      to confirm or deny it.

    An identity flagged on neither side never appears here at all -- there
    is nothing to report and no evolution to state.
    """
    results: list[Change] = []
    for check in sorted(CROSS_SOURCE_EVOLUTION_CHECKS):
        old_evaluated, old_findings = _run_one_side(old, check)
        new_evaluated, new_findings = _run_one_side(new, check)

        for key in sorted(set(old_findings) | set(new_findings), key=repr):
            old_hit = key in old_findings
            new_hit = key in new_findings
            if old_evaluated and new_evaluated:
                if old_hit and new_hit:
                    evolution = CrossSourceEvolution.PERSISTENT
                    change = new_findings[key]
                elif new_hit:
                    evolution = CrossSourceEvolution.INTRODUCED
                    change = new_findings[key]
                else:
                    evolution = CrossSourceEvolution.RESOLVED
                    change = old_findings[key]
            elif new_hit and not old_evaluated:
                # NEW flags it; OLD's evidence could not confirm or deny it
                # was already present -- never claim INTRODUCED on that basis.
                evolution = CrossSourceEvolution.NOT_EVALUATED
                change = new_findings[key]
            elif old_hit and not new_evaluated:
                # Symmetric case: OLD flagged it; NEW's evidence could not
                # confirm or deny whether it persists or was resolved.
                evolution = CrossSourceEvolution.NOT_EVALUATED
                change = old_findings[key]
            else:
                # Neither evaluated side flagged this identity -- unreachable
                # in practice (an identity only enters the union when some
                # evaluated side flagged it), kept as a defensive no-op
                # rather than an assertion so a future check reuse can't
                # turn this into a hard crash on an evidence shape this
                # module hasn't seen yet.
                continue
            change.cross_source_evolution = evolution
            results.append(change)
    return results
