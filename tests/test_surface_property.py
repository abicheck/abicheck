"""Property-based tests for ADR-024 public-ABI surface scoping.

ADR-024 §"Validation & testing strategy" §3 calls for property-based
guarantees — described there as among the most important — that the
header-scope filter only ever *removes or demotes* findings and never
*invents* them:

* **Order-independence / idempotence** — the resolved surface does not
  depend on the order of functions/types in the snapshot, and resolving
  twice yields the same answer.
* **Monotonicity** — adding a purely-private (``ELF_ONLY``) symbol, or an
  internal type that no public API references, never changes the set of
  *public* findings.
* **Subset** — the scoped finding set is a subset of the universe of
  findings the unscoped run produces (filtering relocates, never invents).
* **Anti-hiding** (§4, "the most important") — a layout break on a type
  reachable from a public API always remains a reported finding.
* **Widening** (§D6) — force-including every demoted symbol only moves
  findings from the ledger back into the report; it never hides or invents.

These run on synthetic snapshots (no castxml/gcc) but are Hypothesis-driven
and therefore carry the ``slow`` marker like the other property suites.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from abicheck.checker import compare
from abicheck.checker_types import Change
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.surface import compute_public_surface

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# Strategies — small, surface-relevant snapshots
# ---------------------------------------------------------------------------

# A fixed pool of type names keeps the reachability graph dense enough that
# the public/private split is actually exercised (some types reachable, some
# orphaned) rather than every type being trivially unreferenced.
_TYPE_POOL = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"]
_type_ref_st = st.sampled_from(_TYPE_POOL + ["int", "void"])
_ident_st = st.sampled_from([f"sym{i}" for i in range(8)])


@st.composite
def _record_st(draw, name: str) -> RecordType:
    fields = draw(
        st.lists(
            st.tuples(st.sampled_from(["f0", "f1", "f2"]), _type_ref_st),
            min_size=0,
            max_size=3,
        )
    )
    bases = draw(st.lists(st.sampled_from(_TYPE_POOL), min_size=0, max_size=2))
    return RecordType(
        name=name,
        kind="struct",
        size_bits=draw(st.sampled_from([32, 64, 128])),
        fields=[TypeField(name=n, type=t) for n, t in fields],
        bases=bases,
    )


@st.composite
def _function_st(draw, name: str) -> Function:
    params = draw(st.lists(_type_ref_st, min_size=0, max_size=3))
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}",
        return_type=draw(_type_ref_st),
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        # Mix of exported-public and ELF-only so the surface is non-trivial.
        visibility=draw(st.sampled_from([Visibility.PUBLIC, Visibility.ELF_ONLY])),
    )


@st.composite
def _snapshot_st(draw, *, version: str) -> AbiSnapshot:
    fn_names = draw(st.lists(_ident_st, min_size=1, max_size=5, unique=True))
    type_names = draw(st.lists(st.sampled_from(_TYPE_POOL), min_size=0, max_size=5, unique=True))
    return AbiSnapshot(
        library="lib",
        version=version,
        functions=[draw(_function_st(n)) for n in fn_names],
        types=[draw(_record_st(n)) for n in type_names],
    )


def _change_keys(changes: list[Change]) -> set[tuple[str, str]]:
    return {(c.kind.value, c.symbol) for c in changes}


def _all_finding_keys(result) -> set[tuple[str, str]]:
    """Every finding the run produced, wherever it landed.

    Scoping relocates findings between buckets (e.g. a redundant root that is
    filtered out leaves its dependents in ``changes``), so the meaningful
    universe is the union of all buckets, not just ``changes``.
    """
    return (
        _change_keys(result.changes)
        | _change_keys(result.redundant_changes)
        | _change_keys(result.suppressed_changes)
        | _change_keys(result.out_of_surface_changes)
    )


# ---------------------------------------------------------------------------
# Order-independence / idempotence of surface resolution
# ---------------------------------------------------------------------------


@given(snap=_snapshot_st(version="1"))
@settings(max_examples=75)
def test_surface_resolution_is_order_independent(snap: AbiSnapshot):
    """Resolved surface is invariant under reordering functions/types."""
    base = compute_public_surface(snap)

    shuffled = copy.deepcopy(snap)
    shuffled.functions.reverse()
    shuffled.types.reverse()
    other = compute_public_surface(shuffled)

    assert base.resolvable == other.resolvable
    assert base.public_symbols == other.public_symbols
    assert base.public_types == other.public_types
    assert base.all_symbols == other.all_symbols
    assert base.all_types == other.all_types


@given(snap=_snapshot_st(version="1"))
@settings(max_examples=50)
def test_surface_resolution_is_idempotent(snap: AbiSnapshot):
    """Resolving the same snapshot twice yields identical surfaces."""
    a = compute_public_surface(snap)
    b = compute_public_surface(snap)
    assert a.public_symbols == b.public_symbols
    assert a.public_types == b.public_types
    assert a.resolvable == b.resolvable


# ---------------------------------------------------------------------------
# Monotonicity — adding purely-private entities changes no public finding
# ---------------------------------------------------------------------------


@given(
    old=_snapshot_st(version="1"),
    new=_snapshot_st(version="2"),
    extra_name=st.sampled_from(["_priv_a", "_priv_b", "_priv_c"]),
)
@settings(max_examples=75)
def test_adding_private_symbol_preserves_public_findings(
    old: AbiSnapshot, new: AbiSnapshot, extra_name: str
):
    """Adding an ELF-only symbol to both sides leaves scoped findings unchanged."""
    before = compare(copy.deepcopy(old), copy.deepcopy(new), scope_to_public_surface=True)

    old2 = copy.deepcopy(old)
    new2 = copy.deepcopy(new)
    priv = Function(
        name=extra_name,
        mangled=f"_Zpriv{extra_name}",
        return_type="void",
        params=[],
        visibility=Visibility.ELF_ONLY,
    )
    old2.functions.append(priv)
    new2.functions.append(copy.deepcopy(priv))

    after = compare(old2, new2, scope_to_public_surface=True)

    # The public (reported) findings are identical — the private symbol is
    # invisible to the public surface on both sides, so it produces nothing.
    assert _change_keys(before.changes) == _change_keys(after.changes)


# ---------------------------------------------------------------------------
# Subset — scoping never invents a finding
# ---------------------------------------------------------------------------


@given(old=_snapshot_st(version="1"), new=_snapshot_st(version="2"))
@settings(max_examples=100)
def test_scoped_findings_subset_of_unscoped_universe(old: AbiSnapshot, new: AbiSnapshot):
    """Every scoped finding also appears somewhere in the unscoped run.

    ADR-024 §D4: header scoping only removes/demotes findings; it must never
    surface a (kind, symbol) the unscoped comparison did not produce.
    """
    unscoped = compare(copy.deepcopy(old), copy.deepcopy(new), scope_to_public_surface=False)
    scoped = compare(copy.deepcopy(old), copy.deepcopy(new), scope_to_public_surface=True)

    universe = _all_finding_keys(unscoped)
    assert _all_finding_keys(scoped) <= universe
    # And the reported (kept) scoped findings are themselves within that universe.
    assert _change_keys(scoped.changes) <= universe


# ---------------------------------------------------------------------------
# Anti-hiding — a break on a public-header type always survives scoping
# (ADR-024 §"Validation & testing strategy" §4, "the most important")
# ---------------------------------------------------------------------------


@given(
    type_name=st.sampled_from(_TYPE_POOL),
    old_size=st.sampled_from([32, 64]),
    new_size=st.sampled_from([128, 256]),
)
@settings(max_examples=40)
def test_public_type_break_is_never_hidden_by_scoping(
    type_name: str, old_size: int, new_size: int
):
    """A layout change to a type reachable from a PUBLIC function must remain a
    reported finding under scoping — it is observable to consumers."""
    def _pair(size: int, version: str) -> AbiSnapshot:
        return AbiSnapshot(
            library="lib",
            version=version,
            functions=[
                Function(
                    name="pub_api",
                    mangled="_Z7pub_apiv",
                    return_type=f"{type_name} *",
                    params=[],
                    visibility=Visibility.PUBLIC,
                )
            ],
            types=[RecordType(name=type_name, kind="struct", size_bits=size)],
        )

    old, new = _pair(old_size, "1"), _pair(new_size, "2")
    scoped = compare(old, new, scope_to_public_surface=True)

    reported = _change_keys(scoped.changes)
    demoted = _change_keys(scoped.out_of_surface_changes)
    # The public type's change is reported and was NOT demoted to the ledger.
    assert any(sym == type_name for _, sym in reported)
    assert not any(sym == type_name for _, sym in demoted)


# ---------------------------------------------------------------------------
# Widening overlay (ADR-024 §D6 / Phase 4) — re-promotion never hides or invents
# ---------------------------------------------------------------------------


@given(old=_snapshot_st(version="1"), new=_snapshot_st(version="2"))
@settings(max_examples=75)
def test_widening_only_repromotes_never_hides_or_invents(
    old: AbiSnapshot, new: AbiSnapshot
):
    """Force-including every demoted symbol moves findings from the ledger back
    into the report and changes nothing else: kept grows, the ledger shrinks,
    and the overall universe is identical (widening can only ever *keep*)."""
    scoped = compare(copy.deepcopy(old), copy.deepcopy(new), scope_to_public_surface=True)
    forced = {c.symbol for c in scoped.out_of_surface_changes if c.symbol}

    widened = compare(
        copy.deepcopy(old), copy.deepcopy(new),
        scope_to_public_surface=True, force_public_symbols=forced,
    )

    # Re-promotion keeps every scoped change *reported* — as a normal or a
    # redundant change. Widening can relabel a change between the two buckets
    # (re-promoting a root finding absorbs its now-redundant dependents, e.g. a
    # type removal absorbing a function whose return type was that type), but it
    # never pushes a kept finding back into the ledger. The total universe is
    # asserted unchanged below; here we only forbid a kept→hidden transition.
    assert _change_keys(scoped.changes) <= (
        _change_keys(widened.changes) | _change_keys(widened.redundant_changes)
    )
    # … only removes from the ledger …
    assert _change_keys(widened.out_of_surface_changes) <= _change_keys(
        scoped.out_of_surface_changes
    )
    # … and never invents a finding the scoped run did not already have.
    assert _all_finding_keys(widened) == _all_finding_keys(scoped)
