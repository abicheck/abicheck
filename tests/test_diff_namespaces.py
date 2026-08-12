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

import pytest
from hypothesis import given, strategies as st

from abicheck.checker_policy import ChangeKind
from abicheck.diff_namespaces import (
    DEFAULT_EXPERIMENTAL_NAMESPACES,
    _IndexItem,
    _looks_like_std_reexport,
    _origin_by_name,
    _paired_stable_indices,
    _segments,
    _stable_keys_compatible,
    _strip_experimental,
    _version_strip_segments,
    _version_suffix,
    detect_experimental_namespace_changes,
    detect_inline_namespace_version_bump,
    detect_namespace_patterns,
)
from abicheck.model import (
    AbiSnapshot,
    Function,
    RecordType,
    ScopeOrigin,
    Visibility,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _snap(funcs: list[Function] | None = None,
          types: list[RecordType] | None = None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so",
        version="0",
        functions=list(funcs or []),
        types=list(types or []),
    )


def _fn(name: str, mangled: str | None = None,
        visibility: Visibility = Visibility.PUBLIC) -> Function:
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
    `--public-header`), the one reliable public-reachability signal
    RecordType carries (Codex review)."""
    return RecordType(name=name, kind="class", origin=ScopeOrigin.PUBLIC_HEADER)


# ---------------------------------------------------------------------------
# _segments helper
# ---------------------------------------------------------------------------


class TestSegments:
    def test_splits_simple(self) -> None:
        assert _segments("a::b::c") == ["a", "b", "c"]

    def test_handles_empty(self) -> None:
        assert _segments("") == []

    def test_strips_template_args(self) -> None:
        assert _segments("ns::experimental::sort<int>") == [
            "ns", "experimental", "sort",
        ]

    def test_nested_templates(self) -> None:
        # Outer template args are stripped wholesale; ``::`` inside
        # them does not produce spurious segments.
        assert _segments("ns::foo<bar::baz<int>>") == ["ns", "foo"]

    def test_leading_double_colon(self) -> None:
        # A leading ``::`` produces an empty segment that is dropped.
        assert _segments("::ns::sort") == ["ns", "sort"]

    def test_unqualified(self) -> None:
        assert _segments("plain") == ["plain"]


# ---------------------------------------------------------------------------
# _strip_experimental
# ---------------------------------------------------------------------------


class TestStripExperimental:
    def test_strips_experimental_segment(self) -> None:
        stripped, matched = _strip_experimental("ns::experimental::sort")
        assert stripped == "ns::sort"
        assert matched == "experimental"

    def test_no_match_returns_input_unchanged(self) -> None:
        stripped, matched = _strip_experimental("ns::stable::sort")
        assert stripped == "ns::stable::sort"
        assert matched is None

    def test_strips_preview(self) -> None:
        stripped, matched = _strip_experimental("ns::preview::sort")
        assert stripped == "ns::sort"
        assert matched == "preview"

    def test_custom_namespaces(self) -> None:
        stripped, matched = _strip_experimental(
            "ns::wip::sort", experimental_namespaces=("wip",),
        )
        assert stripped == "ns::sort"
        assert matched == "wip"

    def test_substring_inside_identifier_not_matched(self) -> None:
        stripped, matched = _strip_experimental("ns::ExperimentalView::sort")
        assert matched is None
        assert stripped == "ns::ExperimentalView::sort"

    def test_only_first_match_is_stripped(self) -> None:
        # Nested ``experimental::experimental::`` peels one layer per call.
        stripped, matched = _strip_experimental("a::experimental::experimental::x")
        assert matched == "experimental"
        assert stripped == "a::experimental::x"


# ---------------------------------------------------------------------------
# _origin_by_name
# ---------------------------------------------------------------------------


class TestOriginByName:
    def test_simple_lookup(self) -> None:
        types = [_rec_public("ns::Widget"), _rec("ns::Internal")]
        origins = _origin_by_name(types)
        assert origins["ns::Widget"] == ScopeOrigin.PUBLIC_HEADER
        assert origins["ns::Internal"] == ScopeOrigin.UNKNOWN

    def test_duplicate_name_first_occurrence_wins(self) -> None:
        """Self-review finding: a plain dict comprehension keyed by name
        would silently let a later duplicate overwrite an earlier one.
        Mirrors pattern_verdicts._exact_record's "first match wins" exact-
        identity lookup instead."""
        types = [_rec_public("ns::Widget"), _rec("ns::Widget")]
        origins = _origin_by_name(types)
        assert origins["ns::Widget"] == ScopeOrigin.PUBLIC_HEADER


# ---------------------------------------------------------------------------
# _stable_keys_compatible
# ---------------------------------------------------------------------------


class TestStableKeysCompatible:
    def test_bare_leaf_is_compatible_with_any_qualified_form(self) -> None:
        assert _stable_keys_compatible("check_ranges", "oneapi::dal::detail::check_ranges")
        assert _stable_keys_compatible("ns::check_ranges", "check_ranges")

    def test_identical_keys_are_compatible(self) -> None:
        assert _stable_keys_compatible("ns::sort", "ns::sort")

    def test_diverging_multi_segment_keys_are_not_compatible(self) -> None:
        assert not _stable_keys_compatible("api::sort", "detail::sort")

    def test_empty_key_is_never_compatible(self) -> None:
        assert not _stable_keys_compatible("", "ns::sort")
        assert not _stable_keys_compatible("ns::sort", "")
        assert not _stable_keys_compatible("", "")


# ---------------------------------------------------------------------------
# detect_experimental_namespace_changes — graduations
# ---------------------------------------------------------------------------


class TestExperimentalGraduated:
    def test_function_graduated_with_alias_kept(self) -> None:
        old = _snap(funcs=[_fn("ns::experimental::sort")])
        new = _snap(funcs=[
            _fn("ns::experimental::sort"),
            _fn("ns::sort"),
        ])
        changes = detect_experimental_namespace_changes(old, new)
        assert len(changes) == 1
        c = changes[0]
        assert c.kind == ChangeKind.EXPERIMENTAL_GRADUATED
        assert c.symbol == "ns::sort"
        assert "graduated" in c.description.lower()
        assert c.public_reachable is True

    def test_type_graduated_with_alias_kept(self) -> None:
        old = _snap(types=[_rec("ns::experimental::queue")])
        new = _snap(types=[
            _rec("ns::experimental::queue"),
            _rec("ns::queue"),
        ])
        changes = detect_experimental_namespace_changes(old, new)
        assert len(changes) == 1
        c = changes[0]
        assert c.kind == ChangeKind.EXPERIMENTAL_GRADUATED
        assert c.symbol == "ns::queue"
        assert c.public_reachable is False

    def test_public_header_type_graduated_is_reachable(self) -> None:
        """Codex review: RecordType.origin == ScopeOrigin.PUBLIC_HEADER (set
        only under ADR-024's opt-in --public-header scoping) is a reliable
        public signal for the type-sourced path, unlike the default
        ScopeOrigin.UNKNOWN case above."""
        old = _snap(types=[_rec_public("ns::experimental::queue")])
        new = _snap(types=[
            _rec_public("ns::experimental::queue"),
            _rec_public("ns::queue"),
        ])
        changes = detect_experimental_namespace_changes(old, new)
        assert len(changes) == 1
        c = changes[0]
        assert c.kind == ChangeKind.EXPERIMENTAL_GRADUATED
        assert c.public_reachable is True
        assert c.reachability_kind == "direct_public_symbol"

    def test_no_graduation_when_stable_existed_before(self) -> None:
        # Stable name already existed in old → not a graduation event
        # (just deletion of a redundant alias, which is a separate signal).
        old = _snap(funcs=[
            _fn("ns::experimental::sort"),
            _fn("ns::sort"),
        ])
        new = _snap(funcs=[_fn("ns::sort")])
        changes = detect_experimental_namespace_changes(old, new)
        assert not any(
            c.kind == ChangeKind.EXPERIMENTAL_GRADUATED for c in changes
        )

    def test_no_graduation_when_experimental_alias_dropped(self) -> None:
        # Promotion that ALSO drops the experimental alias is not
        # "graduation"; it's a removal masked by an addition.
        old = _snap(funcs=[_fn("ns::experimental::sort")])
        new = _snap(funcs=[_fn("ns::sort")])
        changes = detect_experimental_namespace_changes(old, new)
        # We expect EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT to NOT fire
        # because a stable twin exists; we also don't fire GRADUATED
        # because the experimental alias is gone.
        assert not any(
            c.kind == ChangeKind.EXPERIMENTAL_GRADUATED for c in changes
        )
        assert not any(
            c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
            for c in changes
        )

    def test_only_public_functions_considered(self) -> None:
        # HIDDEN-visibility functions never reach public ABI — graduating
        # or losing one must not be reported as a namespace event.
        hidden = _fn(
            "ns::experimental::__helper",
            visibility=Visibility.HIDDEN,
        )
        old = _snap(funcs=[hidden])
        new = _snap(funcs=[])
        changes = detect_experimental_namespace_changes(old, new)
        assert changes == []


# ---------------------------------------------------------------------------
# detect_experimental_namespace_changes — silent removals
# ---------------------------------------------------------------------------


class TestExperimentalRemovedWithoutReplacement:
    def test_declared_experimental_alias_removal_survives_demangled_divergence(
        self,
    ) -> None:
        # Codex review, on the mangled-preference fix above: a using-
        # declaration/re-export can legitimately declare a name in
        # `experimental::` whose *mangled symbol* demangles to the
        # underlying stable declaration it aliases (no "experimental"
        # segment at all) -- the same declared-vs-underlying divergence
        # `detect_std_reexport_removed` is built on. Namespace
        # classification must come from the *declared* name only, or this
        # would silently defeat EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
        # for every alias-shaped experimental declaration.
        import abicheck.demangle as dm

        def fake_demangle(mangled_list: list[str]) -> dict[str, str]:
            # The alias's own mangled symbol demangles to the *stable*
            # underlying name -- no "experimental" segment survives a
            # demangle-preferred reading.
            sigs = {"_ZN2ns4sortE": "ns::sort"}
            return {m: sigs[m] for m in mangled_list if m in sigs}

        orig = dm.demangle_batch
        dm.demangle_batch = fake_demangle  # type: ignore[assignment]
        try:
            old = _snap(funcs=[
                _fn("ns::experimental::sort", mangled="_ZN2ns4sortE"),
            ])
            # Genuine removal: the alias is dropped and its mangled symbol
            # is gone from `new` entirely (no stable twin either).
            new = _snap(funcs=[])
            changes = detect_experimental_namespace_changes(old, new)
        finally:
            dm.demangle_batch = orig  # type: ignore[assignment]

        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
        assert changes[0].symbol == "ns::experimental::sort"

    def test_alias_removal_still_fires_when_underlying_symbol_survives_elsewhere(
        self,
    ) -> None:
        # Codex review, on the mangled-still-linked suppression above: the
        # suppression must not treat "this mangled symbol exists *somewhere*
        # in new" as proof the removed declaration survives -- an
        # experimental alias's underlying symbol can keep exporting under a
        # completely unrelated declared name (a different leaf) after the
        # alias itself is deleted, and source that named the alias no
        # longer compiles even though the symbol lives on elsewhere. The
        # suppression must be scoped to the *same leaf*, not any leaf.
        old = _snap(funcs=[
            _fn("ns::experimental::sort", mangled="_ZSAME"),
        ])
        # The mangled symbol survives, but only under an entirely different
        # leaf ("relabelled") -- not a re-qualified spelling of "sort".
        new = _snap(funcs=[
            _fn("ns::detail::relabelled", mangled="_ZSAME"),
        ])
        changes = detect_experimental_namespace_changes(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
        assert changes[0].symbol == "ns::experimental::sort"

    def test_alias_removal_still_fires_when_same_leaf_target_is_unrelated(
        self,
    ) -> None:
        # Codex review, on the (mangled, leaf)-scoped suppression above: a
        # matching *leaf* alone is still not enough -- two genuinely
        # different declarations can coincidentally share both a leaf and
        # (through unrelated aliasing) a mangled symbol. Here
        # `api::experimental::sort` is removed while an unrelated
        # `detail::sort` (different full path, same leaf, same mangled
        # symbol by construction) survives; the alias itself no longer
        # compiles for any caller that named it, so this must still fire.
        old = _snap(funcs=[
            _fn("api::experimental::sort", mangled="_ZSAME2"),
        ])
        new = _snap(funcs=[
            _fn("detail::sort", mangled="_ZSAME2"),
        ])
        changes = detect_experimental_namespace_changes(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
        assert changes[0].symbol == "api::experimental::sort"

    def test_bare_name_demangled_signature_does_not_defeat_suppression(
        self,
    ) -> None:
        # Codex review, on the stable-key compatibility check above: a
        # bare-named OLD declaration demangles to a *full signature*
        # ("ns::experimental::check_ranges()"), while an already-qualified
        # NEW declared name never carries one ("experimental::check_ranges"
        # -- header-derived names are never signatures). Comparing those
        # two stable-keys without stripping the trailing "()" first would
        # mismatch on that alone and wrongly re-fire the original reported
        # false positive through a different door.
        import abicheck.demangle as dm

        def fake_demangle(mangled_list: list[str]) -> dict[str, str]:
            sigs = {"_Zex1": "ns::experimental::check_ranges()"}
            return {m: sigs[m] for m in mangled_list if m in sigs}

        orig = dm.demangle_batch
        dm.demangle_batch = fake_demangle  # type: ignore[assignment]
        try:
            old = _snap(funcs=[_fn("check_ranges", mangled="_Zex1")])
            new = _snap(funcs=[
                _fn("experimental::check_ranges", mangled="_Zex1"),
            ])
            changes = detect_experimental_namespace_changes(old, new)
        finally:
            dm.demangle_batch = orig  # type: ignore[assignment]

        assert not any(
            c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
            for c in changes
        )

    def test_all_collapsed_overloads_must_survive_to_suppress(self) -> None:
        # Codex review: when neither side's declared name carries a
        # signature, two overloads sharing one leaf collapse into a
        # *single* (stable_key, leaf) bucket -- `old_exp` can hold more
        # than one entry. A single surviving sibling overload's mangled
        # symbol must not suppress the whole bucket's removal when a
        # different, genuinely-removed sibling shares it.
        import abicheck.demangle as dm

        def fake_demangle(mangled_list: list[str]) -> dict[str, str]:
            sigs = {"_ZM2": "ns::experimental::foo()"}
            return {m: sigs[m] for m in mangled_list if m in sigs}

        orig = dm.demangle_batch
        dm.demangle_batch = fake_demangle  # type: ignore[assignment]
        try:
            old = _snap(funcs=[
                _fn("ns::experimental::foo", mangled="_ZM1"),
                _fn("ns::experimental::foo", mangled="_ZM2"),
            ])
            # M1 is genuinely removed. M2 survives, but re-qualified as a
            # bare name that demangles to a full signature -- landing in a
            # *different* bucket key than OLD's own (no "()" there), which
            # is what makes `still_linked` the only remaining signal.
            new = _snap(funcs=[_fn("foo", mangled="_ZM2")])
            changes = detect_experimental_namespace_changes(old, new)
        finally:
            dm.demangle_batch = orig  # type: ignore[assignment]

        assert any(
            c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
            for c in changes
        )

    def test_silent_function_removal(self) -> None:
        old = _snap(funcs=[_fn("ns::experimental::bar")])
        new = _snap(funcs=[])
        changes = detect_experimental_namespace_changes(old, new)
        assert len(changes) == 1
        c = changes[0]
        assert c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
        assert c.symbol == "ns::experimental::bar"
        # ADR-044 D1 (Codex review): _func_index_items only ever
        # indexes Visibility.PUBLIC functions, so this finding's mere
        # existence already proves its subject is public — tagged directly
        # so a broad namespace: "ns::experimental::*" suppression rule
        # doesn't silently hide it (this detector runs after
        # ApplySuppression, so nothing else would ever tag it).
        assert c.public_reachable is True
        assert c.reachability_kind == "direct_public_symbol"

    def test_silent_type_removal(self) -> None:
        old = _snap(types=[_rec("ns::experimental::queue")])
        new = _snap(types=[])
        changes = detect_experimental_namespace_changes(old, new)
        assert len(changes) == 1
        c = changes[0]
        assert c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
        assert c.symbol == "ns::experimental::queue"
        # RecordType has no Visibility field, and this type's origin defaults
        # to ScopeOrigin.UNKNOWN (no --public-header scoping used) — no
        # reliable signal in that (common) case.
        assert c.public_reachable is False
        assert c.reachability_kind is None

    def test_public_header_type_removal_is_reachable(self) -> None:
        """Codex review: unlike the default ScopeOrigin.UNKNOWN case above,
        ScopeOrigin.PUBLIC_HEADER (ADR-024 opt-in --public-header scoping)
        IS a reliable public signal, so this must be tagged reachable."""
        old = _snap(types=[_rec_public("ns::experimental::queue")])
        new = _snap(types=[])
        changes = detect_experimental_namespace_changes(old, new)
        assert len(changes) == 1
        c = changes[0]
        assert c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
        assert c.public_reachable is True
        assert c.reachability_kind == "direct_public_symbol"

    def test_replacement_at_stable_name_suppresses(self) -> None:
        old = _snap(funcs=[_fn("ns::experimental::bar")])
        new = _snap(funcs=[_fn("ns::bar")])
        changes = detect_experimental_namespace_changes(old, new)
        assert not any(
            c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
            for c in changes
        )

    def test_custom_experimental_namespaces(self) -> None:
        old = _snap(funcs=[_fn("ns::wip::bar")])
        new = _snap(funcs=[])
        changes = detect_experimental_namespace_changes(
            old, new, experimental_namespaces=("wip",),
        )
        assert any(
            c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
            for c in changes
        )

    def test_stable_only_change_does_not_fire(self) -> None:
        # Nothing in experimental:: → no finding.
        old = _snap(funcs=[_fn("ns::sort")])
        new = _snap(funcs=[])
        changes = detect_experimental_namespace_changes(old, new)
        assert changes == []

    def test_versioned_inline_namespace_spellings_report_once(self) -> None:
        # A versioned inline namespace makes the same declaration reachable
        # under two qualified spellings -- the full path and the
        # version-elided path unqualified lookup from the enclosing scope
        # also resolves to. Both spellings mangle to the SAME symbol (the
        # ABI mangles an inline namespace's segment either way) -- that
        # shared mangled name is the identity evidence the merge requires.
        # When a header-AST producer surfaces both as separate
        # declarations, removing the entity must read as ONE
        # EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT, not one per spelling.
        old = _snap(funcs=[
            _fn("preview::spmd::v1::communicator", mangled="_ZSame"),
            _fn("preview::spmd::communicator", mangled="_ZSame"),
        ])
        new = _snap(funcs=[])
        changes = detect_experimental_namespace_changes(old, new)
        removed = [
            c for c in changes
            if c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
        ]
        assert len(removed) == 1

    def test_unrelated_declaration_sharing_version_shaped_segment_not_merged(
        self,
    ) -> None:
        """Codex review, P1: a version-shaped segment (``v1``) is not proof
        of an *inline* namespace -- it's a legal name for an ordinary one
        too. Two functions with DIFFERENT mangled names (genuinely distinct
        declarations, not an inline-namespace alias pair) must each be
        judged on their own -- removing one while the other survives must
        still report the removal, not be silently absorbed just because
        they share a leaf name.
        """
        old = _snap(funcs=[
            _fn("preview::v1::bar", mangled="_ZOne"),
            _fn("preview::bar", mangled="_ZTwo"),
        ])
        new = _snap(funcs=[_fn("preview::bar", mangled="_ZTwo")])
        changes = detect_experimental_namespace_changes(old, new)
        removed = [
            c for c in changes
            if c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
        ]
        assert len(removed) == 1
        assert removed[0].symbol == "preview::v1::bar"

    def test_merged_key_stable_across_declaration_order(self) -> None:
        """Codex review, P1: the merged key for a genuine alias pair must
        not depend on which spelling appears first in a snapshot's own
        declaration order -- old and new can list the same two spellings
        in different orders (a harmless extraction-order difference) and
        must still resolve to the SAME index key, or one looks removed
        relative to the other's spelling.
        """
        old = _snap(funcs=[
            _fn("preview::spmd::v1::communicator", mangled="_ZSame"),
            _fn("preview::spmd::communicator", mangled="_ZSame"),
        ])
        # Reversed declaration order relative to `old`, otherwise identical.
        new = _snap(funcs=[
            _fn("preview::spmd::communicator", mangled="_ZSame"),
            _fn("preview::spmd::v1::communicator", mangled="_ZSame"),
        ])
        changes = detect_experimental_namespace_changes(old, new)
        assert not any(
            c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
            for c in changes
        )

    def test_bare_vs_qualified_name_same_mangled_symbol_no_false_positive(
        self,
    ) -> None:
        # Reported regression (shared with diff_templates.py's
        # INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API): a header/AST dumper
        # backend can populate ``Function.name`` with inconsistent
        # qualification for the *identical* linked symbol across two
        # snapshots -- a bare leaf on one side, a namespace-qualified
        # spelling with its own formatting on the other. Keying the
        # (stable_key, leaf) index on the declared name alone made a
        # still-exported symbol (confirmed unchanged via `nm` in the field)
        # read as removed-without-replacement purely because the dumper
        # changed how much of its own name it qualified between two
        # versions, not because the symbol moved out of `experimental::`.
        import abicheck.demangle as dm

        def fake_demangle(mangled_list: list[str]) -> dict[str, str]:
            sigs = {"_Zex1": "ns::experimental::check_ranges"}
            return {m: sigs[m] for m in mangled_list if m in sigs}

        orig = dm.demangle_batch
        dm.demangle_batch = fake_demangle  # type: ignore[assignment]
        try:
            old = _snap(funcs=[_fn("check_ranges", mangled="_Zex1")])
            # A real, documented quirk (AGENTS.md): a header AST backend can
            # drop the enclosing namespace from a declared name. The mangled
            # symbol -- what a consumer actually links against -- is
            # unchanged.
            new = _snap(funcs=[
                _fn("experimental::check_ranges", mangled="_Zex1"),
            ])
            changes = detect_experimental_namespace_changes(old, new)
        finally:
            dm.demangle_batch = orig  # type: ignore[assignment]

        assert not any(
            c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
            for c in changes
        )

    def test_version_stripping_ignores_parameter_type_segments(self) -> None:
        """Codex review, P2: a function's ``stripped`` deliberately keeps
        its full parameter-list signature (to keep overloads distinct), so
        naively scanning it for a version-shaped segment can strip one out
        of a *parameter's own type* (``ns::v2::T``) instead of only the
        declaration's own scope path -- corrupting the grouping key
        differently for each spelling of a genuine alias pair (one
        correctly loses its real scope-level ``v1``, the other incorrectly
        loses the parameter's unrelated ``v2``) so they land in different
        canon groups and never merge, even though they share a mangled
        name. Both spellings here carry the identical parameter type
        (``ns::v2::T``, itself version-shaped) and the same mangled name;
        removing the function must read as ONE finding.
        """
        old = _snap(funcs=[
            _fn("preview::v1::foo(ns::v2::T)", mangled="_ZSame"),
            _fn("preview::foo(ns::v2::T)", mangled="_ZSame"),
        ])
        new = _snap(funcs=[])
        changes = detect_experimental_namespace_changes(old, new)
        removed = [
            c for c in changes
            if c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
        ]
        assert len(removed) == 1

    def test_overloaded_raw_key_never_used_as_merge_evidence(self) -> None:
        """Codex review, P1, fresh evidence: a header-derived qualified name
        can omit a function's parameter-list signature
        (``_qualified_function_name`` returns ``name`` as-is whenever it
        already contains ``"::"``), so two genuinely distinct overloads can
        share one layer-1 raw key -- and that raw key's aggregated identity
        set then spans BOTH overloads' mangled names.

        Reviewer's exact scenario: OLD has overloads A and B both spelled
        ``preview::v1::foo`` (raw key identity ``{A, B}``) plus a
        version-elided alias spelling ``preview::foo`` that only ever named
        A (raw key identity ``{A}``). The two sets DO intersect on A, but
        that intersection isn't proof the alias raw key means the same
        thing as the *whole* overloaded raw key -- treating it as evidence
        would merge all three qnames into one component. NEW keeps only B
        (still spelled ``preview::v1::foo``); if the merge fired, the
        merged entity would read as "still present" (B's survival) and A's
        removal -- alias included -- would vanish entirely, unreported.

        A raw key with an ambiguous (>1 value) identity set must never
        qualify as layer-2 merge evidence, so A's alias spelling must still
        surface its own removal.
        """
        old = _snap(funcs=[
            _fn("preview::v1::foo", mangled="_ZFooA"),
            _fn("preview::v1::foo", mangled="_ZFooB"),
            _fn("preview::foo", mangled="_ZFooA"),
        ])
        new = _snap(funcs=[_fn("preview::v1::foo", mangled="_ZFooB")])
        changes = detect_experimental_namespace_changes(old, new)
        removed = [
            c for c in changes
            if c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
        ]
        assert len(removed) == 1
        assert removed[0].symbol == "preview::foo"

    def test_bare_fallback_mangled_name_never_used_as_merge_evidence(self) -> None:
        """Codex review, P1, fresh evidence: ``Function.mangled`` is not
        always a real ABI-mangled name -- both header-AST producers fall
        back to the bare declaration name when no real linkage name is
        available (``dwarf_snapshot.py``'s ``mangled = linkage_name or
        name``, ``dumper_clang.py``'s ``mangled = ... or name``). Two
        structurally unrelated declarations that both hit this fallback
        can then coincidentally carry the SAME bare-name "mangled" value
        even though their scopes genuinely differ -- reproduced here with
        the reviewer's exact repro shape: an old ``preview::v1::foo`` and
        an unrelated new ``preview::foo``, both falling back to the bare
        leaf ``"foo"`` as their `mangled` field (no ``_Z``/``?`` prefix --
        not a real mangled name). This must NOT read as a genuine alias:
        removing the versioned spelling must still be reported.
        """
        old = _snap(funcs=[
            _fn("preview::v1::foo", mangled="foo"),
            _fn("preview::foo", mangled="foo"),
        ])
        new = _snap(funcs=[_fn("preview::foo", mangled="foo")])
        changes = detect_experimental_namespace_changes(old, new)
        removed = [
            c for c in changes
            if c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
        ]
        assert len(removed) == 1
        assert removed[0].symbol == "preview::v1::foo"

    def test_versioned_inline_namespace_type_spellings_double_report_is_accepted(
        self,
    ) -> None:
        """Known, documented limitation (see `_type_index_items`'s
        docstring): unlike a function's mangled name, no `RecordType`
        field has survived adversarial review as reliable alias-identity
        evidence -- a structural fingerprint and `source_location` were
        both tried and falsified by concrete Codex review
        counterexamples. Two spellings of the same type via a versioned
        inline namespace are therefore reported as two separate
        `EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT` findings rather than
        merged into one, even though (unlike the unrelated-declaration
        tests below) they really are the same entity here.
        """
        old = _snap(types=[
            _rec_at("detail::v1::cpu_feature_map", "spmd.h:10"),
            _rec_at("detail::cpu_feature_map", "spmd.h:10"),
        ])
        new = _snap(types=[])
        changes = detect_experimental_namespace_changes(
            old, new, experimental_namespaces=("detail",),
        )
        removed = [
            c for c in changes
            if c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
        ]
        assert len(removed) == 2

    def test_unrelated_types_sharing_version_shaped_segment_not_merged(
        self,
    ) -> None:
        """With no type-alias merging at all, two unrelated types that
        happen to share a leaf name via a version-shaped segment are
        naturally never merged -- removing one while the other survives
        must still report the removal.
        """
        old = _snap(types=[
            _rec("preview::v1::bar"),
            _rec("preview::bar"),
        ])
        new = _snap(types=[_rec("preview::bar")])
        changes = detect_experimental_namespace_changes(old, new)
        removed = [
            c for c in changes
            if c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
        ]
        assert len(removed) == 1
        assert removed[0].symbol == "preview::v1::bar"

    def test_unrelated_types_sharing_a_bare_filename_location_not_merged(
        self,
    ) -> None:
        """Codex review, P1, fresh finding: a `source_location` can
        legitimately be a bare filename with no line (clang/DWARF both
        omit the line when it's unavailable), so two unrelated types in
        the same file both missing line info would collide on an
        identical location under location-based identity. With type
        identity always `None` now, this can't happen -- included as a
        regression pin for the specific counterexample.
        """
        old = _snap(types=[
            _rec_at("preview::v1::bar", "shared.h"),
            _rec_at("preview::bar", "shared.h"),
        ])
        new = _snap(types=[_rec_at("preview::bar", "shared.h")])
        changes = detect_experimental_namespace_changes(old, new)
        removed = [
            c for c in changes
            if c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
        ]
        assert len(removed) == 1
        assert removed[0].symbol == "preview::v1::bar"

    def test_leaf_survives_namespace_qualified_param_type(self) -> None:
        """Reported bug: a demangled ELF-only symbol carries its full
        signature (``Class::method(ns::Type const&, long) const``), not just
        a qualified name. ``_segments()`` only tracks ``<``/``>`` depth, not
        ``(``/``)`` — so an unstripped signature whose parameter type is
        itself namespace-qualified used to split *inside* the parameter
        list and derive a leaf like ``"data_type const&, long) const"``
        instead of the real member name.
        """
        import abicheck.demangle as dm

        old_sig = (
            "ns::experimental::spmd::communicator<ns::spmd::none>::"
            "get_alloc_kind(ns::detail::data_type const&, long) const"
        )

        def fake_demangle(mangled_list: list[str]) -> dict[str, str]:
            return {m: old_sig for m in mangled_list}

        orig = dm.demangle_batch
        dm.demangle_batch = fake_demangle  # type: ignore[assignment]
        try:
            old = _snap(funcs=[_fn("", mangled="_ZN2ns13get_alloc_kindE")])
            new = _snap(funcs=[])
            changes = detect_experimental_namespace_changes(old, new)
        finally:
            dm.demangle_batch = orig  # type: ignore[assignment]

        assert len(changes) == 1
        c = changes[0]
        assert c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
        # The reported *leaf* must be the real member name, not a
        # parameter-type fragment split out of the unstripped signature —
        # unlike the leaf, the '{old}' declaration text legitimately still
        # carries the full signature (Codex review: needed to distinguish
        # one overload from another; see _func_index_items).
        assert "leaf 'get_alloc_kind'" in c.description
        assert "leaf 'data_type" not in c.description

    def test_removed_overload_reported_when_sibling_overload_survives(self) -> None:
        """Codex review: stripping the parameter list when deriving the
        *identity* used to index functions (not just the reported leaf)
        would collapse two overloads (``f(int)``, ``f(double)``) onto one
        key — so removing one overload while its sibling survives would
        read as "still present" and go unreported. The identity used for
        matching must keep the full signature; only the reported leaf name
        strips it.
        """
        import abicheck.demangle as dm

        def fake_demangle(mangled_list: list[str]) -> dict[str, str]:
            sigs = {
                "_ZN2ns13experimental1fEi": "ns::experimental::f(int)",
                "_ZN2ns13experimental1fEd": "ns::experimental::f(double)",
            }
            return {m: sigs[m] for m in mangled_list if m in sigs}

        orig = dm.demangle_batch
        dm.demangle_batch = fake_demangle  # type: ignore[assignment]
        try:
            old = _snap(funcs=[
                _fn("", mangled="_ZN2ns13experimental1fEi"),
                _fn("", mangled="_ZN2ns13experimental1fEd"),
            ])
            # Only the int overload disappears; the double overload survives
            # unchanged (still in experimental::, no stable replacement).
            new = _snap(funcs=[_fn("", mangled="_ZN2ns13experimental1fEd")])
            changes = detect_experimental_namespace_changes(old, new)
        finally:
            dm.demangle_batch = orig  # type: ignore[assignment]

        removed = [
            c for c in changes
            if c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
        ]
        assert len(removed) == 1
        assert "f(int)" in removed[0].symbol


# ---------------------------------------------------------------------------
# _looks_like_std_reexport
# ---------------------------------------------------------------------------


class TestLooksLikeStdReexport:
    def test_classic_reexport(self) -> None:
        assert _looks_like_std_reexport(
            "lib::execution::par", "std::execution::par",
        )

    def test_same_name_in_std_is_not_reexport(self) -> None:
        # If both sides are in std::, this is the genuine declaration,
        # not a re-export. We only report when a library namespace
        # aliases a std:: entity.
        assert not _looks_like_std_reexport(
            "std::execution::par", "std::execution::par",
        )

    def test_underlying_not_in_std_is_rejected(self) -> None:
        assert not _looks_like_std_reexport("lib::par", "other::par")

    def test_different_leaf_is_rejected(self) -> None:
        assert not _looks_like_std_reexport("lib::par", "std::seq")

    def test_empty_inputs(self) -> None:
        assert not _looks_like_std_reexport("", "std::par")
        assert not _looks_like_std_reexport("lib::par", "")


# ---------------------------------------------------------------------------
# detect_std_reexport_removed
# ---------------------------------------------------------------------------


class TestStdReexportRemoved:
    def test_reexport_removed_fires(self) -> None:
        # OLD: lib::par is a using-declaration aliasing std::par.
        # Because we cannot run a demangler here without external help,
        # we inject the mangled name as the demangled form by way of
        # the std-prefixed mangled string. The detector queries
        # ``demangle_batch`` which will return "" for synthetic mangled
        # names that don't start with ``_Z``, so we need an alternative
        # path: monkey-patch the demangler.
        import abicheck.diff_namespaces as mod

        captured: list[list[str]] = []

        def fake_demangle(mangled_list: list[str]) -> dict[str, str]:
            captured.append(list(mangled_list))
            return {"_ZN3lib3parE": "std::execution::par"}

        # The detector imports demangle_batch lazily from .demangle
        # inside the function; we patch the source module so the lazy
        # import resolves to our fake.
        import abicheck.demangle as dm
        orig = dm.demangle_batch
        dm.demangle_batch = fake_demangle  # type: ignore[assignment]
        try:
            old = _snap(funcs=[_fn("lib::execution::par", mangled="_ZN3lib3parE")])
            new = _snap(funcs=[])
            changes = mod.detect_std_reexport_removed(old, new)
        finally:
            dm.demangle_batch = orig  # type: ignore[assignment]

        assert len(changes) == 1
        c = changes[0]
        assert c.kind == ChangeKind.STD_REEXPORT_REMOVED
        assert c.symbol == "lib::execution::par"
        assert "std::execution::par" in c.description
        # ADR-044 D1 (Codex review): only ever emitted for a Visibility.PUBLIC
        # function, so tagged directly at construction time.
        assert c.public_reachable is True
        assert c.reachability_kind == "direct_public_symbol"

    def test_reexport_kept_does_not_fire(self) -> None:
        import abicheck.demangle as dm

        def fake_demangle(mangled_list: list[str]) -> dict[str, str]:
            return {m: "std::execution::par" for m in mangled_list}

        orig = dm.demangle_batch
        dm.demangle_batch = fake_demangle  # type: ignore[assignment]
        try:
            old = _snap(funcs=[
                _fn("lib::execution::par", mangled="_ZN3lib3parE"),
            ])
            new = _snap(funcs=[
                _fn("lib::execution::par", mangled="_ZN3lib3parE"),
            ])
            from abicheck.diff_namespaces import detect_std_reexport_removed
            changes = detect_std_reexport_removed(old, new)
        finally:
            dm.demangle_batch = orig  # type: ignore[assignment]

        assert changes == []

    def test_genuine_function_removal_does_not_fire(self) -> None:
        # The OLD function lives in lib::, demangles to lib:: (NOT std::),
        # so it's not a re-export; removal should NOT produce
        # STD_REEXPORT_REMOVED. (A separate detector reports func_removed.)
        import abicheck.demangle as dm

        def fake_demangle(mangled_list: list[str]) -> dict[str, str]:
            return {m: "lib::par" for m in mangled_list}

        orig = dm.demangle_batch
        dm.demangle_batch = fake_demangle  # type: ignore[assignment]
        try:
            old = _snap(funcs=[_fn("lib::par", mangled="_ZN3lib3parE")])
            new = _snap(funcs=[])
            from abicheck.diff_namespaces import detect_std_reexport_removed
            changes = detect_std_reexport_removed(old, new)
        finally:
            dm.demangle_batch = orig  # type: ignore[assignment]

        assert changes == []


# ---------------------------------------------------------------------------
# Combined entry point
# ---------------------------------------------------------------------------


class TestCombinedEntryPoint:
    def test_returns_findings_from_all_subdetectors(self) -> None:
        # Two findings expected: one EXPERIMENTAL_GRADUATED, one
        # EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT.
        old = _snap(funcs=[
            _fn("ns::experimental::a"),
            _fn("ns::experimental::b"),
        ])
        new = _snap(funcs=[
            _fn("ns::experimental::a"),
            _fn("ns::a"),
        ])
        changes = detect_namespace_patterns(old, new)
        kinds = sorted(c.kind for c in changes)
        assert ChangeKind.EXPERIMENTAL_GRADUATED in kinds
        assert ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT in kinds

    def test_default_namespaces_include_preview_and_v0(self) -> None:
        assert "experimental" in DEFAULT_EXPERIMENTAL_NAMESPACES
        assert "preview" in DEFAULT_EXPERIMENTAL_NAMESPACES
        assert "v0" in DEFAULT_EXPERIMENTAL_NAMESPACES


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------


class TestPipelineIntegration:
    def test_default_pipeline_includes_namespace_step(self) -> None:
        from abicheck.post_processing import DEFAULT_PIPELINE
        assert "detect_namespace_patterns" in DEFAULT_PIPELINE.step_names

    def test_findings_appear_via_compare(self) -> None:
        from abicheck.checker import compare

        old = _snap(funcs=[_fn("ns::experimental::bar")])
        new = _snap(funcs=[])
        # ``compare`` returns (verdict, diff_result); we only check
        # the changes list to keep the test resilient to verdict
        # plumbing changes elsewhere in the codebase.
        result = compare(old, new)
        # Some compare() signatures return a single object; pull
        # changes off whichever shape we got.
        changes = getattr(result, "changes", None) or getattr(
            result[1] if isinstance(result, tuple) else result, "changes",
        )
        kinds = [c.kind for c in changes]
        assert ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT in kinds


@pytest.mark.parametrize(
    "qname, expected_segments",
    [
        ("a::b::c", ["a", "b", "c"]),
        ("ns::experimental::sort<int>", ["ns", "experimental", "sort"]),
        ("ns::foo<bar::baz<int>>", ["ns", "foo"]),
        ("", []),
        ("plain", ["plain"]),
    ],
)
def test_segments_parametrized(qname: str, expected_segments: list[str]) -> None:
    assert _segments(qname) == expected_segments


# ---------------------------------------------------------------------------
# Inline-namespace version bump
# ---------------------------------------------------------------------------


class TestVersionSuffix:
    @pytest.mark.parametrize("seg, expected", [
        ("_V1", 1),
        ("_V12", 12),
        ("__v2", 2),
        ("v3", 3),
        ("__1", 1),
        ("V0", 0),
        ("v", None),
        ("plain", None),
        ("VNotANum", None),
        ("", None),
    ])
    def test_suffix(self, seg: str, expected: int | None) -> None:
        assert _version_suffix(seg) == expected


class TestVersionStrip:
    def test_strips_first_version_segment(self) -> None:
        stripped, ver = _version_strip_segments(["ns", "_V1", "sort"])
        assert stripped == ("ns", "sort")
        assert ver == 1

    def test_no_change_when_no_version(self) -> None:
        stripped, ver = _version_strip_segments(["ns", "stable", "sort"])
        assert stripped == ("ns", "stable", "sort")
        assert ver is None


class TestInlineNamespaceVersionBump:
    def test_function_version_bumped(self) -> None:
        old = _snap(funcs=[_fn("ns::_V1::sort")])
        new = _snap(funcs=[_fn("ns::_V2::sort")])
        changes = detect_inline_namespace_version_bump(old, new)
        assert len(changes) == 1
        c = changes[0]
        assert c.kind == ChangeKind.INLINE_NAMESPACE_VERSION_BUMPED
        assert "_V2" in c.symbol
        # Function-sourced entries are filtered to Visibility.PUBLIC by
        # _collect_versioned_entries, so this bump is a reliable public break.
        assert c.public_reachable is True
        assert c.reachability_kind == "direct_public_symbol"

    def test_type_version_bumped(self) -> None:
        old = _snap(types=[_rec("ns::__1::queue")])
        new = _snap(types=[_rec("ns::__2::queue")])
        changes = detect_inline_namespace_version_bump(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.INLINE_NAMESPACE_VERSION_BUMPED
        # Origin defaults to ScopeOrigin.UNKNOWN without --public-header — no
        # reliable public signal in that (common) case.
        assert changes[0].public_reachable is False
        assert changes[0].reachability_kind is None

    def test_public_header_type_version_bumped(self) -> None:
        """Codex review: RecordType.origin == ScopeOrigin.PUBLIC_HEADER (set
        only under ADR-024's opt-in --public-header scoping) IS a reliable
        public-reachability signal for a type-sourced bump, unlike the
        default ScopeOrigin.UNKNOWN case above."""
        old = _snap(types=[_rec_public("ns::__1::queue")])
        new = _snap(types=[_rec_public("ns::__2::queue")])
        changes = detect_inline_namespace_version_bump(old, new)
        assert len(changes) == 1
        assert changes[0].public_reachable is True
        assert changes[0].reachability_kind == "direct_public_symbol"

    def test_old_side_public_alone_is_reachable(self) -> None:
        """Codex review (fresh evidence): old-side public-header evidence
        alone is enough to prove an old-consumer break — an application
        linked against the old public symbol breaks regardless of whether
        the new symbol also carries public-header evidence. Public-header
        scoping can be asymmetric between two snapshots (the type moved out
        of the scoped header set, or the flag only covers one side), so
        requiring both sides tagged public (the old ``and``) let this case
        stay silently untagged and suppressible."""
        old = _snap(types=[_rec_public("ns::__1::queue")])
        new = _snap(types=[_rec("ns::__2::queue")])
        changes = detect_inline_namespace_version_bump(old, new)
        assert len(changes) == 1
        assert changes[0].public_reachable is True
        assert changes[0].reachability_kind == "direct_public_symbol"

    def test_new_side_public_alone_is_reachable(self) -> None:
        old = _snap(types=[_rec("ns::__1::queue")])
        new = _snap(types=[_rec_public("ns::__2::queue")])
        changes = detect_inline_namespace_version_bump(old, new)
        assert len(changes) == 1
        assert changes[0].public_reachable is True
        assert changes[0].reachability_kind == "direct_public_symbol"

    def test_no_version_segment_no_finding(self) -> None:
        old = _snap(funcs=[_fn("ns::sort")])
        new = _snap(funcs=[_fn("ns::sort")])
        assert detect_inline_namespace_version_bump(old, new) == []

    def test_downgrade_does_not_fire(self) -> None:
        # We only report bumps; a downgrade is suspicious enough that it
        # would manifest as a func_removed/func_added pair anyway.
        old = _snap(funcs=[_fn("ns::_V3::sort")])
        new = _snap(funcs=[_fn("ns::_V2::sort")])
        assert detect_inline_namespace_version_bump(old, new) == []

    def test_same_version_no_finding(self) -> None:
        old = _snap(funcs=[_fn("ns::_V1::sort")])
        new = _snap(funcs=[_fn("ns::_V1::sort")])
        assert detect_inline_namespace_version_bump(old, new) == []

    def test_works_when_only_one_symbol_present(self) -> None:
        # The existing symbol-level INLINE_NAMESPACE_MOVED detector
        # requires ≥2 moves; this detector deliberately does not.
        old = _snap(funcs=[_fn("ns::_V1::only_one")])
        new = _snap(funcs=[_fn("ns::_V2::only_one")])
        assert len(detect_inline_namespace_version_bump(old, new)) == 1


# ---------------------------------------------------------------------------
# _paired_stable_indices — generalized property tests
# ---------------------------------------------------------------------------
#
# Retrospective (see docs/contribute/adr/060-alias-merge-identity-evidence.md
# for the full assessment): every hand-written example test above pins one
# SPECIFIC counterexample a reviewer happened to find by inspection. Across
# this detector's review history, five rounds each found a genuine bug in
# the merge primitive itself (not in a caller's domain logic) that no
# hand-written example test caught, because each new example test was
# written to confirm the fix just made, not to adversarially search the
# input space the way a reviewer with fresh eyes did. These property tests
# instead state the primitive's actual CONTRACT as invariants and let
# Hypothesis search for a counterexample the way a reviewer would -- each
# property below is annotated with which review-round bug class it
# generalizes, so a regression in any of them fails a fast, deterministic
# test instead of waiting for another review round.
#
# The strategy below generates items sharing a `leaf` (and, once a version
# segment is stripped, a `canon`) purely by chance -- `_ENTITY_NAMES` has
# only two values, so collisions (both genuine-alias and coincidental)
# happen often across the default 100 Hypothesis examples without needing
# to force them.

_ENTITY_NAMES = st.sampled_from(["alpha", "beta"])
_IDENTITIES = st.sampled_from([None, "id-alpha", "id-beta", "id-other"])
_SIDES = st.sampled_from(["old", "new"])


@st.composite
def _alias_item_placements(draw):
    """A random list of ``(side, stripped, leaf, identity)`` placements.

    Every item's `leaf` is its entity name; `stripped` is either the bare
    `"ns::{entity}"` or the version-elided-from `"ns::v1::{entity}"` --
    both strip to the same canon, modeling the real experimental-stripped
    shape ``_func_index_items``/``_type_index_items`` produce. `identity`
    is drawn independently per item, so two items sharing a leaf may have
    equal identity (a genuine alias pair), unequal identity (coincidental
    name collision, the P1 counterexample class), or one/both `None` (no
    evidence, the "falsified identity mechanism" class).
    """
    n = draw(st.integers(min_value=1, max_value=6))
    placements = []
    for _ in range(n):
        entity = draw(_ENTITY_NAMES)
        versioned = draw(st.booleans())
        stripped = f"ns::v1::{entity}" if versioned else f"ns::{entity}"
        identity = draw(_IDENTITIES)
        side = draw(_SIDES)
        placements.append((side, stripped, entity, identity))
    return placements


def _build_paired_items(
    placements: list[tuple[str, str, str, object | None]],
) -> tuple[list[_IndexItem], list[_IndexItem]]:
    old_items: list[_IndexItem] = []
    new_items: list[_IndexItem] = []
    for i, (side, stripped, leaf, identity) in enumerate(placements):
        item = _IndexItem(qname=f"q{i}", stripped=stripped, leaf=leaf, identity=identity)
        (old_items if side == "old" else new_items).append(item)
    return old_items, new_items


class TestPairedStableIndicesProperties:
    @given(placements=_alias_item_placements())
    def test_every_item_appears_exactly_once(self, placements) -> None:
        """No item is lost or duplicated by the merge -- the most basic
        wiring invariant, but exactly what silently breaks first if a
        future edit changes the pooling/component-splitting logic."""
        old_items, new_items = _build_paired_items(placements)
        old_out, new_out = _paired_stable_indices(old_items, new_items)
        assert sorted(q for qs in old_out.values() for q in qs) == sorted(
            it.qname for it in old_items
        )
        assert sorted(q for qs in new_out.values() for q in qs) == sorted(
            it.qname for it in new_items
        )

    @given(placements=_alias_item_placements())
    def test_never_merges_across_raw_keys_without_shared_identity(
        self, placements
    ) -> None:
        """Generalizes the original P1 finding (blind version-stripping
        merged unrelated declarations sharing a leaf name) and its later
        variants (empty-string identity, value-equality-only identity):
        whenever a single output key's qnames span two or more DISTINCT
        raw spellings (``stripped``), every pair of those distinct
        spellings must be connected by at least one shared, non-``None``
        identity value somewhere among their own items. No amount of
        coincidental name shape is ever enough on its own.

        Items sharing the exact same ``stripped`` (a literal, byte-
        identical raw-key collision -- e.g. the existing, unconditional
        experimental/stable-graduation bucketing this module has always
        done, unrelated to version-segment aliasing) are grouped by raw
        key first and exempted from needing identity evidence *among
        themselves* -- that bucketing predates and is orthogonal to the
        alias-merge feature this property is about (a Hypothesis-found
        regression: an earlier version of this property demanded evidence
        even within one raw key, which is stricter than the primitive's
        actual, correct contract and broke on legitimate same-spelling
        collisions).
        """
        old_items, new_items = _build_paired_items(placements)
        by_qname = {it.qname: it for it in old_items + new_items}
        old_out, new_out = _paired_stable_indices(old_items, new_items)
        key_to_qnames: dict[tuple[str, str], list[str]] = {}
        for out in (old_out, new_out):
            for key, qnames in out.items():
                key_to_qnames.setdefault(key, []).extend(qnames)
        for key, qnames in key_to_qnames.items():
            items = [by_qname[q] for q in qnames]
            idents_by_raw: dict[str, set[object]] = {}
            for it in items:
                if it.identity is not None:
                    idents_by_raw.setdefault(it.stripped, set()).add(it.identity)
            raw_keys = sorted({it.stripped for it in items})
            for i, raw_a in enumerate(raw_keys):
                for raw_b in raw_keys[i + 1 :]:
                    a, b = idents_by_raw.get(raw_a), idents_by_raw.get(raw_b)
                    assert a and b and (a & b), (key, raw_a, raw_b, a, b)

    @given(placements=_alias_item_placements())
    def test_shared_identity_within_a_leaf_always_merges(self, placements) -> None:
        """Generalizes the "canonicalize keys across unequal alias
        membership" finding: two items that DO share real identity
        evidence must end up under the SAME key regardless of which
        side(s) they were placed on -- a singleton on one side and a pair
        on the other must not silently disagree on the key for what is
        the same entity.

        This guarantee only holds when each side's raw key is itself
        UNAMBIGUOUS evidence -- exactly one distinct identity value among
        its own items (Codex review, P1, fresh evidence: a raw key
        aggregating more than one identity, the overloaded-function-name
        shape, is not reliable evidence and must not be required to merge
        with anything; see ``_paired_stable_indices``'s docstring). Two
        raw keys whose *own* identity sets are each a singleton, and those
        singletons are equal, must still resolve to one key regardless of
        side placement -- that part of the original finding is unchanged.
        """
        old_items, new_items = _build_paired_items(placements)
        old_out, new_out = _paired_stable_indices(old_items, new_items)
        qname_to_key: dict[str, tuple[str, str]] = {}
        for out in (old_out, new_out):
            for key, qnames in out.items():
                for q in qnames:
                    qname_to_key[q] = key
        by_leaf: dict[str, list[_IndexItem]] = {}
        for it in old_items + new_items:
            by_leaf.setdefault(it.leaf, []).append(it)
        for group in by_leaf.values():
            # Per-raw-key identity sets, aggregated across old+new (mirrors
            # `_paired_stable_indices`'s own `raw_identities`) -- only a
            # raw key whose set is an unambiguous singleton is eligible to
            # participate in the merge guarantee below.
            idents_by_raw: dict[str, set[object]] = {}
            for it in group:
                if it.identity is not None:
                    idents_by_raw.setdefault(it.stripped, set()).add(it.identity)
            unambiguous_raw = {
                raw for raw, idents in idents_by_raw.items() if len(idents) == 1
            }
            by_identity: dict[object, set[tuple[str, str]]] = {}
            for it in group:
                if it.identity is None or it.stripped not in unambiguous_raw:
                    continue
                by_identity.setdefault(it.identity, set()).add(qname_to_key[it.qname])
            for identity, keys in by_identity.items():
                assert len(keys) == 1, (identity, keys)

    @given(placements=_alias_item_placements(), shuffle_seed=st.integers())
    def test_output_independent_of_encounter_order(
        self, placements, shuffle_seed
    ) -> None:
        """Generalizes the "keep merged keys stable across declaration
        order" finding directly: shuffling the SAME items (same content,
        different list order -- modeling two independent extractor runs
        listing declarations differently) must never change the resulting
        keys or groupings.
        """
        import random

        old_items, new_items = _build_paired_items(placements)
        old_out1, new_out1 = _paired_stable_indices(old_items, new_items)

        rng = random.Random(shuffle_seed)
        shuffled_old = list(old_items)
        shuffled_new = list(new_items)
        rng.shuffle(shuffled_old)
        rng.shuffle(shuffled_new)
        old_out2, new_out2 = _paired_stable_indices(shuffled_old, shuffled_new)

        def _normalize(d: dict[tuple[str, str], list[str]]) -> set[tuple[tuple[str, str], frozenset]]:
            return {(k, frozenset(v)) for k, v in d.items()}

        assert _normalize(old_out1) == _normalize(old_out2)
        assert _normalize(new_out1) == _normalize(new_out2)

    @given(placements=_alias_item_placements())
    def test_identity_ignores_parameter_signature_noise(self, placements) -> None:
        """Generalizes the P2 "canonicalize only the function's
        declaration scope" finding: appending an arbitrary, version-shaped
        parenthesized suffix (modeling a function's parameter-list
        signature) to every item's `stripped` must never change which
        items get merged -- the canon computation must be blind to
        anything after the top-level `(`.
        """
        old_items, new_items = _build_paired_items(placements)
        old_out1, new_out1 = _paired_stable_indices(old_items, new_items)

        def _with_signature_noise(items: list[_IndexItem]) -> list[_IndexItem]:
            return [
                it._replace(stripped=f"{it.stripped}(ns::v2::T, ns::v3::U)")
                for it in items
            ]

        old_out2, new_out2 = _paired_stable_indices(
            _with_signature_noise(old_items), _with_signature_noise(new_items)
        )

        def _grouping(
            d: dict[tuple[str, str], list[str]],
        ) -> set[frozenset[str]]:
            return {frozenset(v) for v in d.values()}

        assert _grouping(old_out1) == _grouping(old_out2)
        assert _grouping(new_out1) == _grouping(new_out2)

    @given(placements=_alias_item_placements())
    def test_qname_lists_are_content_stable_regardless_of_input_list_identity(
        self, placements
    ) -> None:
        """A narrower companion to `test_output_independent_of_encounter_order`:
        even the exact *list* of qnames stored per key (not just the set of
        keys/groupings) must be the same multiset across two independent
        calls with the same content. This is what a hash-randomized
        internal ordering step (pooling raw keys through a bare ``set``,
        rather than a sorted list) would violate even though the coarser
        grouping-only property above cannot see it -- see the dedicated
        cross-process test below for the actual `PYTHONHASHSEED` evidence.
        """
        old_items, new_items = _build_paired_items(placements)
        old_out1, new_out1 = _paired_stable_indices(list(old_items), list(new_items))
        old_out2, new_out2 = _paired_stable_indices(list(old_items), list(new_items))

        def _normalize(d: dict[tuple[str, str], list[str]]) -> set[tuple[tuple[str, str], tuple[str, ...]]]:
            return {(k, tuple(sorted(v))) for k, v in d.items()}

        assert _normalize(old_out1) == _normalize(old_out2)
        assert _normalize(new_out1) == _normalize(new_out2)


class TestDeterminismAcrossHashSeeds:
    def test_reported_symbol_independent_of_pythonhashseed(self) -> None:
        """Codex review, P1, fresh finding: pooling raw keys through a bare
        ``{*raw_old, *raw_new}`` set iterated them in Python's
        hash-randomized order (``PYTHONHASHSEED``, randomized per process
        by default), so which of two alias spellings ended up first in a
        merged bucket -- and therefore which one
        ``_emit_experimental_change`` reports as the finding's ``symbol``
        -- could differ between two runs of the IDENTICAL input. Since a
        suppression rule can match on the exact reported symbol name, this
        made the verdict itself depend on an environment variable most
        users never set. A property test within a single pytest process
        cannot observe this (the hash seed is fixed for the process's
        lifetime); this test spawns real subprocesses with different
        explicit seeds and asserts they agree, the only way to actually
        exercise the bug this class of fix addresses.
        """
        import os
        import subprocess
        import sys

        script = (
            "from abicheck.diff_namespaces import detect_experimental_namespace_changes\n"
            "from abicheck.model import AbiSnapshot, Function, Visibility\n"
            "def mk(names):\n"
            "    funcs = [Function(name=n, mangled='_ZSame', return_type='void',"
            " visibility=Visibility.PUBLIC) for n in names]\n"
            "    return AbiSnapshot(library='l', version='1', functions=funcs)\n"
            "old = mk(['preview::v1::foo', 'preview::foo'])\n"
            "new = mk([])\n"
            "changes = detect_experimental_namespace_changes(old, new)\n"
            "print(changes[0].symbol if changes else None)\n"
        )
        symbols = set()
        for seed in ("0", "1", "2", "3", "4", "5"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            result = subprocess.run(
                [sys.executable, "-c", script],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stderr
            symbols.add(result.stdout.strip())
        assert len(symbols) == 1, symbols
