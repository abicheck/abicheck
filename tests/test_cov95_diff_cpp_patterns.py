# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Coverage-focused unit tests for ``abicheck.diff_cpp_patterns``.

These exercise the internal helpers and edge-case branches that the
existing ``test_cpp_pattern_detectors.py`` suite does not reach:

* ``_unqualified_function_name`` template-arg stripping loop
* ``_parent_namespace`` with no ``::``
* ``_stable_leading_template_args`` degenerate / mismatch branches
* ``_extract_template_args`` no-match path
* ``_split_top_level_commas_local`` nesting
* tag-rename candidate rejection paths
* inline-body pimpl helper branches
* bundle-SONAME directory scanning and best-effort SONAME readers

Pure Python, no external tools — part of the default fast lane.
"""

from __future__ import annotations

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.diff_cpp_patterns import (
    BundleMember,
    _cohort_key,
    _emit_inline_body_findings,
    _extract_soname_major,
    _extract_template_args,
    _find_public_pimpl_holders,
    _inline_accessors_for,
    _last_segment,
    _parent_namespace,
    _read_elf_soname,
    _read_soname_best_effort,
    _split_top_level_commas_local,
    _stable_leading_template_args,
    _symbols_embedding_leaf,
    _unqualified_function_name,
    bundle_members_from_directory,
    detect_bundle_soname_skew,
    detect_default_template_arg_changed,
    detect_inline_body_renamed_member,
    detect_tag_type_renamed,
)
from abicheck.model import (
    AbiSnapshot,
    Function,
    RecordType,
    TypeField,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fn(
    name: str,
    mangled: str | None = None,
    *,
    is_inline: bool = False,
) -> Function:
    return Function(
        name=name,
        mangled=mangled or f"_Z{len(name)}{name.replace('::', '')}v",
        return_type="void",
        params=[],
        is_inline=is_inline,
    )


def _snap(
    name: str = "lib",
    functions: list[Function] | None = None,
    types: list[RecordType] | None = None,
) -> AbiSnapshot:
    return AbiSnapshot(
        library=name,
        version="1.0",
        functions=functions or [],
        types=types or [],
    )


# ---------------------------------------------------------------------------
# _unqualified_function_name (lines 145-157)
# ---------------------------------------------------------------------------


class TestUnqualifiedFunctionName:
    def test_plain_qualified(self) -> None:
        assert _unqualified_function_name("ns::function") == "function"

    def test_function_template_args_stripped(self) -> None:
        assert _unqualified_function_name("ns::function<int>") == "function"

    def test_class_template_args_stripped(self) -> None:
        # The trailing ``::method`` must survive after the class template
        # args are removed.
        assert _unqualified_function_name("ns::Class<float, A>::method") == "method"

    def test_nested_template_args(self) -> None:
        assert _unqualified_function_name("ns::Class<X<int>>::method<Y>") == "method"

    def test_unqualified_with_template(self) -> None:
        assert _unqualified_function_name("function<int>") == "function"


# ---------------------------------------------------------------------------
# _last_segment / _parent_namespace (line 104)
# ---------------------------------------------------------------------------


class TestNameSegments:
    def test_last_segment_qualified(self) -> None:
        assert _last_segment("a::b::c") == "c"

    def test_last_segment_unqualified(self) -> None:
        assert _last_segment("foo") == "foo"

    def test_parent_namespace_qualified(self) -> None:
        assert _parent_namespace("a::b::c") == "a::b"

    def test_parent_namespace_unqualified(self) -> None:
        # No ``::`` → empty parent namespace (line 104).
        assert _parent_namespace("foo") == ""


# ---------------------------------------------------------------------------
# _stable_leading_template_args (lines 521, 524, 527)
# ---------------------------------------------------------------------------


class TestStableLeadingTemplateArgs:
    def test_trailing_change_true(self) -> None:
        assert _stable_leading_template_args("float, A", "float, B") is True

    def test_leading_change_false(self) -> None:
        assert _stable_leading_template_args("float, A", "double, B") is False

    def test_single_arg_degenerate_true(self) -> None:
        # len 1 vs len 1 → degenerate accepted (line 524).
        assert _stable_leading_template_args("A", "B") is True

    def test_empty_old_args_false(self) -> None:
        # Empty list → False (line 521).
        assert _stable_leading_template_args("", "x") is False

    def test_empty_new_args_false(self) -> None:
        assert _stable_leading_template_args("x", "") is False

    def test_length_mismatch_false(self) -> None:
        # Different argument-list lengths → False (line 527).
        assert _stable_leading_template_args("a, b", "a, b, c") is False

    def test_no_difference_returns_false(self) -> None:
        # Identical lists: diff_idx == len → not (>= 1 and < len) → False.
        assert _stable_leading_template_args("a, b", "a, b") is False


# ---------------------------------------------------------------------------
# _split_top_level_commas_local
# ---------------------------------------------------------------------------


class TestSplitTopLevelCommas:
    def test_simple(self) -> None:
        assert _split_top_level_commas_local("a, b, c") == ["a", " b", " c"]

    def test_nested_angle_brackets_not_split(self) -> None:
        # The comma inside ``<...>`` must not split.
        assert _split_top_level_commas_local("a, X<b, c>") == ["a", " X<b, c>"]

    def test_empty(self) -> None:
        assert _split_top_level_commas_local("") == []


# ---------------------------------------------------------------------------
# _extract_template_args (lines 577->588, 589)
# ---------------------------------------------------------------------------


class TestExtractTemplateArgs:
    def test_function_template(self) -> None:
        assert _extract_template_args("ns::function<float>") == "float"

    def test_with_call_args(self) -> None:
        assert _extract_template_args("ns::function<float>(int)") == "float"

    def test_method_class_args(self) -> None:
        assert _extract_template_args("ns::Class<float, A>::method") == "float, A"

    def test_innermost_class_args(self) -> None:
        assert _extract_template_args("Outer<X<int>>::Inner<Y>") == "Y"

    def test_no_template_returns_none(self) -> None:
        # No ``<...>`` group at all → None (line 589).
        assert _extract_template_args("ns::compute") is None

    def test_unbalanced_returns_none(self) -> None:
        # ``operator<`` has an unmatched ``<`` → no balanced group → None.
        assert _extract_template_args("operator<") is None


# ---------------------------------------------------------------------------
# detect_default_template_arg_changed extra branches (625, 630, 642)
# ---------------------------------------------------------------------------


class TestDefaultTemplateArgBranches:
    def test_removed_without_template_args_skipped(self) -> None:
        # Removed fn has no ``<...>`` → old_args is None → continue (625).
        old = _snap(functions=[_fn("ns::plain::compute", "_Zoldplain")])
        new = _snap(functions=[_fn("ns::other::compute", "_Znewother")])
        assert detect_default_template_arg_changed(old, new) == []

    def test_candidate_same_args_skipped(self) -> None:
        # Candidate has identical template args → new_args == old_args
        # branch (line 630) — no finding because nothing differs.
        old = _snap(
            functions=[
                _fn("ns::d<float, A>::compute", "_Zrm"),
            ]
        )
        new = _snap(
            functions=[
                # same stem + same args → no change
                _fn("ns::d<float, A>::compute", "_Zrm"),
                # candidate that survives but identical args under same stem
                _fn("ns::d<float, A>::compute", "_Zsurv"),
            ]
        )
        assert detect_default_template_arg_changed(old, new) == []

    def test_candidate_without_template_args_skipped(self) -> None:
        # Surviving candidate under same stem has NO template args → its
        # new_args is None → continue at line 629-630.
        old = _snap(
            functions=[
                _fn("ns::compute<float, A>", "_Zrm1"),
            ]
        )
        # Same callable stem "ns::compute" but candidate has no <...>.
        new = _snap(
            functions=[
                _fn("ns::compute", "_Zplain"),
            ]
        )
        assert detect_default_template_arg_changed(old, new) == []

    def test_trailing_change_emits_one_finding(self) -> None:
        old = _snap(
            functions=[
                _fn("ns::d<float, ns::A>::compute", "_Zold"),
            ]
        )
        new = _snap(
            functions=[
                _fn("ns::d<float, ns::B>::compute", "_Znew"),
            ]
        )
        findings = detect_default_template_arg_changed(old, new)
        assert len(findings) == 1
        assert findings[0].kind == ChangeKind.DEFAULT_TEMPLATE_ARG_CHANGED

    def test_leading_arg_change_not_flagged(self) -> None:
        # Both args differ (leading position changes) → _stable_leading_template_args
        # returns False → ``continue`` at line 639, no finding emitted.
        old = _snap(functions=[_fn("ns::d<float, ns::A>::compute", "_Zold")])
        new = _snap(functions=[_fn("ns::d<double, ns::B>::compute", "_Znew")])
        assert detect_default_template_arg_changed(old, new) == []


# ---------------------------------------------------------------------------
# _symbols_embedding_leaf and tag-rename rejection paths (423, 441)
# ---------------------------------------------------------------------------


class TestSymbolsEmbeddingLeaf:
    def test_matches_with_underscore_stripped(self) -> None:
        # leaf "brute_force" → token "bruteforce"; both forms checked.
        out = _symbols_embedding_leaf({"_Zbruteforce", "_Zunrelated"}, "brute_force")
        assert out == ["_Zbruteforce"]

    def test_no_match_empty(self) -> None:
        assert _symbols_embedding_leaf({"_Zfoo"}, "bar") == []


class TestTagRenameRejectionPaths:
    def test_candidate_without_added_token_no_finding(self) -> None:
        # Removed tag has symbol evidence but the candidate added tag has
        # NO matching added symbol → ``continue`` at line 423, loop
        # exhausts → return None at 441.
        old_tag = RecordType(name="ns::oldtag", kind="struct", size_bits=8, fields=[])
        new_tag = RecordType(name="ns::newtag", kind="struct", size_bits=8, fields=[])
        old = _snap(
            functions=[_fn("ns_oldtag_inst", "_Zns_oldtag_inst")],
            types=[old_tag],
        )
        # No added symbol embeds "newtag" → candidate rejected.
        new = _snap(
            functions=[_fn("unrelated", "_Zunrelated")],
            types=[new_tag],
        )
        assert detect_tag_type_renamed(old, new) == []

    def test_no_removed_symbol_evidence_no_finding(self) -> None:
        # Removed tag has NO symbol embedding its leaf → early return None.
        old_tag = RecordType(name="ns::oldtag", kind="struct", size_bits=8, fields=[])
        new_tag = RecordType(name="ns::newtag", kind="struct", size_bits=8, fields=[])
        old = _snap(functions=[_fn("totally_other", "_Zother")], types=[old_tag])
        new = _snap(
            functions=[_fn("ns_newtag_inst", "_Zns_newtag_inst")], types=[new_tag]
        )
        assert detect_tag_type_renamed(old, new) == []

    def test_successful_tag_rename_emits_change(self) -> None:
        # Empty tag vanishes from old, empty tag appears in new under the same
        # parent namespace, with both old-leaf and new-leaf symbol evidence →
        # successful TAG_TYPE_RENAMED (the ``return Change`` at line 424).
        old_tag = RecordType(name="ns::oldtag", kind="struct", size_bits=8, fields=[])
        new_tag = RecordType(name="ns::newtag", kind="struct", size_bits=8, fields=[])
        old = _snap(
            functions=[_fn("ns_oldtag_inst", "_Zns_oldtag_inst")],
            types=[old_tag],
        )
        new = _snap(
            functions=[_fn("ns_newtag_inst", "_Zns_newtag_inst")],
            types=[new_tag],
        )
        findings = detect_tag_type_renamed(old, new)
        assert len(findings) == 1
        ch = findings[0]
        assert ch.kind == ChangeKind.TAG_TYPE_RENAMED
        assert ch.old_value == "ns::oldtag"
        assert ch.new_value == "ns::newtag"
        assert "_Zns_oldtag_inst" in (ch.affected_symbols or [])


# ---------------------------------------------------------------------------
# inline-body helpers (744, 746, 753, 827, 829, 847, 849)
# ---------------------------------------------------------------------------


class TestFindPublicPimplHolders:
    def test_finds_holder_referencing_internal(self) -> None:
        holder = RecordType(
            name="mylib::Widget",
            kind="class",
            size_bits=64,
            fields=[
                TypeField(name="impl_", type="std::unique_ptr<mylib::detail::Impl>")
            ],
        )
        found = _find_public_pimpl_holders([holder], "mylib::detail::Impl", ("detail",))
        assert found == {"mylib::Widget"}

    def test_skips_internal_type_itself(self) -> None:
        # A type that is itself internal is not a public holder (line 824).
        internal = RecordType(
            name="mylib::detail::Impl",
            kind="class",
            size_bits=64,
            fields=[TypeField(name="self_", type="mylib::detail::Impl*")],
        )
        found = _find_public_pimpl_holders(
            [internal], "mylib::detail::Impl", ("detail",)
        )
        assert found == set()

    def test_no_matching_field_no_holder(self) -> None:
        holder = RecordType(
            name="mylib::Widget",
            kind="class",
            size_bits=32,
            fields=[TypeField(name="x", type="int")],
        )
        found = _find_public_pimpl_holders([holder], "mylib::detail::Impl", ("detail",))
        assert found == set()


class TestInlineAccessorsFor:
    def test_inline_member_in_holder(self) -> None:
        fn = _fn("mylib::Widget::get", "_Zget", is_inline=True)
        out = _inline_accessors_for([fn], {"mylib::Widget"})
        assert out == [fn]

    def test_non_inline_skipped(self) -> None:
        fn = _fn("mylib::Widget::get", "_Zget", is_inline=False)
        assert _inline_accessors_for([fn], {"mylib::Widget"}) == []

    def test_unqualified_name_skipped(self) -> None:
        # No ``::`` in name → line 847 continue.
        fn = _fn("freefunc", "_Zfree", is_inline=True)
        assert _inline_accessors_for([fn], {"mylib::Widget"}) == []

    def test_holder_not_in_set_skipped(self) -> None:
        # Qualified inline fn but enclosing class not a holder (line 849).
        fn = _fn("mylib::Other::get", "_Zget", is_inline=True)
        assert _inline_accessors_for([fn], {"mylib::Widget"}) == []


class TestEmitInlineBodyFindings:
    def test_no_pimpl_holder_skips_candidate(self) -> None:
        # rename candidate present, but neither old nor new types hold a
        # pimpl to the internal type → ``continue`` at line 746.
        findings = _emit_inline_body_findings(
            [("mylib::detail::Impl", "a_", "b_")],
            old_types={},
            new_types={},
            old_functions=[],
            namespaces=("detail",),
        )
        assert findings == []

    def test_holder_but_no_inline_accessor_skips(self) -> None:
        holder = RecordType(
            name="mylib::Widget",
            kind="class",
            size_bits=64,
            fields=[TypeField(name="impl_", type="mylib::detail::Impl*")],
        )
        findings = _emit_inline_body_findings(
            [("mylib::detail::Impl", "a_", "b_")],
            old_types={"mylib::Widget": holder},
            new_types={"mylib::Widget": holder},
            old_functions=[_fn("mylib::Widget::get", "_Zget", is_inline=False)],
            namespaces=("detail",),
        )
        assert findings == []

    def test_old_types_fallback_holder(self) -> None:
        # new_types has no holder but old_types does (line 744 fallback).
        holder = RecordType(
            name="mylib::Widget",
            kind="class",
            size_bits=64,
            fields=[TypeField(name="impl_", type="mylib::detail::Impl*")],
        )
        inline_fn = _fn("mylib::Widget::get", "_Zget", is_inline=True)
        findings = _emit_inline_body_findings(
            [("mylib::detail::Impl", "a_", "b_")],
            old_types={"mylib::Widget": holder},
            new_types={},
            old_functions=[inline_fn],
            namespaces=("detail",),
        )
        assert len(findings) == 1
        assert findings[0].kind == ChangeKind.INLINE_BODY_REFERENCES_RENAMED_MEMBER
        assert findings[0].old_value == "a_"
        assert findings[0].new_value == "b_"

    def test_duplicate_candidate_dedup(self) -> None:
        # Same (holder, internal, old_field) appears twice → second is
        # skipped via the ``seen`` set (line 753).
        holder = RecordType(
            name="mylib::Widget",
            kind="class",
            size_bits=64,
            fields=[TypeField(name="impl_", type="mylib::detail::Impl*")],
        )
        inline_fn = _fn("mylib::Widget::get", "_Zget", is_inline=True)
        findings = _emit_inline_body_findings(
            [
                ("mylib::detail::Impl", "a_", "b_"),
                ("mylib::detail::Impl", "a_", "b_"),
            ],
            old_types={"mylib::Widget": holder},
            new_types={"mylib::Widget": holder},
            old_functions=[inline_fn],
            namespaces=("detail",),
        )
        # Only one finding despite the duplicate candidate.
        assert len(findings) == 1


class TestDetectInlineBodyNoCandidates:
    def test_no_rename_candidates_returns_empty(self) -> None:
        # No FIELD_RENAMED and no paired field changes → early return [].
        old = _snap()
        new = _snap()
        changes: list[Change] = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="x", description=""),
        ]
        assert detect_inline_body_renamed_member(old, new, changes) == []

    def test_field_renamed_candidate_path(self) -> None:
        # Drive the FIELD_RENAMED collector (lines 682-686).
        holder = RecordType(
            name="mylib::Widget",
            kind="class",
            size_bits=64,
            fields=[TypeField(name="impl_", type="mylib::detail::Impl*")],
        )
        impl = RecordType(name="mylib::detail::Impl", kind="class", size_bits=32)
        inline_fn = _fn("mylib::Widget::get", "_Zget", is_inline=True)
        old = _snap(functions=[inline_fn], types=[impl, holder])
        new = _snap(functions=[inline_fn], types=[impl, holder])
        changes = [
            Change(
                kind=ChangeKind.FIELD_RENAMED,
                symbol="mylib::detail::Impl::a_",
                description="",
                old_value="a_",
                new_value="b_",
            ),
        ]
        findings = detect_inline_body_renamed_member(old, new, changes)
        assert len(findings) == 1
        assert findings[0].old_value == "a_"

    def test_field_renamed_non_internal_skipped(self) -> None:
        # FIELD_RENAMED on a public (non-detail) type → not internal,
        # collector skips it (line 684 continue), no candidates.
        old = _snap()
        new = _snap()
        changes = [
            Change(
                kind=ChangeKind.FIELD_RENAMED,
                symbol="mylib::Public::a_",
                description="",
                old_value="a_",
                new_value="b_",
            ),
        ]
        assert detect_inline_body_renamed_member(old, new, changes) == []

    def test_paired_field_unbalanced_not_collected(self) -> None:
        # 1 removed but 0 added on the internal type → counts differ,
        # so _collect_paired_field_candidates produces nothing (line 721).
        old = _snap()
        new = _snap()
        changes = [
            Change(
                kind=ChangeKind.TYPE_FIELD_REMOVED,
                symbol="mylib::detail::Impl::a_",
                description="",
            ),
        ]
        assert detect_inline_body_renamed_member(old, new, changes) == []

    def test_paired_field_no_namespace_in_symbol_skipped(self) -> None:
        # TYPE_FIELD_REMOVED whose symbol has no ``::`` → line 708 continue.
        old = _snap()
        new = _snap()
        changes = [
            Change(
                kind=ChangeKind.TYPE_FIELD_REMOVED, symbol="bareField", description=""
            ),
            Change(
                kind=ChangeKind.TYPE_FIELD_ADDED, symbol="bareField2", description=""
            ),
        ]
        assert detect_inline_body_renamed_member(old, new, changes) == []


# ---------------------------------------------------------------------------
# bundle SONAME skew branches (920, 925)
# ---------------------------------------------------------------------------


class TestBundleSkewBranches:
    def test_cohort_key(self) -> None:
        assert _cohort_key("libfoo_core.so.2") == "libfoo_core"

    def test_new_member_missing_skipped(self) -> None:
        # Old library cohort has no new counterpart → line 920 continue,
        # leaving deltas empty → line 925 return [].
        old = [BundleMember("libgone.so.1", "libgone.so.1", 1)]
        new = [BundleMember("libother.so.1", "libother.so.1", 1)]
        assert detect_bundle_soname_skew(old, new) == []

    def test_empty_deltas_returns_empty(self) -> None:
        assert detect_bundle_soname_skew([], []) == []


# ---------------------------------------------------------------------------
# bundle_members_from_directory (964-984)
# ---------------------------------------------------------------------------


class TestBundleMembersFromDirectory:
    def test_nonexistent_dir_returns_empty(self, tmp_path) -> None:
        missing = tmp_path / "does_not_exist"
        assert bundle_members_from_directory(str(missing)) == []

    def test_skips_subdirs_and_non_elf(self, tmp_path) -> None:
        # A subdirectory (not a file) → skipped at line 969.
        (tmp_path / "subdir").mkdir()
        # A non-ELF file (wrong magic) → _read_soname_best_effort None → skip.
        (tmp_path / "readme.txt").write_bytes(b"not an elf at all")
        # An ELF-magic file whose parse yields no soname → still skipped.
        (tmp_path / "libstub.so").write_bytes(b"\x7fELF" + b"\x00" * 60)
        members = bundle_members_from_directory(str(tmp_path))
        assert members == []

    def test_constructs_member_from_synthetic_soname(
        self, tmp_path, monkeypatch
    ) -> None:
        # Monkeypatch the SONAME reader so a recognised .so yields a real
        # SONAME with a major version → BundleMember constructed (977-983).
        from abicheck import diff_cpp_patterns as mod

        lib = tmp_path / "libfoo.so.3"
        lib.write_bytes(b"\x7fELF" + b"\x00" * 60)

        def fake_reader(path: str) -> str | None:
            return "libfoo.so.3" if path.endswith("libfoo.so.3") else None

        monkeypatch.setattr(mod, "_read_soname_best_effort", fake_reader)
        members = bundle_members_from_directory(str(tmp_path))
        assert len(members) == 1
        assert members[0].library == "libfoo.so.3"
        assert members[0].soname == "libfoo.so.3"
        assert members[0].soname_major == 3

    def test_soname_without_major_skipped(self, tmp_path, monkeypatch) -> None:
        # SONAME present but no extractable major → line 975-976 continue.
        from abicheck import diff_cpp_patterns as mod

        lib = tmp_path / "libfoo.so"
        lib.write_bytes(b"\x7fELF" + b"\x00" * 60)
        monkeypatch.setattr(mod, "_read_soname_best_effort", lambda p: "libfoo.so")
        assert bundle_members_from_directory(str(tmp_path)) == []


# ---------------------------------------------------------------------------
# _read_soname_best_effort (990-998) and _read_elf_soname (1004-1014)
# ---------------------------------------------------------------------------


class TestReadSonameBestEffort:
    def test_nonexistent_path_returns_none(self, tmp_path) -> None:
        # open() raises OSError → None (line 994).
        missing = tmp_path / "nope.so"
        assert _read_soname_best_effort(str(missing)) is None

    def test_non_elf_magic_returns_none(self, tmp_path) -> None:
        f = tmp_path / "thing.bin"
        f.write_bytes(b"MZ\x00\x00rest")  # PE magic, not handled → None (998).
        assert _read_soname_best_effort(str(f)) is None

    def test_elf_magic_delegates_to_elf_reader(self, tmp_path, monkeypatch) -> None:
        from abicheck import diff_cpp_patterns as mod

        f = tmp_path / "lib.so"
        f.write_bytes(b"\x7fELF" + b"\x00" * 60)
        monkeypatch.setattr(mod, "_read_elf_soname", lambda p: "libx.so.5")
        assert _read_soname_best_effort(str(f)) == "libx.so.5"


class TestReadElfSoname:
    def test_parse_error_returns_none(self, tmp_path, monkeypatch) -> None:
        # parse_elf_metadata raising → caught, return None (lines 1012-1013).
        from abicheck import elf_metadata

        f = tmp_path / "lib.so"
        f.write_bytes(b"\x7fELF" + b"\x00" * 4)

        def boom(_path):
            raise ValueError("bad elf")

        monkeypatch.setattr(elf_metadata, "parse_elf_metadata", boom)
        assert _read_elf_soname(str(f)) is None

    def test_returns_soname_from_metadata(self, tmp_path, monkeypatch) -> None:
        from abicheck import elf_metadata

        class _Meta:
            soname = "libz.so.1"

        f = tmp_path / "lib.so"
        f.write_bytes(b"\x7fELF")
        monkeypatch.setattr(elf_metadata, "parse_elf_metadata", lambda p: _Meta())
        assert _read_elf_soname(str(f)) == "libz.so.1"

    def test_metadata_none_returns_none(self, tmp_path, monkeypatch) -> None:
        from abicheck import elf_metadata

        f = tmp_path / "lib.so"
        f.write_bytes(b"\x7fELF")
        monkeypatch.setattr(elf_metadata, "parse_elf_metadata", lambda p: None)
        assert _read_elf_soname(str(f)) is None

    def test_metadata_empty_soname_returns_none(self, tmp_path, monkeypatch) -> None:
        from abicheck import elf_metadata

        class _Meta:
            soname = ""

        f = tmp_path / "lib.so"
        f.write_bytes(b"\x7fELF")
        monkeypatch.setattr(elf_metadata, "parse_elf_metadata", lambda p: _Meta())
        assert _read_elf_soname(str(f)) is None


# ---------------------------------------------------------------------------
# misc: soname major extraction (sanity, drives _extract_soname_major)
# ---------------------------------------------------------------------------


class TestSonameMajor:
    def test_dll_form(self) -> None:
        assert _extract_soname_major("libfoo-4.dll") == 4

    def test_dylib_form(self) -> None:
        assert _extract_soname_major("libfoo.7.dylib") == 7

    def test_none_when_no_suffix(self) -> None:
        assert _extract_soname_major("libfoo.so") is None
