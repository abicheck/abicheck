# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Unit tests for the namespace-shape pattern detectors.

These tests build synthetic ``AbiSnapshot`` objects — no C/C++ compiler,
libabigail, abi-compliance-checker, or castxml needed. They are part of
the default fast test suite.
"""

from __future__ import annotations

from hypothesis import given, strategies as st

from abicheck.checker_policy import ChangeKind
from abicheck.diff_namespaces import (
    DEFAULT_EXPERIMENTAL_NAMESPACES,
    _IndexItem,
    _strip_experimental,
    _unique_promotion_target,
    detect_experimental_namespace_changes,
)
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    Visibility,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _snap(
    funcs: list[Function] | None = None, types: list[RecordType] | None = None
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so",
        version="0",
        functions=list(funcs or []),
        types=list(types or []),
    )


def _fn(
    name: str, mangled: str | None = None, visibility: Visibility = Visibility.PUBLIC
) -> Function:
    return Function(
        name=name,
        mangled=mangled if mangled is not None else f"_Z{name}",
        return_type="void",
        visibility=visibility,
    )


def _rec(name: str) -> RecordType:
    return RecordType(name=name, kind="class")


def _rec_at(name: str, source_location: str = "communicator.h:10") -> RecordType:
    """A type with a declaring source location set. NOT identity evidence
    for the type-alias merge (``_type_index_items`` always uses ``None``
    for types -- see its docstring for why `source_location` was tried
    and falsified as type identity, same as an earlier structural
    fingerprint attempt); this helper exists only to build the specific
    location shapes those falsifying test cases need."""
    return RecordType(name=name, kind="class", source_location=source_location)


def _rec_public(name: str) -> RecordType:
    """A type explicitly scoped to the public-header set (ADR-024
    `-H`/`--header`), the one reliable public-reachability signal
    RecordType carries (Codex review)."""
    return RecordType(name=name, kind="class", origin=ScopeOrigin.PUBLIC_HEADER)


# ---------------------------------------------------------------------------
# _segments helper
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Promotion to a stable namespace whose path differs by more than the
# experimental segment (oneCCL: ccl::preview::X -> ccl::v1::X, v1 a plain,
# non-inline namespace, so mangled names differ).
# ---------------------------------------------------------------------------


def _fn_sig(name: str, mangled: str, params: list[str]) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        visibility=Visibility.PUBLIC,
        params=[Param(name=f"p{i}", type=t) for i, t in enumerate(params)],
    )


def _removed(changes: list) -> list:
    return [
        c
        for c in changes
        if c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
    ]


class TestPromotionToStableNamespace:
    SIG = ["const ccl::communicator&", "int"]

    def test_onecl_preview_to_v1_is_not_removal(self) -> None:
        old = _snap(
            funcs=[_fn_sig("ccl::preview::split_communicators", "_ZA", self.SIG)]
        )
        new = _snap(funcs=[_fn_sig("ccl::v1::split_communicators", "_ZB", self.SIG)])
        assert _removed(detect_experimental_namespace_changes(old, new)) == []

    def test_signature_mismatch_still_reports(self) -> None:
        old = _snap(
            funcs=[_fn_sig("ccl::preview::split_communicators", "_ZA", self.SIG)]
        )
        new = _snap(funcs=[_fn_sig("ccl::v1::split_communicators", "_ZB", ["int"])])
        assert len(_removed(detect_experimental_namespace_changes(old, new))) == 1

    def test_different_root_still_reports(self) -> None:
        old = _snap(
            funcs=[_fn_sig("ccl::preview::split_communicators", "_ZA", self.SIG)]
        )
        new = _snap(funcs=[_fn_sig("other::v1::split_communicators", "_ZB", self.SIG)])
        assert len(_removed(detect_experimental_namespace_changes(old, new))) == 1

    def test_ambiguous_targets_still_report(self) -> None:
        old = _snap(
            funcs=[_fn_sig("ccl::preview::split_communicators", "_ZA", self.SIG)]
        )
        new = _snap(
            funcs=[
                _fn_sig("ccl::v1::split_communicators", "_ZB", self.SIG),
                _fn_sig("ccl::v2::split_communicators", "_ZC", self.SIG),
            ]
        )
        assert len(_removed(detect_experimental_namespace_changes(old, new))) == 1

    def test_preexisting_stable_same_leaf_is_not_a_promotion(self) -> None:
        old = _snap(
            funcs=[
                _fn_sig("ccl::preview::split_communicators", "_ZA", self.SIG),
                _fn_sig("ccl::detail::split_communicators", "_ZD", self.SIG),
            ]
        )
        new = _snap(
            funcs=[_fn_sig("ccl::detail::split_communicators", "_ZD", self.SIG)]
        )
        assert len(_removed(detect_experimental_namespace_changes(old, new))) == 1

    def test_experimental_target_is_not_a_promotion(self) -> None:
        old = _snap(
            funcs=[_fn_sig("ccl::preview::split_communicators", "_ZA", self.SIG)]
        )
        new = _snap(
            funcs=[
                _fn_sig("ccl::experimental::v1::split_communicators", "_ZB", self.SIG)
            ]
        )
        assert len(_removed(detect_experimental_namespace_changes(old, new))) == 1

    def test_two_removals_cannot_share_one_target(self) -> None:
        old = _snap(
            funcs=[
                _fn_sig("ccl::preview::a::f", "_ZA", ["int"]),
                _fn_sig("ccl::experimental::b::f", "_ZB", ["int"]),
            ]
        )
        new = _snap(funcs=[_fn_sig("ccl::v1::f", "_ZC", ["int"])])
        # Only one could have been promoted; neither is proven, both reported.
        assert len(_removed(detect_experimental_namespace_changes(old, new))) == 2
        # Reordering the OLD declarations does not change the outcome.
        old_rev = _snap(funcs=list(reversed(old.functions)))
        assert len(_removed(detect_experimental_namespace_changes(old_rev, new))) == 2

    def test_surviving_sibling_does_not_block_promotion(self) -> None:
        old = _snap(
            funcs=[
                _fn_sig("ccl::preview::a::f", "_ZA", ["int"]),
                _fn_sig("ccl::experimental::b::f", "_ZB", ["int"]),
            ]
        )
        new = _snap(
            funcs=[
                _fn_sig("ccl::experimental::b::f", "_ZB", ["int"]),
                _fn_sig("ccl::v1::f", "_ZC", ["int"]),
            ]
        )
        assert _removed(detect_experimental_namespace_changes(old, new)) == []

    def test_types_never_promoted_without_signature_evidence(self) -> None:
        old = _snap(types=[_rec("ccl::preview::comm_split_attr")])
        new = _snap(types=[_rec("ccl::v1::comm_split_attr")])
        assert len(_removed(detect_experimental_namespace_changes(old, new))) == 1


_ROOTS = st.sampled_from(["ccl", "dal", "sycl"])
_MIDS = st.sampled_from(["v1", "v2", "detail", "api", "preview", "experimental"])
_LEAVES = st.sampled_from(["split", "merge"])
_SIGS = st.sampled_from([(), ("int",), ("int", "float")])


@st.composite
def _decl_items(draw):
    root = draw(_ROOTS)
    mids = draw(st.lists(_MIDS, max_size=2))
    leaf = draw(_LEAVES)
    sig = draw(st.one_of(st.none(), _SIGS))
    qname = "::".join([root, *mids, leaf])
    stripped, _ = _strip_experimental(qname, DEFAULT_EXPERIMENTAL_NAMESPACES)
    return _IndexItem(qname, stripped, leaf, None, sig)


class TestUniquePromotionTargetProperties:
    """Contract of the promotion primitive, stated as invariants against an
    oracle that re-derives each gate independently of the implementation."""

    NS = DEFAULT_EXPERIMENTAL_NAMESPACES

    @given(
        removed=_decl_items(),
        old=st.lists(_decl_items(), max_size=5),
        new=st.lists(_decl_items(), max_size=5),
    )
    def test_target_satisfies_every_gate(self, removed, old, new) -> None:
        target = _unique_promotion_target(removed, old, new, self.NS)
        if target is None:
            return
        tsegs = target.split("::")
        rsegs = removed.qname.split("::")
        assert removed.signature is not None
        assert tsegs[-1] == removed.leaf
        assert tsegs[0] == rsegs[0]
        assert not any(s in self.NS for s in tsegs)
        matching_new = [
            i for i in new if i.qname == target and i.signature == removed.signature
        ]
        assert matching_new, "target must be a real NEW decl with the same signature"
        assert not any(
            i.qname == target and i.signature == removed.signature for i in old
        ), "a pre-existing declaration is never a promotion target"

    @given(
        removed=_decl_items(),
        old=st.lists(_decl_items(), max_size=5),
        new=st.lists(_decl_items(), max_size=5),
    )
    def test_uniqueness_oracle(self, removed, old, new) -> None:
        # Independent brute-force oracle over the same gates.
        def ok(i: _IndexItem) -> bool:
            segs = i.qname.split("::")
            return (
                removed.signature is not None
                and i.signature == removed.signature
                and segs[-1] == removed.leaf
                and segs[0] == removed.qname.split("::")[0]
                and segs[0] not in self.NS
                and not any(s in self.NS for s in segs)
                and not any(
                    o.qname == i.qname and o.signature == i.signature for o in old
                )
            )

        def claims(o: _IndexItem) -> bool:
            segs = o.qname.split("::")
            return (
                o.signature == removed.signature
                and segs[-1] == removed.leaf
                and segs[0] == removed.qname.split("::")[0]
                and any(s in self.NS for s in segs)
                and not any(
                    n.qname == o.qname and n.signature == o.signature for n in new
                )
            )

        expected = {i.qname for i in new if ok(i)}
        # Reverse uniqueness: a target is reserved for exactly one removal.
        claimants = {removed.qname} | {o.qname for o in old if claims(o)}
        got = _unique_promotion_target(removed, old, new, self.NS)
        unique = len(expected) == 1 and len(claimants) == 1
        assert got == (next(iter(expected)) if unique else None)

    @given(
        removed=_decl_items(),
        old=st.lists(_decl_items(), max_size=5),
        new=st.lists(_decl_items(), max_size=5),
        seed=st.randoms(use_true_random=False),
    )
    def test_order_independent(self, removed, old, new, seed) -> None:
        a = _unique_promotion_target(removed, old, new, self.NS)
        old2, new2 = list(old), list(new)
        seed.shuffle(old2)
        seed.shuffle(new2)
        assert _unique_promotion_target(removed, old2, new2, self.NS) == a

    @given(removed=_decl_items(), new=st.lists(_decl_items(), max_size=5))
    def test_no_signature_evidence_never_promotes(self, removed, new) -> None:
        bare = removed._replace(signature=None)
        assert _unique_promotion_target(bare, [], new, self.NS) is None
