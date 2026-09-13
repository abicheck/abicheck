"""Parallel safety tests — concurrent compare() calls must not interfere.

Validates that:
1. Multiple concurrent comparisons on different snapshots produce correct results
2. Multiple concurrent comparisons on shared snapshots don't corrupt state
3. No global state mutation between calls
"""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor, as_completed

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.model import (
    AbiSnapshot,
    Function,
    RecordType,
    Variable,
    Visibility,
)


def _snap(
    version="1.0", functions=None, variables=None, types=None, enums=None, typedefs=None
):
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=functions or [],
        variables=variables or [],
        types=types or [],
        enums=enums or [],
        typedefs=typedefs or {},
    )


def _pub_func(name, mangled, ret="void", **kwargs):
    return Function(
        name=name,
        mangled=mangled,
        return_type=ret,
        visibility=Visibility.PUBLIC,
        **kwargs,
    )


def _pub_var(name, mangled, type_):
    return Variable(
        name=name, mangled=mangled, type=type_, visibility=Visibility.PUBLIC
    )


# ═══════════════════════════════════════════════════════════════════════════
# Concurrent Independent Comparisons
# ═══════════════════════════════════════════════════════════════════════════


class TestConcurrentIndependent:
    """Multiple independent compare() calls in parallel."""

    def test_10_parallel_no_change_comparisons(self):
        """10 concurrent NO_CHANGE comparisons all return correct verdicts."""
        snap = _snap(functions=[_pub_func(f"f{i}", f"_Z2f{i}v") for i in range(10)])

        def run_compare(idx):
            old = copy.deepcopy(snap)
            new = copy.deepcopy(snap)
            r = compare(old, new)
            return idx, r.verdict, len(r.changes)

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(run_compare, i) for i in range(10)]
            results = [f.result() for f in as_completed(futures)]

        for idx, verdict, n_changes in results:
            assert verdict == Verdict.NO_CHANGE, f"Task {idx} got {verdict}"
            assert n_changes == 0, f"Task {idx} got {n_changes} changes"

    def test_10_parallel_breaking_comparisons(self):
        """10 concurrent BREAKING comparisons all detect correctly."""

        def run_compare(idx):
            f = _pub_func(f"func{idx}", f"_Z4func{idx}v")
            old = _snap(functions=[f])
            new = _snap(functions=[])
            r = compare(old, new)
            return (
                idx,
                r.verdict,
                any(c.kind == ChangeKind.FUNC_REMOVED for c in r.changes),
            )

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(run_compare, i) for i in range(10)]
            results = [f.result() for f in as_completed(futures)]

        for idx, verdict, found_removal in results:
            assert verdict == Verdict.BREAKING, f"Task {idx} got {verdict}"
            assert found_removal, f"Task {idx} missing FUNC_REMOVED"


# ═══════════════════════════════════════════════════════════════════════════
# Concurrent with Shared Snapshots
# ═══════════════════════════════════════════════════════════════════════════


class TestConcurrentSharedSnapshots:
    """Multiple threads reading the same snapshots concurrently."""

    def test_shared_snapshot_concurrent_reads(self):
        """Multiple threads comparing the same snapshot objects don't interfere.

        No deepcopy — threads genuinely share the same snapshot data to
        verify compare() does not mutate its inputs.
        """
        f = _pub_func("api", "_Z3apiv", ret="int")
        old = _snap(functions=[f])
        new = _snap(functions=[_pub_func("api", "_Z3apiv", ret="long")])

        def run_compare(_idx):
            r = compare(old, new)
            return r.verdict, any(
                c.kind == ChangeKind.FUNC_RETURN_CHANGED for c in r.changes
            )

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(run_compare, i) for i in range(5)]
            results = [f.result() for f in as_completed(futures)]

        for verdict, found in results:
            assert verdict == Verdict.BREAKING
            assert found

        # Verify inputs were not mutated
        assert len(old.functions) == 1
        assert len(new.functions) == 1


# ═══════════════════════════════════════════════════════════════════════════
# Mixed Concurrent: Different Kinds of Changes
# ═══════════════════════════════════════════════════════════════════════════


class TestConcurrentMixed:
    """Different types of changes processed concurrently."""

    def test_mixed_changes_concurrent(self):
        """Each thread tests a different ChangeKind, all results correct."""
        test_cases = [
            # (description, old_snap, new_snap, expected_verdict, expected_kind)
            (
                "func_removed",
                _snap(functions=[_pub_func("a", "_Z1av")]),
                _snap(),
                Verdict.BREAKING,
                ChangeKind.FUNC_REMOVED,
            ),
            (
                "func_added",
                _snap(),
                _snap(functions=[_pub_func("b", "_Z1bv")]),
                Verdict.COMPATIBLE,
                ChangeKind.FUNC_ADDED,
            ),
            (
                "return_changed",
                _snap(functions=[_pub_func("c", "_Z1cv", ret="int")]),
                _snap(functions=[_pub_func("c", "_Z1cv", ret="long")]),
                Verdict.BREAKING,
                ChangeKind.FUNC_RETURN_CHANGED,
            ),
            (
                "type_size_changed",
                _snap(types=[RecordType(name="T", kind="struct", size_bits=32)]),
                _snap(types=[RecordType(name="T", kind="struct", size_bits=64)]),
                Verdict.BREAKING,
                ChangeKind.TYPE_SIZE_CHANGED,
            ),
            (
                "no_change",
                _snap(functions=[_pub_func("d", "_Z1dv")]),
                _snap(functions=[_pub_func("d", "_Z1dv")]),
                Verdict.NO_CHANGE,
                None,
            ),
        ]

        def run_case(case):
            desc, old, new, exp_verdict, exp_kind = case
            r = compare(copy.deepcopy(old), copy.deepcopy(new))
            return desc, r.verdict, {c.kind for c in r.changes}, exp_verdict, exp_kind

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(run_case, tc) for tc in test_cases]
            results = [f.result() for f in as_completed(futures)]

        for desc, verdict, kinds, exp_verdict, exp_kind in results:
            assert verdict == exp_verdict, (
                f"{desc}: expected {exp_verdict}, got {verdict}"
            )
            if exp_kind is not None:
                assert exp_kind in kinds, f"{desc}: expected {exp_kind} in {kinds}"


# ═══════════════════════════════════════════════════════════════════════════
# No Global State Leakage
# ═══════════════════════════════════════════════════════════════════════════


class TestNoGlobalStateLeak:
    """Verify no global state mutation between compare() calls."""

    def test_sequential_calls_independent(self):
        """Sequential compare() calls should not affect each other."""
        # First comparison: BREAKING
        f1 = _pub_func("removed", "_Z7removedv")
        r1 = compare(_snap(functions=[f1]), _snap())
        assert r1.verdict == Verdict.BREAKING

        # Second comparison: NO_CHANGE — should not be affected by first
        f2 = _pub_func("kept", "_Z4keptv")
        r2 = compare(_snap(functions=[f2]), _snap(functions=[f2]))
        assert r2.verdict == Verdict.NO_CHANGE
        assert len(r2.changes) == 0

        # Third comparison: COMPATIBLE
        r3 = compare(_snap(), _snap(functions=[f2]))
        assert r3.verdict == Verdict.COMPATIBLE

    def test_index_caching_independent(self):
        """Snapshot index() builds don't leak between comparisons."""
        f = _pub_func("x", "_Z1xv")
        snap1 = _snap(functions=[f])
        snap2 = _snap(functions=[f])

        # Force index building via comparison
        r1 = compare(snap1, snap2)
        assert r1.verdict == Verdict.NO_CHANGE

        # Now compare with a different snapshot — index should rebuild
        snap3 = _snap(functions=[_pub_func("y", "_Z1yv")])
        r2 = compare(snap1, snap3)
        # f("x") removed from new → BREAKING; f("y") added → COMPATIBLE
        assert r2.verdict == Verdict.BREAKING
