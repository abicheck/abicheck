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

from abicheck.checker_policy import ChangeKind
from abicheck.diff_namespaces import (
    DEFAULT_EXPERIMENTAL_NAMESPACES,
    _looks_like_std_reexport,
    _origin_by_name,
    _segments,
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


def _rec_nontrivial(name: str, size_bits: int = 64) -> RecordType:
    """A type with real structural content (a known size), unlike the
    empty/trivial ``_rec()`` -- needed to exercise the type-alias identity
    fingerprint, which deliberately returns no evidence for an empty
    record (Codex review, P1: every empty class of the same kind shares
    the same trivial fingerprint, so it isn't distinguishing evidence)."""
    return RecordType(name=name, kind="class", size_bits=size_bits)


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
    def test_silent_function_removal(self) -> None:
        old = _snap(funcs=[_fn("ns::experimental::bar")])
        new = _snap(funcs=[])
        changes = detect_experimental_namespace_changes(old, new)
        assert len(changes) == 1
        c = changes[0]
        assert c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
        assert c.symbol == "ns::experimental::bar"
        # ADR-044 D1 (Codex review): _index_funcs_by_stable_key only ever
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

    def test_versioned_inline_namespace_type_spellings_report_once(self) -> None:
        # Non-trivial (known-size) records: a genuine structural match is
        # real identity evidence for a true inline-namespace alias pair.
        old = _snap(types=[
            _rec_nontrivial("detail::v1::cpu_feature_map"),
            _rec_nontrivial("detail::cpu_feature_map"),
        ])
        new = _snap(types=[])
        changes = detect_experimental_namespace_changes(
            old, new, experimental_namespaces=("detail",),
        )
        removed = [
            c for c in changes
            if c.kind == ChangeKind.EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT
        ]
        assert len(removed) == 1

    def test_unrelated_empty_types_sharing_version_shaped_segment_not_merged(
        self,
    ) -> None:
        """Codex review, P1: an empty/trivial record carries no
        distinguishing structure -- every empty class of the same kind
        shares the identical fingerprint, so two genuinely unrelated empty
        types (``preview::v1::bar`` and ``preview::bar``) must NOT be
        treated as an alias pair. Removing one while the other survives
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
        # one overload from another; see _index_funcs_by_stable_key).
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
