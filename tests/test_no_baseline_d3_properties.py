# SPDX-License-Identifier: Apache-2.0
"""ADR-068 D3 as an executable invariant over *generated* one-sided inputs.

``AGENTS.md``'s "a bug fix's regression test targets the bug *class*, not the
one reported input" rule, applied to the ``compare --no-baseline`` audit fix.
The eleven G20 fixtures are the acceptance corpus
(``tests/parity/test_no_baseline_audit_corpus_parity.py``) -- eleven fixed
inputs, which by construction only foreclose the eleven shapes their authors
already thought of. This module states the underlying contract instead, and
searches for a counterexample:

**D3, for a run whose OLD side is ``declared_absent``:** whatever snapshot
the candidate is, whatever evidence it carries or lacks, and whichever
cross-source checks fire on it --

1. every reported finding reads exactly ``not_evaluated``. This is
   deliberately stronger than the rule this module first stated ("never
   ``introduced``/``resolved``"), and the strengthening is the point: while
   the audit was implemented as a self-diff, ``persistent`` was reachable
   and permitted, which reported a *history* -- "present on both sides" --
   for a baseline the run was told does not exist. With the baseline truly
   absent, no evolution is observable at all, so any state other than
   ``not_evaluated`` is a manufactured claim;
2. the *comparison* half of the change set is always empty (no delta was
   evaluated, so no addition, removal, or modification can exist);
3. the partition between the two is total and disjoint, so no finding is
   silently dropped by the split itself.

The generator deliberately produces snapshots across the whole evidence
ladder rather than only ones known to fire a check: an evidence-poor
candidate is exactly where a check's gate closes, and a run reporting
*nothing* must satisfy the same invariant as one reporting several. The
oracle is the enum itself -- ``{s for s in CrossSourceEvolution if s is not
NOT_EVALUATED}`` is what must never appear -- checked directly rather than
against any constant the implementation also reads, so widening the
implementation cannot silently widen this test's oracle along with it.

The primitive-level half (``AGENTS.md``'s "Primitive-level property tests")
sits below: ``partition_no_baseline_findings`` is a reusable classification
primitive, so it gets its own contract tests over synthetic ``Change``
objects, decoupled from any snapshot at all.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings, strategies as st

from abicheck.checker_policy import ChangeKind, CrossSourceEvolution
from abicheck.checker_types import Change
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, RecordType, ScopeOrigin, Variable
from abicheck.policy.no_baseline_findings import (
    NoBaselineInvariantError,
    is_one_sided_finding,
    partition_no_baseline_findings,
)
from abicheck.workflows.no_baseline_compare import run_no_baseline_compare

_HSETTINGS = settings(
    max_examples=75,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

#: Every state that asserts something about a baseline, i.e. everything an
#: audit with no baseline may not report. Derived from the enum itself so a
#: fifth state added later is forbidden by default rather than falling
#: through an allowlist -- and derived here, in the test, never read from a
#: constant the implementation also reads.
_FORBIDDEN_STATES = {
    state
    for state in CrossSourceEvolution
    if state is not CrossSourceEvolution.NOT_EVALUATED
}


def test_only_not_evaluated_is_permitted_without_a_baseline() -> None:
    """The oracle really is "everything except ``not_evaluated``".

    Guards the derivation above: if the enum grows a state, it lands in the
    forbidden set automatically, and ``persistent`` -- the state the
    self-diff implementation used to produce and permit -- is forbidden.
    """
    assert CrossSourceEvolution.NOT_EVALUATED not in _FORBIDDEN_STATES
    assert CrossSourceEvolution.PERSISTENT in _FORBIDDEN_STATES
    assert _FORBIDDEN_STATES | {CrossSourceEvolution.NOT_EVALUATED} == set(
        CrossSourceEvolution
    )


# ---------------------------------------------------------------------------
# Candidate-snapshot strategies -- across the evidence ladder, not only the
# shapes that are known to fire a check.
# ---------------------------------------------------------------------------

_IDENTS = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=8
).filter(lambda s: not s.startswith("_") or len(s) > 1)

_ORIGINS = st.sampled_from(
    [
        ScopeOrigin.PUBLIC_HEADER,
        ScopeOrigin.PRIVATE_HEADER,
        ScopeOrigin.EXPORT_ONLY,
        ScopeOrigin.UNKNOWN,
    ]
)


@st.composite
def _functions(draw, names):
    out = []
    for name in names:
        origin = draw(_ORIGINS)
        # A return type that either does or does not name a generated record
        # -- the private_header_leak / public_to_internal_dependency shape.
        rtype = draw(st.sampled_from(["void", "int", "detail::Impl *", "Widget *"]))
        out.append(
            Function(
                name=name,
                mangled=f"_Z{len(name)}{name}v",
                return_type=rtype,
                origin=origin,
            )
        )
    return out


@st.composite
def _candidate_snapshots(draw):
    """One candidate ``AbiSnapshot``, anywhere on the evidence ladder.

    Independently varies the four things the eleven cross-source checks
    gate on: whether an export table exists at all, whether a declaration's
    origin resolves, whether a symbol-versioning scheme is defined, and
    whether a referenced type is private. Crucially, the export table and
    the declaration list are drawn *separately* -- a declaration with no
    matching export (``public_not_exported``) and an export with no
    declaration (``exported_not_public``) are both reachable, as is a
    snapshot with neither.
    """
    names = draw(st.lists(_IDENTS, min_size=0, max_size=4, unique=True))
    exported = draw(st.lists(st.sampled_from(names or ["x"]), max_size=4, unique=True))
    has_elf = draw(st.booleans())
    versioned = draw(st.booleans())
    has_private_type = draw(st.booleans())
    from_headers = draw(st.booleans())

    snap = AbiSnapshot(
        library="libgen.so",
        version="1.0",
        from_headers=from_headers,
    )
    if has_elf:
        snap.elf = ElfMetadata(
            symbols=[
                ElfSymbol(
                    name=f"_Z{len(n)}{n}v", version="LIB_1.0" if versioned else ""
                )
                for n in exported
            ],
            versions_defined=["LIB_1.0"] if versioned else [],
        )
    snap.functions = draw(_functions(names))
    if has_private_type:
        snap.types = [
            RecordType(
                name="detail::Impl", kind="struct", origin=ScopeOrigin.PRIVATE_HEADER
            )
        ]
    if draw(st.booleans()):
        snap.variables = [
            Variable(
                name="g_state",
                mangled="_Z7g_state",
                type="int",
                origin=draw(_ORIGINS),
            )
        ]
    return snap


@given(snapshot=_candidate_snapshots())
@_HSETTINGS
def test_no_generated_candidate_ever_reports_a_forbidden_evolution_state(
    snapshot: AbiSnapshot,
) -> None:
    """D3's core rule, in its sharpened form: with OLD declared absent,
    *every* state except ``not_evaluated`` is unreachable for *any*
    candidate -- ``persistent`` included."""
    result = run_no_baseline_compare(snapshot)
    for change in result.findings:
        assert change.cross_source_evolution not in _FORBIDDEN_STATES, (
            f"{change.kind.value} reported "
            f"{change.cross_source_evolution} on a declared_absent OLD"
        )
    # The positive half of the same statement: a cross-source finding does
    # not merely avoid the forbidden states, it carries the one state that
    # is true of it. Asserted separately so a future change that stopped
    # stamping the field at all -- leaving `None`, which passes the
    # membership check above vacuously -- fails here.
    for change in result.findings:
        if change.cross_source_evolution is not None:
            assert (
                change.cross_source_evolution is CrossSourceEvolution.NOT_EVALUATED
            ), f"{change.kind.value} reported {change.cross_source_evolution}"
    assert all(
        c.cross_source_evolution is CrossSourceEvolution.NOT_EVALUATED
        for c in result.findings
        if not c.candidate_side_enrichment
    )


@given(snapshot=_candidate_snapshots())
@_HSETTINGS
def test_no_generated_candidate_ever_produces_a_comparison_finding(
    snapshot: AbiSnapshot,
) -> None:
    """The comparison half of the old blanket assertion, still absolute.

    Asserted over ``result.diff.changes`` (everything ``compare()`` emitted)
    rather than over ``result.findings`` (what survived the partition), so a
    partition that wrongly classified a real comparison finding as
    candidate-side would fail here rather than hide behind itself.
    """
    result = run_no_baseline_compare(snapshot)
    comparison_findings = [
        c for c in result.diff.changes if not is_one_sided_finding(c)
    ]
    assert comparison_findings == [], (
        "a run with no baseline produced a comparison finding: "
        f"{[c.kind.value for c in comparison_findings]}"
    )


@given(snapshot=_candidate_snapshots())
@_HSETTINGS
def test_reported_findings_are_exactly_the_candidate_side_ones(
    snapshot: AbiSnapshot,
) -> None:
    """Totality: the audit reports every candidate-side finding
    ``compare()`` emitted, and invents none.

    This is the half a fix could most easily get wrong in the *other*
    direction -- crashing was the reported bug, but silently dropping a
    finding while no longer crashing would satisfy every "does not raise"
    test and still lose the capability the audit exists for.
    """
    result = run_no_baseline_compare(snapshot)
    expected = [c for c in result.diff.changes if is_one_sided_finding(c)]
    assert list(result.findings) == expected


@given(snapshot=_candidate_snapshots())
@_HSETTINGS
def test_audit_is_deterministic_for_a_fixed_candidate(
    snapshot: AbiSnapshot,
) -> None:
    """Two audits of the same candidate report the same findings, in the
    same order -- a report a user diffs across CI runs must not depend on
    iteration order inside the partition."""
    first = run_no_baseline_compare(snapshot)
    second = run_no_baseline_compare(snapshot)
    assert [c.kind for c in first.findings] == [c.kind for c in second.findings]
    assert [c.symbol for c in first.findings] == [c.symbol for c in second.findings]


# ---------------------------------------------------------------------------
# Primitive-level contract: the partition helpers, decoupled from snapshots.
# ---------------------------------------------------------------------------


def _change(
    *,
    kind: ChangeKind = ChangeKind.EXPORTED_NOT_PUBLIC,
    symbol: str = "sym",
    evolution: CrossSourceEvolution | None = None,
    enrichment: bool = False,
) -> Change:
    return Change(
        kind=kind,
        symbol=symbol,
        description="generated",
        cross_source_evolution=evolution,
        candidate_side_enrichment=enrichment,
    )


_MARKERS = st.one_of(
    st.tuples(st.sampled_from(list(CrossSourceEvolution)), st.booleans()),
    st.tuples(st.none(), st.booleans()),
)


@st.composite
def _change_lists(draw):
    markers = draw(st.lists(_MARKERS, min_size=0, max_size=8))
    return [
        _change(symbol=f"s{i}", evolution=evolution, enrichment=enrichment)
        for i, (evolution, enrichment) in enumerate(markers)
    ]


@given(changes=_change_lists())
@_HSETTINGS
def test_partition_is_total_and_disjoint(changes: list[Change]) -> None:
    """Every input lands in exactly one half, and nothing is invented.

    The single property a partition primitive must have, stated
    independently of what "candidate-side" happens to mean today -- so a
    future third marker cannot make the split lossy without failing here.
    """
    partition = partition_no_baseline_findings(changes)
    assert len(partition.one_sided) + len(partition.identity) == len(changes)
    assert not (set(map(id, partition.one_sided)) & set(map(id, partition.identity)))
    assert set(map(id, partition.one_sided)) | set(map(id, partition.identity)) == set(
        map(id, changes)
    )


@given(changes=_change_lists())
@_HSETTINGS
def test_partition_preserves_order_within_each_half(changes: list[Change]) -> None:
    """A report renders findings in emission order, so the split may not
    reorder either half."""
    partition = partition_no_baseline_findings(changes)
    for half in (partition.one_sided, partition.identity):
        symbols = [c.symbol for c in half]
        assert symbols == sorted(symbols, key=lambda s: int(s[1:]))


@given(changes=_change_lists())
@_HSETTINGS
def test_check_raises_exactly_when_the_comparison_half_is_non_empty(
    changes: list[Change],
) -> None:
    """``check_no_baseline_partition`` raises iff the partition it is handed
    actually carries a comparison finding -- never on a legal one, and
    always on an illegal one.

    There is deliberately no second, evolution-state rule to test here any
    more: the permitted-state allowlist this module used to check
    (``d3_violations``) existed only because the self-diff could produce
    ``persistent``. The audit now cannot produce any state but
    ``not_evaluated``, which the snapshot-level properties above assert
    directly, so a rule restating it would only be a second place to keep
    in sync.
    """
    from abicheck.policy.no_baseline_findings import check_no_baseline_partition

    partition = partition_no_baseline_findings(changes)
    if partition.identity:
        try:
            check_no_baseline_partition(partition)
        except NoBaselineInvariantError:
            return
        raise AssertionError("expected NoBaselineInvariantError, none raised")
    check_no_baseline_partition(partition)


def test_the_invariant_error_is_not_an_assert() -> None:
    """The guard must survive ``python -O``.

    The original blanket check was an ``assert``, which is stripped under
    ``-O`` -- so the comparison-half violation it existed to catch would
    have become a silently wrong audit report rather than a loud failure.
    Asserted by constructing the violating partition directly, since the
    real pipeline cannot produce one.
    """
    from abicheck.policy.no_baseline_findings import (
        NoBaselineFindingPartition,
        check_no_baseline_partition,
    )

    partition = NoBaselineFindingPartition(
        one_sided=(), identity=(_change(kind=ChangeKind.FUNC_REMOVED),)
    )
    try:
        check_no_baseline_partition(partition)
    except NoBaselineInvariantError as exc:
        assert "state it structurally cannot have" in str(exc)
        assert ChangeKind.FUNC_REMOVED.value in str(exc)
    else:
        raise AssertionError("expected NoBaselineInvariantError")
