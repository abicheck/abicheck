"""ADR-050 D1/D2 (G32 Phase A, slice 1) — the ExtractionContract fingerprint
algorithm and the check_contracts_comparable gate.

Scope: this module tests abicheck.comparability as pure functions. Neither
dumper.py wiring, the gate's integration into checker.compare/other entry
points, nor the legacy-CLI labeled --include grammar exist yet — see
abicheck/comparability.py's own module docstring for exactly what's
deferred. The numbered tests below map to the 16 dedicated tests ADR-050's
G32 plan (Phase A) requires; test 14's CLI-grammar-parsing sub-assertions
are not covered here since SidedIncludePathParam doesn't exist yet — its
semantic core (a labeled sibling support root) is fully covered via
comparability.IncludeDir(label=...) directly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from abicheck.comparability import (
    ComparabilityMismatch,
    IncludeDir,
    _header_sequence_is_additive_reorder_free,
    _include_sequence_is_additive_owned_growth,
    _scope_field_is_additive_superset,
    check_contracts_comparable,
    compute_extraction_contract,
)
from abicheck.elf_metadata import ElfMetadata
from abicheck.errors import ProfileMismatchError, ScopeMismatchError, SnapshotError
from abicheck.header_utils import resolve_inferred_header_roots
from abicheck.macho_metadata import MachoMetadata
from abicheck.model import AbiSnapshot, ExtractionContract
from abicheck.pe_metadata import PeMetadata


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _snap(contract: ExtractionContract | None, **kwargs) -> AbiSnapshot:
    return AbiSnapshot(library="libfoo.so", version="1.0", contract=contract, **kwargs)


# ---------------------------------------------------------------------------
# scope_fingerprint: root-relative header identity (tests 1, 2)
# ---------------------------------------------------------------------------


def test_1_identical_header_name_different_checkout_root_matches(tmp_path):
    old_h = _write(tmp_path / "v1" / "foo.h", "int add(int, int);\n")
    new_h = _write(tmp_path / "v2" / "foo.h", "int add(int, int);\n")
    old = compute_extraction_contract(declared_headers=[old_h])
    new = compute_extraction_contract(declared_headers=[new_h])
    assert old.scope_fingerprint == new.scope_fingerprint


def test_2_single_declared_header_rename_does_not_flip_scope_fingerprint(tmp_path):
    # Inverted by design (Codex review, PR #624 follow-up — the CI-red
    # incident once the gate went live on real dumps at scale): with only
    # one declared header per side, there is nothing to disambiguate a
    # name against, and renaming a project's single main header (v1.h ->
    # v2.h, or any other rename) is common, legitimate practice, not a
    # manifest/CLI-flag mistake. See test_2b below for the case that must
    # still mismatch: 2+ declared headers, where names are load-bearing.
    old_h = _write(tmp_path / "v1" / "foo.h", "int add(int, int);\n")
    new_h = _write(tmp_path / "v2" / "bar.h", "int add(int, int);\n")
    old = compute_extraction_contract(declared_headers=[old_h])
    new = compute_extraction_contract(declared_headers=[new_h])
    assert old.scope_fingerprint == new.scope_fingerprint


def test_2b_multi_header_set_still_distinguishes_different_names(tmp_path):
    # The multi-header case is untouched: two co-located declared headers
    # still need real per-file identity to disambiguate a genuine
    # declared-surface difference.
    a = _write(tmp_path / "v1" / "a.h", "int f(void);\n")
    b_old = _write(tmp_path / "v1" / "b.h", "int g(void);\n")
    b_new = _write(tmp_path / "v1" / "c.h", "int g(void);\n")
    old = compute_extraction_contract(declared_headers=[a, b_old])
    new = compute_extraction_contract(declared_headers=[a, b_new])
    assert old.scope_fingerprint != new.scope_fingerprint


def test_no_common_anchor_across_declared_paths_does_not_crash(tmp_path, monkeypatch):
    # CodeRabbit review (PR #624): os.path.commonpath raises ValueError when
    # its candidates share no common anchor at all (e.g. mixed drives on
    # Windows, or a local vs. UNC root) -- simulated here on any platform by
    # monkeypatching commonpath itself, since a real cross-drive fixture
    # isn't constructible on POSIX. This must degrade to a still-usable,
    # still-deterministic fingerprint, not propagate as an unhandled crash.
    real_commonpath = os.path.commonpath

    def _raising_commonpath(paths):
        raise ValueError("simulated: no common anchor (e.g. mixed drives)")

    monkeypatch.setattr(os.path, "commonpath", _raising_commonpath)
    h = _write(tmp_path / "v1" / "foo.h", "int add(int, int);\n")
    contract = compute_extraction_contract(declared_headers=[h])
    assert contract is not None
    assert contract.scope_fingerprint is not None

    monkeypatch.setattr(os.path, "commonpath", real_commonpath)
    contract_normal = compute_extraction_contract(declared_headers=[h])
    assert contract_normal.scope_fingerprint is not None


def test_no_common_anchor_fallback_still_distinguishes_different_headers(
    tmp_path, monkeypatch
):
    # Uses a 2-header declared set (not a single header -- see test_2b):
    # a single declared header's name is no longer load-bearing scope
    # identity (Codex review, PR #624 follow-up), so this fallback-path
    # robustness check needs the multi-header case to still exercise real
    # disambiguation.
    def _raising_commonpath(paths):
        raise ValueError("simulated: no common anchor")

    monkeypatch.setattr(os.path, "commonpath", _raising_commonpath)
    a = _write(tmp_path / "v1" / "a.h", "int f(void);\n")
    old_b = _write(tmp_path / "v1" / "foo.h", "int add(int, int);\n")
    new_b = _write(tmp_path / "v1" / "bar.h", "int add(int, int);\n")
    old = compute_extraction_contract(declared_headers=[a, old_b])
    new = compute_extraction_contract(declared_headers=[a, new_b])
    assert old.scope_fingerprint != new.scope_fingerprint


# ---------------------------------------------------------------------------
# profile_fingerprint: declared-header order (Codex review, PR #624)
# ---------------------------------------------------------------------------


def test_declared_header_order_differs_profile_fingerprint_when_l2_ran(tmp_path):
    # The aggregate driver TU dumper.py generates includes declared headers
    # sequentially in the caller's given order, so a macro/pragma side
    # effect from one header can change how a LATER header parses --
    # `-H a.h -H b.h` and `-H b.h -H a.h` can genuinely produce different
    # ASTs even though the same header SET is declared either way.
    # profile_fingerprint must catch that reordering.
    a = _write(tmp_path / "a.h", "int a(void);\n")
    b = _write(tmp_path / "b.h", "int b(void);\n")
    order_ab = compute_extraction_contract(
        declared_headers=[a, b], l2_frontend_ran=True
    )
    order_ba = compute_extraction_contract(
        declared_headers=[b, a], l2_frontend_ran=True
    )
    assert order_ab.profile_fingerprint != order_ba.profile_fingerprint


def test_declared_header_order_does_not_affect_scope_fingerprint(tmp_path):
    # scope_fingerprint stays order-independent -- the declared *surface*
    # (which headers are public) doesn't depend on dump order, only
    # profile_fingerprint (the extraction context) does.
    a = _write(tmp_path / "a.h", "int a(void);\n")
    b = _write(tmp_path / "b.h", "int b(void);\n")
    order_ab = compute_extraction_contract(
        declared_headers=[a, b], l2_frontend_ran=True
    )
    order_ba = compute_extraction_contract(
        declared_headers=[b, a], l2_frontend_ran=True
    )
    assert order_ab.scope_fingerprint == order_ba.scope_fingerprint


def test_declared_header_order_ignores_a_repeated_header(tmp_path):
    # A header named twice must not itself change header_sequence --
    # order-preserving de-duplication (first occurrence wins), mirroring
    # the same duplicate-collapse rule scope's "headers" field applies.
    a = _write(tmp_path / "a.h", "int a(void);\n")
    b = _write(tmp_path / "b.h", "int b(void);\n")
    once = compute_extraction_contract(declared_headers=[a, b], l2_frontend_ran=True)
    repeated = compute_extraction_contract(
        declared_headers=[a, b, a], l2_frontend_ran=True
    )
    assert once.profile_fingerprint == repeated.profile_fingerprint


def test_declared_header_order_irrelevant_without_l2_frontend(tmp_path):
    # No L2 frontend ran means no aggregate driver TU was ever generated,
    # so there is nothing for header order to have affected -- and
    # profile_fingerprint is None in this case regardless (l2_frontend_ran
    # gates profile_fingerprint's existence entirely).
    a = _write(tmp_path / "a.h", "int a(void);\n")
    b = _write(tmp_path / "b.h", "int b(void);\n")
    order_ab = compute_extraction_contract(declared_headers=[a, b])
    order_ba = compute_extraction_contract(declared_headers=[b, a])
    assert order_ab.profile_fingerprint is None
    assert order_ba.profile_fingerprint is None
    assert order_ab.scope_fingerprint == order_ba.scope_fingerprint


# ---------------------------------------------------------------------------
# profile_fingerprint: -I directory content hashing (tests 4, 5, 6)
# ---------------------------------------------------------------------------


def test_3_identical_out_of_checkout_dep_alongside_headers_matches_scope(tmp_path):
    old_h = _write(tmp_path / "v1" / "foo.h", "int add(int, int);\n")
    new_h = _write(tmp_path / "v2" / "foo.h", "int add(int, int);\n")
    dep = _write(tmp_path / "opt" / "dep" / "d.h", "int g(void);\n")
    old = compute_extraction_contract(
        declared_headers=[old_h],
        l2_frontend_ran=True,
        declared_includes=[
            IncludeDir(tmp_path / "v1"),
            IncludeDir(tmp_path / "opt" / "dep"),
        ],
        depfile_resolved_paths=[old_h, dep],
    )
    new = compute_extraction_contract(
        declared_headers=[new_h],
        l2_frontend_ran=True,
        declared_includes=[
            IncludeDir(tmp_path / "v2"),
            IncludeDir(tmp_path / "opt" / "dep"),
        ],
        depfile_resolved_paths=[new_h, dep],
    )
    assert old.scope_fingerprint == new.scope_fingerprint


def test_4_routine_two_checkout_dependency_matches_profile(tmp_path):
    dep_old = _write(tmp_path / "old" / "include" / "dep.h", "struct Dep { int x; };\n")
    dep_new = _write(tmp_path / "new" / "include" / "dep.h", "struct Dep { int x; };\n")
    old = compute_extraction_contract(
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "old" / "include")],
        depfile_resolved_paths=[dep_old],
    )
    new = compute_extraction_contract(
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "new" / "include")],
        depfile_resolved_paths=[dep_new],
    )
    assert old.profile_fingerprint == new.profile_fingerprint


def test_depfile_paths_are_deduplicated_before_external_slot_bucketing(tmp_path):
    # Codex review (PR #624): depfile_resolved_paths can realistically list
    # the same resolved file more than once (e.g. concatenated per-TU
    # depfiles, or an un-deduplicated depfile parse) -- an otherwise
    # identical extraction must not fingerprint differently just because
    # one side happens to repeat the same dependency entry.
    dep_old = _write(tmp_path / "old" / "include" / "dep.h", "struct Dep { int x; };\n")
    dep_new = _write(tmp_path / "new" / "include" / "dep.h", "struct Dep { int x; };\n")
    once = compute_extraction_contract(
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "old" / "include")],
        depfile_resolved_paths=[dep_old],
    )
    repeated = compute_extraction_contract(
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "new" / "include")],
        depfile_resolved_paths=[dep_new, dep_new],
    )
    assert once.profile_fingerprint == repeated.profile_fingerprint


def test_5_genuinely_different_dependency_content_differs_profile(tmp_path):
    dep_old = _write(
        tmp_path / "dep-v1" / "include" / "dep.h", "struct Dep { int x; };\n"
    )
    dep_new = _write(
        tmp_path / "dep-v2" / "include" / "dep.h", "struct Dep { int x; int y; };\n"
    )
    old = compute_extraction_contract(
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "dep-v1" / "include")],
        depfile_resolved_paths=[dep_old],
    )
    new = compute_extraction_contract(
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "dep-v2" / "include")],
        depfile_resolved_paths=[dep_new],
    )
    assert old.profile_fingerprint != new.profile_fingerprint


def test_6_project_include_plus_shared_external_dep_matches(tmp_path):
    old_h = _write(tmp_path / "work" / "v1" / "include" / "foo.h", "int f(void);\n")
    new_h = _write(tmp_path / "work" / "v2" / "include" / "foo.h", "int f(void);\n")
    dep = _write(tmp_path / "opt" / "dep" / "d.h", "int g(void);\n")
    old = compute_extraction_contract(
        declared_headers=[old_h],
        l2_frontend_ran=True,
        declared_includes=[
            IncludeDir(tmp_path / "work" / "v1" / "include"),
            IncludeDir(tmp_path / "opt" / "dep"),
        ],
        depfile_resolved_paths=[old_h, dep],
    )
    new = compute_extraction_contract(
        declared_headers=[new_h],
        l2_frontend_ran=True,
        declared_includes=[
            IncludeDir(tmp_path / "work" / "v2" / "include"),
            IncludeDir(tmp_path / "opt" / "dep"),
        ],
        depfile_resolved_paths=[new_h, dep],
    )
    assert old.profile_fingerprint == new.profile_fingerprint


def test_7_macro_only_header_never_owning_a_declaration_still_counted(tmp_path):
    # A header pulled in purely for macros (never itself declaration-bearing)
    # must still feed the digest -- otherwise a dependency-content diff
    # confined to it would silently pass the gate.
    dep_old = _write(tmp_path / "dep1" / "abi_config.h", "#define ABI_LAYOUT 1\n")
    dep_new = _write(tmp_path / "dep2" / "abi_config.h", "#define ABI_LAYOUT 2\n")
    old = compute_extraction_contract(
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "dep1")],
        depfile_resolved_paths=[dep_old],
    )
    new = compute_extraction_contract(
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "dep2")],
        depfile_resolved_paths=[dep_new],
    )
    assert old.profile_fingerprint != new.profile_fingerprint


# ---------------------------------------------------------------------------
# unambiguous encoding of joined fields (Codex review, PR #624): a raw
# "|"/":"/"," join across user-controlled strings can let two structurally
# different inputs collapse to the identical joined string, silently
# defeating the whole fingerprint.
# ---------------------------------------------------------------------------


def test_macro_ops_with_embedded_delimiters_does_not_collide(tmp_path):
    # macro_ops=[("D", "A|U:B")] (one -D flag whose value happens to contain
    # "|" and ":") must NOT fingerprint identically to
    # [("D", "A"), ("U", "B")] (two separate macro operations) -- a naive
    # "|".join(f"{op}:{val}") would collapse both to the literal string
    # "D:A|U:B".
    one_op = compute_extraction_contract(
        l2_frontend_ran=True, macro_ops=[("D", "A|U:B")]
    )
    two_ops = compute_extraction_contract(
        l2_frontend_ran=True, macro_ops=[("D", "A"), ("U", "B")]
    )
    assert one_op.profile_fingerprint != two_ops.profile_fingerprint


def test_pass_through_flag_order_differs_profile_fingerprint(tmp_path):
    # Codex review (PR #624): a repeatable pass-through frontend flag with
    # ABI-relevant preprocessing order (e.g. -include a.h -include b.h)
    # forces preprocessing content whose order can change macro/pragma
    # state -- "-include a.h -include b.h" and "-include b.h -include a.h"
    # must fingerprint differently, unlike the depfile buckets (which are
    # deliberately order-independent).
    order_ab = compute_extraction_contract(
        l2_frontend_ran=True, pass_through_flags=["-include", "a.h", "-include", "b.h"]
    )
    order_ba = compute_extraction_contract(
        l2_frontend_ran=True, pass_through_flags=["-include", "b.h", "-include", "a.h"]
    )
    assert order_ab.profile_fingerprint != order_ba.profile_fingerprint


def test_pass_through_flags_absent_on_both_sides_is_unaffected(tmp_path):
    old = compute_extraction_contract(l2_frontend_ran=True, compiler_family="gcc")
    new = compute_extraction_contract(l2_frontend_ran=True, compiler_family="gcc")
    assert old.profile_fingerprint == new.profile_fingerprint


def test_pass_through_flag_path_operand_ignores_checkout_root(tmp_path):
    # Codex review (PR #624): a path-valued pass-through operand (e.g. the
    # forced-include target of `-include /checkout-old/force.h`) must be
    # content-hashed, not hashed as its raw checkout-root-dependent
    # absolute string -- byte-identical forced-include content must
    # fingerprint identically regardless of which checkout it was
    # extracted from.
    old_force = _write(tmp_path / "old" / "force.h", "#define FORCED 1\n")
    new_force = _write(tmp_path / "new" / "force.h", "#define FORCED 1\n")
    old = compute_extraction_contract(
        l2_frontend_ran=True, pass_through_flags=["-include", old_force]
    )
    new = compute_extraction_contract(
        l2_frontend_ran=True, pass_through_flags=["-include", new_force]
    )
    assert old.profile_fingerprint == new.profile_fingerprint


def test_pass_through_flag_path_operand_content_change_differs(tmp_path):
    old_force = _write(tmp_path / "old" / "force.h", "#define FORCED 1\n")
    new_force = _write(tmp_path / "new" / "force.h", "#define FORCED 2\n")
    old = compute_extraction_contract(
        l2_frontend_ran=True, pass_through_flags=["-include", old_force]
    )
    new = compute_extraction_contract(
        l2_frontend_ran=True, pass_through_flags=["-include", new_force]
    )
    assert old.profile_fingerprint != new.profile_fingerprint


def test_pass_through_flag_str_and_path_do_not_collide(tmp_path):
    # A raw str element must not be indistinguishable from a Path element's
    # content hash -- the "str:"/"path:" tag prevents e.g. a literal flag
    # string that happens to equal some file's content hash from silently
    # matching a genuinely different Path-derived fingerprint.
    force = _write(tmp_path / "force.h", "#define FORCED 1\n")
    as_path = compute_extraction_contract(
        l2_frontend_ran=True, pass_through_flags=["-include", force]
    )
    as_str = compute_extraction_contract(
        l2_frontend_ran=True, pass_through_flags=["-include", str(force)]
    )
    assert as_path.profile_fingerprint != as_str.profile_fingerprint


def test_ancestor_slot_token_with_comma_in_header_name_does_not_collide(tmp_path):
    # One project-owned slot owning a single header literally named
    # "a.h,b.h" must not fingerprint identically to one project-owned slot
    # owning two separate headers "a.h" and "b.h" -- a naive
    # ",".join(sorted(identities)) collapses both to the literal string
    # "a.h,b.h" (verified: ",".join(["a.h,b.h"]) == ",".join(["a.h","b.h"])
    # == "a.h,b.h"), the same class of bug as macro_ops above, one level
    # deeper in the ancestor-derived slot token.
    one_header = _write(tmp_path / "inc1" / "a.h,b.h", "int f(void);\n")
    two_headers = [
        _write(tmp_path / "inc2" / "a.h", "int f(void);\n"),
        _write(tmp_path / "inc2" / "b.h", "int g(void);\n"),
    ]
    single = compute_extraction_contract(
        declared_headers=[one_header],
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "inc1")],
    )
    split = compute_extraction_contract(
        declared_headers=two_headers,
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "inc2")],
    )
    assert single.profile_fingerprint != split.profile_fingerprint


def test_ancestor_slot_token_deduplicates_a_repeated_declared_header(tmp_path):
    # Codex review (PR #624): declared_headers is not itself deduplicated
    # before reaching _slot_token_for_ancestor, so the same header supplied
    # twice in one CLI/manifest invocation (e.g. [a.h, b.h, a.h]) must not
    # retain a duplicate (identity, relative_path) pair in the owned slot
    # token -- a compare where only one side repeats the header would
    # otherwise spuriously raise ProfileMismatchError on a duplicate
    # declaration that changes nothing about the declared surface.
    a = _write(tmp_path / "include" / "a.h", "int a(void);\n")
    b = _write(tmp_path / "include" / "b.h", "int b(void);\n")
    once = compute_extraction_contract(
        declared_headers=[a, b],
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "include")],
    )
    repeated = compute_extraction_contract(
        declared_headers=[a, b, a],
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "include")],
    )
    assert once.profile_fingerprint == repeated.profile_fingerprint


# ---------------------------------------------------------------------------
# The routine real-world shape: same dir is both --header and --include
# (tests 8, 8b, 8c)
# ---------------------------------------------------------------------------


def test_8_ordinary_header_edit_does_not_flip_either_fingerprint(tmp_path):
    old_h = _write(tmp_path / "old" / "include" / "foo.h", "int add(int a, int b);\n")
    new_h = _write(
        tmp_path / "new" / "include" / "foo.h", "int add(int a, int b, int c);\n"
    )
    old = compute_extraction_contract(
        declared_headers=[old_h],
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "old" / "include")],
        depfile_resolved_paths=[old_h],
    )
    new = compute_extraction_contract(
        declared_headers=[new_h],
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "new" / "include")],
        depfile_resolved_paths=[new_h],
    )
    assert old.scope_fingerprint == new.scope_fingerprint
    assert old.profile_fingerprint == new.profile_fingerprint


def test_8b_unnamed_support_header_edit_does_not_flip_profile(tmp_path):
    old_h = _write(tmp_path / "old" / "include" / "foo.h", "int add(int a, int b);\n")
    new_h = _write(tmp_path / "new" / "include" / "foo.h", "int add(int a, int b);\n")
    old_detail = _write(
        tmp_path / "old" / "include" / "detail_v1.h", "int helper(void);\n"
    )
    new_detail = _write(
        tmp_path / "new" / "include" / "detail_v2.h", "int helper(void);\n"
    )
    old = compute_extraction_contract(
        declared_headers=[old_h],
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "old" / "include")],
        depfile_resolved_paths=[old_h, old_detail],
    )
    new = compute_extraction_contract(
        declared_headers=[new_h],
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "new" / "include")],
        depfile_resolved_paths=[new_h, new_detail],
    )
    assert old.profile_fingerprint == new.profile_fingerprint


def test_8c_no_include_flag_at_all_still_excludes_same_dir_support_header(tmp_path):
    old_h = _write(tmp_path / "old" / "include" / "foo.h", "int add(int a, int b);\n")
    new_h = _write(tmp_path / "new" / "include" / "foo.h", "int add(int a, int b);\n")
    old_detail = _write(
        tmp_path / "old" / "include" / "detail_v1.h", "int helper(void);\n"
    )
    new_detail = _write(
        tmp_path / "new" / "include" / "detail_v2.h", "int helper(void);\n"
    )
    old = compute_extraction_contract(
        declared_headers=[old_h],
        l2_frontend_ran=True,
        depfile_resolved_paths=[old_h, old_detail],
    )
    new = compute_extraction_contract(
        declared_headers=[new_h],
        l2_frontend_ran=True,
        depfile_resolved_paths=[new_h, new_detail],
    )
    assert old.profile_fingerprint == new.profile_fingerprint


def test_8d_implicit_parent_ownership_wins_over_a_nested_non_owned_include(tmp_path):
    # Codex review (PR #624): a file under a declared header's own parent
    # that ALSO falls under a nested, non-owned --include (e.g. --header
    # old/include/foo.h plus --include old/include/sub, with foo.h quote-
    # including sub/detail.h) must still be treated as implicitly
    # project-owned -- not attributed to the nested external slot and
    # content-hashed, which would make an ordinary internal support-header
    # edit spuriously raise ProfileMismatchError.
    old_h = _write(tmp_path / "old" / "include" / "foo.h", "int add(int a, int b);\n")
    new_h = _write(tmp_path / "new" / "include" / "foo.h", "int add(int a, int b);\n")
    old_detail = _write(
        tmp_path / "old" / "include" / "sub" / "detail.h", "int helper(void);\n"
    )
    new_detail = _write(
        tmp_path / "new" / "include" / "sub" / "detail.h",
        "int helper(void); /* internal-only edit */\n",
    )
    old = compute_extraction_contract(
        declared_headers=[old_h],
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "old" / "include" / "sub")],
        depfile_resolved_paths=[old_h, old_detail],
    )
    new = compute_extraction_contract(
        declared_headers=[new_h],
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "new" / "include" / "sub")],
        depfile_resolved_paths=[new_h, new_detail],
    )
    assert old.profile_fingerprint == new.profile_fingerprint


def test_8e_owned_ancestor_wins_over_a_nested_non_owned_include(tmp_path):
    # Codex review (PR #624): a file under a project-owned ANCESTOR -I
    # directory (e.g. --include old, owned because it's an ancestor of a
    # declared header) that ALSO falls under a nested, non-owned --include
    # (e.g. --include old/generated) must still be treated as project-owned
    # -- the owned ancestor's "every file under it, named or not" exclusion
    # wins over the nested dir's longer/deeper longest-prefix match. Unlike
    # test_8d, ownership here comes from an explicit --include ancestor, not
    # the implicit declared-header-parent rule.
    old_h = _write(tmp_path / "old" / "include" / "foo.h", "int add(int a, int b);\n")
    new_h = _write(tmp_path / "new" / "include" / "foo.h", "int add(int a, int b);\n")
    old_gen = _write(tmp_path / "old" / "generated" / "config.h", "int helper(void);\n")
    new_gen = _write(
        tmp_path / "new" / "generated" / "config.h",
        "int helper(void); /* internal-only edit */\n",
    )
    old = compute_extraction_contract(
        declared_headers=[old_h],
        l2_frontend_ran=True,
        declared_includes=[
            IncludeDir(tmp_path / "old"),
            IncludeDir(tmp_path / "old" / "generated"),
        ],
        depfile_resolved_paths=[old_h, old_gen],
    )
    new = compute_extraction_contract(
        declared_headers=[new_h],
        l2_frontend_ran=True,
        declared_includes=[
            IncludeDir(tmp_path / "new"),
            IncludeDir(tmp_path / "new" / "generated"),
        ],
        depfile_resolved_paths=[new_h, new_gen],
    )
    assert old.profile_fingerprint == new.profile_fingerprint


# ---------------------------------------------------------------------------
# system/toolchain bucket (tests 9, 10)
# ---------------------------------------------------------------------------


def test_9_10_unattributed_depfile_path_still_hashed_into_system_bucket(tmp_path):
    old_sys = _write(tmp_path / "sysroot_old" / "stdio.h", "// v1\n")
    new_sys = _write(tmp_path / "sysroot_new" / "stdio.h", "// v2 DIFFERENT\n")
    old = compute_extraction_contract(
        l2_frontend_ran=True, depfile_resolved_paths=[old_sys]
    )
    new = compute_extraction_contract(
        l2_frontend_ran=True, depfile_resolved_paths=[new_sys]
    )
    assert old.profile_fingerprint != new.profile_fingerprint


def test_depfile_paths_are_deduplicated_before_system_bucket_hashing(tmp_path):
    # Codex review (PR #624): the same dedup rule applies to the
    # unattributed system/toolchain bucket -- a repeated depfile entry must
    # not double-count its content hash.
    sysfile = _write(tmp_path / "sysroot" / "stddef.h", "// content\n")
    once = compute_extraction_contract(
        l2_frontend_ran=True, depfile_resolved_paths=[sysfile]
    )
    repeated = compute_extraction_contract(
        l2_frontend_ran=True, depfile_resolved_paths=[sysfile, sysfile]
    )
    assert once.profile_fingerprint == repeated.profile_fingerprint


def test_system_bucket_ignores_checkout_root_dependent_absolute_paths(tmp_path):
    # Codex review (PR #624): a system-bucket file (e.g. an auto-injected
    # sysroot/-isystem header not under any declared IncludeDir) has no
    # declared -I directory to make its path side-local against. Two
    # otherwise-identical toolchains whose system headers happen to be
    # materialized under different checkout/cache roots
    # (/tmp/old-sysroot/usr/include/stddef.h vs.
    # /tmp/new-sysroot/usr/include/stddef.h) must fingerprint identically --
    # only content, never the raw resolved path, may feed the digest.
    old_sys = _write(
        tmp_path / "old-sysroot" / "usr" / "include" / "stddef.h", "// v1\n"
    )
    new_sys = _write(
        tmp_path / "new-sysroot" / "usr" / "include" / "stddef.h", "// v1\n"
    )
    old = compute_extraction_contract(
        l2_frontend_ran=True, depfile_resolved_paths=[old_sys]
    )
    new = compute_extraction_contract(
        l2_frontend_ran=True, depfile_resolved_paths=[new_sys]
    )
    assert old.profile_fingerprint == new.profile_fingerprint


# ---------------------------------------------------------------------------
# generated driver TU exclusion (test 11)
# ---------------------------------------------------------------------------


def test_11_generated_driver_tu_excluded_from_bucketing(tmp_path):
    # The driver files live OUTSIDE any project-owned directory (not under
    # old_h's/new_h's parent, and no matching --include for their own
    # directory either) so they would otherwise land in the unordered
    # system/toolchain bucket, not get excluded by project-ownership alone
    # (CodeRabbit review, PR #624: the original version of this test placed
    # the driver file inside the already-project-owned header directory, so
    # the assertion passed even without the generated_driver_path exclusion
    # -- it tested nothing). Their content genuinely differs (embedding the
    # side-specific absolute #include path dumper.py's real driver would
    # write), so the assertion only holds if generated_driver_path exclusion
    # actually drops them before system-bucket hashing.
    old_h = _write(tmp_path / "old" / "foo.h", "int f(void);\n")
    new_h = _write(tmp_path / "new" / "foo.h", "int f(void);\n")
    driver_old = _write(
        tmp_path / "driver_old" / "__driver__.cpp", '#include "/abs/old/foo.h"\n'
    )
    driver_new = _write(
        tmp_path / "driver_new" / "__driver__.cpp", '#include "/abs/new/foo.h"\n'
    )
    old = compute_extraction_contract(
        declared_headers=[old_h],
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "old")],
        depfile_resolved_paths=[driver_old, old_h],
        generated_driver_path=driver_old,
    )
    new = compute_extraction_contract(
        declared_headers=[new_h],
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "new")],
        depfile_resolved_paths=[driver_new, new_h],
        generated_driver_path=driver_new,
    )
    assert old.profile_fingerprint == new.profile_fingerprint


def test_11b_without_exclusion_the_driver_tu_would_have_differed(tmp_path):
    # Companion negative check proving test_11 is load-bearing: the same
    # driver files, NOT passed as generated_driver_path, land in the system
    # bucket and their genuinely different content flips profile_fingerprint
    # -- confirming the match in test_11 comes from the exclusion, not from
    # some other reason both sides happened to agree.
    old_h = _write(tmp_path / "old" / "foo.h", "int f(void);\n")
    new_h = _write(tmp_path / "new" / "foo.h", "int f(void);\n")
    driver_old = _write(
        tmp_path / "driver_old" / "__driver__.cpp", '#include "/abs/old/foo.h"\n'
    )
    driver_new = _write(
        tmp_path / "driver_new" / "__driver__.cpp", '#include "/abs/new/foo.h"\n'
    )
    old = compute_extraction_contract(
        declared_headers=[old_h],
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "old")],
        depfile_resolved_paths=[driver_old, old_h],
    )
    new = compute_extraction_contract(
        declared_headers=[new_h],
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "new")],
        depfile_resolved_paths=[driver_new, new_h],
    )
    assert old.profile_fingerprint != new.profile_fingerprint


# ---------------------------------------------------------------------------
# per-slot positional tokens preserve -I order (tests 12, 13)
# ---------------------------------------------------------------------------


def test_12_project_owned_and_external_slot_swap_differs(tmp_path):
    work = _write(tmp_path / "work" / "foo.h", "int f(void);\n")
    dep = _write(tmp_path / "dep" / "dep.h", "int g(void);\n")
    order_a = compute_extraction_contract(
        declared_headers=[work],
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "work"), IncludeDir(tmp_path / "dep")],
        depfile_resolved_paths=[work, dep],
    )
    order_b = compute_extraction_contract(
        declared_headers=[work],
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(tmp_path / "dep"), IncludeDir(tmp_path / "work")],
        depfile_resolved_paths=[work, dep],
    )
    assert order_a.profile_fingerprint != order_b.profile_fingerprint


def test_13_two_project_owned_slots_swapped_order_differs(tmp_path):
    foo = _write(tmp_path / "include" / "foo.h", "int f(void);\n")
    bar = _write(tmp_path / "generated" / "bar.h", "int g(void);\n")
    order_a = compute_extraction_contract(
        declared_headers=[foo, bar],
        l2_frontend_ran=True,
        declared_includes=[
            IncludeDir(tmp_path / "include"),
            IncludeDir(tmp_path / "generated"),
        ],
        depfile_resolved_paths=[foo, bar],
    )
    order_b = compute_extraction_contract(
        declared_headers=[foo, bar],
        l2_frontend_ran=True,
        declared_includes=[
            IncludeDir(tmp_path / "generated"),
            IncludeDir(tmp_path / "include"),
        ],
        depfile_resolved_paths=[foo, bar],
    )
    assert order_a.profile_fingerprint != order_b.profile_fingerprint


def test_13b_two_project_owned_slots_with_same_basename_swapped_order_differs(
    tmp_path,
):
    # Codex review (PR #624): two project-owned roots each owning a
    # DIFFERENTLY-LOCATED declared header that happens to share a basename
    # (include/foo.h vs generated/foo.h) must still tokenize distinctly --
    # a basename-only token would collapse both to "hdrs:foo.h" and lose the
    # order-sensitivity test_13 above already covers for distinctly-named
    # headers.
    foo_inc = _write(tmp_path / "include" / "foo.h", "int f(void);\n")
    foo_gen = _write(tmp_path / "generated" / "foo.h", "int g(void);\n")
    order_a = compute_extraction_contract(
        declared_headers=[foo_inc, foo_gen],
        l2_frontend_ran=True,
        declared_includes=[
            IncludeDir(tmp_path / "include"),
            IncludeDir(tmp_path / "generated"),
        ],
        depfile_resolved_paths=[foo_inc, foo_gen],
    )
    order_b = compute_extraction_contract(
        declared_headers=[foo_inc, foo_gen],
        l2_frontend_ran=True,
        declared_includes=[
            IncludeDir(tmp_path / "generated"),
            IncludeDir(tmp_path / "include"),
        ],
        depfile_resolved_paths=[foo_inc, foo_gen],
    )
    assert order_a.profile_fingerprint != order_b.profile_fingerprint


def test_13c_nested_project_owned_slots_owning_same_header_swapped_order_differs(
    tmp_path,
):
    # Codex review (PR #624): two NESTED/overlapping project-owned roots
    # (-I work and -I work/include) that both own the exact SAME declared
    # header must still tokenize distinctly per slot. Unlike test_13b's
    # separate-roots case, the GLOBAL root-relative header identity alone is
    # identical here regardless of which of the two dirs owns it (there's
    # only one header, one identity) -- so before this fix, swapping
    # declared_includes order produced the identical include_sequence either
    # way, silently losing order-sensitivity for exactly the kind of nested
    # -I setup where order genuinely changes #include resolution.
    foo = _write(tmp_path / "work" / "include" / "foo.h", "int f(void);\n")
    order_a = compute_extraction_contract(
        declared_headers=[foo],
        l2_frontend_ran=True,
        declared_includes=[
            IncludeDir(tmp_path / "work"),
            IncludeDir(tmp_path / "work" / "include"),
        ],
        depfile_resolved_paths=[foo],
    )
    order_b = compute_extraction_contract(
        declared_headers=[foo],
        l2_frontend_ran=True,
        declared_includes=[
            IncludeDir(tmp_path / "work" / "include"),
            IncludeDir(tmp_path / "work"),
        ],
        depfile_resolved_paths=[foo],
    )
    assert order_a.profile_fingerprint != order_b.profile_fingerprint


def test_13d_single_owned_slot_for_single_header_rename_does_not_flip_profile(
    tmp_path,
):
    # Real CI incident (PR #624 follow-up, examples/case189 and every other
    # single-`-H` example case): P3's auto-added include root
    # (`resolve_inferred_header_roots`, cli_dump_helpers.py) makes a lone
    # declared header's own parent directory an owned `-I` slot even with no
    # explicit --include at all. Before this fix, that owned slot's token
    # embedded the header's own basename via `_slot_token_for_ancestor`'s
    # dir-relative-path component -- so a legitimate single-header rename
    # (v1.h -> v2.h) flipped `include_sequence` and therefore
    # `profile_fingerprint`, even though `header_sequence` and scope's
    # "headers" field both already correctly collapsed to
    # "<single-header>". Distinct from test_13c: there the SAME header is
    # owned by two NESTED slots (order-sensitivity must survive), whereas
    # here there is exactly one owned slot for one header -- nothing to
    # disambiguate, so the header's name must not be load-bearing.
    old_header = _write(tmp_path / "old" / "v1.h", "int f(void);\n")
    new_header = _write(tmp_path / "new" / "v2.h", "int f(void);\n")
    old = compute_extraction_contract(
        declared_headers=[old_header],
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(old_header.parent)],
        depfile_resolved_paths=[old_header],
    )
    new = compute_extraction_contract(
        declared_headers=[new_header],
        l2_frontend_ran=True,
        declared_includes=[IncludeDir(new_header.parent)],
        depfile_resolved_paths=[new_header],
    )
    assert old.profile_fields["include_sequence"] == '["0:hdrs:<single-header>"]'
    assert old.profile_fingerprint == new.profile_fingerprint


# ---------------------------------------------------------------------------
# labeled sibling support root (test 14 -- semantic core only; the CLI
# grammar itself is not implemented yet, see this module's own docstring)
# ---------------------------------------------------------------------------


def test_14_labeled_sibling_support_root_edit_does_not_flip_profile(tmp_path):
    old_inc = _write(tmp_path / "old" / "include" / "foo.h", "int f(void);\n")
    new_inc = _write(tmp_path / "new" / "include" / "foo.h", "int f(void);\n")
    old_priv = _write(tmp_path / "old" / "src" / "priv.h", "int helper(void);\n")
    new_priv = _write(tmp_path / "new" / "src" / "priv.h", "int helper_v2(void);\n")
    old = compute_extraction_contract(
        declared_headers=[old_inc],
        l2_frontend_ran=True,
        declared_includes=[
            IncludeDir(tmp_path / "old" / "include"),
            IncludeDir(tmp_path / "old" / "src", label="support"),
        ],
        depfile_resolved_paths=[old_inc, old_priv],
    )
    new = compute_extraction_contract(
        declared_headers=[new_inc],
        l2_frontend_ran=True,
        declared_includes=[
            IncludeDir(tmp_path / "new" / "include"),
            IncludeDir(tmp_path / "new" / "src", label="support"),
        ],
        depfile_resolved_paths=[new_inc, new_priv],
    )
    assert old.profile_fingerprint == new.profile_fingerprint


def test_14b_labeled_root_swapped_order_against_unrelated_external_differs(tmp_path):
    support = _write(tmp_path / "src" / "priv.h", "int helper(void);\n")
    dep = _write(tmp_path / "dep" / "d.h", "int g(void);\n")
    order_a = compute_extraction_contract(
        l2_frontend_ran=True,
        declared_includes=[
            IncludeDir(tmp_path / "src", label="support"),
            IncludeDir(tmp_path / "dep"),
        ],
        depfile_resolved_paths=[support, dep],
    )
    order_b = compute_extraction_contract(
        l2_frontend_ran=True,
        declared_includes=[
            IncludeDir(tmp_path / "dep"),
            IncludeDir(tmp_path / "src", label="support"),
        ],
        depfile_resolved_paths=[support, dep],
    )
    assert order_a.profile_fingerprint != order_b.profile_fingerprint


# ---------------------------------------------------------------------------
# no-inputs / symbols-only rules
# ---------------------------------------------------------------------------


def test_no_inputs_at_all_returns_no_contract():
    assert compute_extraction_contract() is None


def test_symbols_only_no_l2_frontend_has_no_profile_fingerprint(tmp_path):
    contract = compute_extraction_contract(
        public_header_paths=[tmp_path / "include" / "foo.h"]
    )
    assert contract is not None
    assert contract.profile_fingerprint is None
    assert contract.scope_fingerprint is not None


def test_symbols_only_with_no_provenance_returns_no_contract():
    assert compute_extraction_contract(l2_frontend_ran=False) is None


def test_l2_shaped_kwargs_without_l2_frontend_ran_still_returns_no_contract(tmp_path):
    # Codex review (PR #624): passing L2-shaped keyword arguments
    # (declared_includes, macro_ops, compiler_family) without also setting
    # l2_frontend_ran=True (no L2 invocation actually ran, and no scope
    # inputs either) must not produce a non-None "empty shell"
    # ExtractionContract whose profile_fingerprint AND scope_fingerprint are
    # both None -- that would misreport as real contract coverage to
    # checker.compare's contract_coverage logic.
    dep = _write(tmp_path / "dep" / "d.h", "int g(void);\n")
    contract = compute_extraction_contract(
        l2_frontend_ran=False,
        compiler_family="gcc",
        declared_includes=[IncludeDir(tmp_path / "dep")],
        depfile_resolved_paths=[dep],
        macro_ops=[("D", "FOO=1")],
    )
    assert contract is None


def test_symbols_only_public_header_paths_are_root_relative_not_absolute(tmp_path):
    # Codex review (PR #624): a symbols-only dump's public_header_paths must
    # normalize the same root-relative way declared_headers does, or an
    # ordinary two-checkout compare relying only on --public-header
    # provenance would spuriously ScopeMismatchError on checkout-root paths
    # alone.
    old_contract = compute_extraction_contract(
        public_header_paths=[tmp_path / "checkout-old" / "include" / "foo.h"]
    )
    new_contract = compute_extraction_contract(
        public_header_paths=[tmp_path / "checkout-new" / "include" / "foo.h"]
    )
    assert old_contract.scope_fingerprint == new_contract.scope_fingerprint


def test_symbols_only_public_header_dirs_are_root_relative_not_absolute(tmp_path):
    old_contract = compute_extraction_contract(
        public_header_dirs=[tmp_path / "checkout-old" / "api" / "include"]
    )
    new_contract = compute_extraction_contract(
        public_header_dirs=[tmp_path / "checkout-new" / "api" / "include"]
    )
    assert old_contract.scope_fingerprint == new_contract.scope_fingerprint


def test_single_public_header_dir_rename_does_not_flip_scope_fingerprint(tmp_path):
    # Real CI incident (PR #624 follow-up, test_perf_binary_scan.py's
    # `test_headers_depth_matrix_args_stays_l2_only_and_fast`): a lone
    # `-H old=<dir>`/`-H new=<dir>` umbrella directory feeds its OWN name
    # into `public_header_dirs`, not just a checkout-root prefix -- unlike
    # test_symbols_only_public_header_dirs_are_root_relative_not_absolute
    # above (same final "include" component both sides), here the
    # directory's own basename genuinely differs ("old-include" vs
    # "new-include", the exact fixture names that CI test uses). With only
    # one directory declared there is nothing to disambiguate a name
    # against, same reasoning as a single declared header's own filename.
    old_contract = compute_extraction_contract(
        public_header_dirs=[tmp_path / "old-include"]
    )
    new_contract = compute_extraction_contract(
        public_header_dirs=[tmp_path / "new-include"]
    )
    assert old_contract.scope_fields["public_header_dirs"] == '["<single-header-dir>"]'
    assert old_contract.scope_fingerprint == new_contract.scope_fingerprint


def test_two_public_header_dirs_with_different_names_still_distinguishes(tmp_path):
    # The multi-directory case still needs real per-directory identity
    # (Codex review, PR #624 follow-up, symmetric with test_2b's multi-header
    # case): two co-located declared public-header dirs must not collapse to
    # the same token -- ["a", "b"] vs. ["a", "c"] is a genuine
    # declared-surface difference the single-entry collapse above must not
    # hide.
    a = tmp_path / "old" / "a"
    a.mkdir(parents=True)
    old_b = tmp_path / "old" / "b"
    old_b.mkdir()
    new_c = tmp_path / "new" / "c"
    new_c.mkdir(parents=True)
    old_contract = compute_extraction_contract(public_header_dirs=[a, old_b])
    new_contract = compute_extraction_contract(public_header_dirs=[a, new_c])
    assert old_contract.scope_fingerprint != new_contract.scope_fingerprint


def test_public_header_dir_shallower_than_declared_headers_does_not_leak_its_name(
    tmp_path,
):
    # Real CI incident: a `--devel-pkg`/`-H <dir>` umbrella extracted to a
    # per-run-random temp root (e.g. dpkg -x'd into
    # /tmp/abicheck_dev_XXXXXX), with the root itself passed as a lone
    # public_header_dir *alongside* declared_headers discovered several
    # directories below it (e.g. <root>/usr/include/zlib.h,
    # <root>/usr/share/doc/.../gzlog.h -- exactly zlib1g-dev's real layout).
    # "headers" and "public_header_dirs" must normalize against SEPARATE
    # roots: sharing one (computed from every entry's parent, including the
    # directory) pulls the shared root up to the directory's *own* parent
    # the moment the directory sits shallower than the header files,
    # leaking that root's random name into "headers"' identities even
    # though "public_header_dirs" collapses its own single-entry case away
    # separately. Two independent extractions of byte-identical headers
    # into two differently-named roots must still fingerprint identically.
    old_root = tmp_path / "abicheck_dev_oldrandom"
    new_root = tmp_path / "abicheck_dev_newrandom"
    old_headers = [
        _write(old_root / "usr" / "include" / "zlib.h", "int zlib_api(void);"),
        _write(
            old_root / "usr" / "share" / "doc" / "examples" / "gzlog.h",
            "int gzlog_example(void);",
        ),
    ]
    new_headers = [
        _write(new_root / "usr" / "include" / "zlib.h", "int zlib_api(void);"),
        _write(
            new_root / "usr" / "share" / "doc" / "examples" / "gzlog.h",
            "int gzlog_example(void);",
        ),
    ]
    old_contract = compute_extraction_contract(
        declared_headers=old_headers, public_header_dirs=[old_root]
    )
    new_contract = compute_extraction_contract(
        declared_headers=new_headers, public_header_dirs=[new_root]
    )
    assert old_contract.scope_fields["headers"] == new_contract.scope_fields["headers"]
    # Built via Path, not hard-coded "/" literals: _side_local_identity
    # stringifies relative_to()'s result, which joins with the platform's
    # own separator (backslash on Windows).
    expected_headers = sorted(
        [
            str(Path("include", "zlib.h")),
            str(Path("share", "doc", "examples", "gzlog.h")),
        ]
    )
    assert old_contract.scope_fields["headers"] == json.dumps(expected_headers)
    assert old_contract.scope_fingerprint == new_contract.scope_fingerprint


def test_declared_headers_and_public_header_paths_share_one_scope_identity(tmp_path):
    # Codex review (PR #624): the same logical header captured via a full L2
    # dump's declared_headers on one side and a symbols-only dump's
    # public_header_paths provenance on the other (an ordinary depth
    # difference, e.g. comparing `scan --depth binary` against a fuller
    # stored baseline) must not scope-mismatch just because the two
    # mechanisms feed different keyword arguments.
    h_old = _write(tmp_path / "old" / "include" / "foo.h", "int f(void);\n")
    h_new = _write(tmp_path / "new" / "include" / "foo.h", "int f(void);\n")
    full_l2 = compute_extraction_contract(declared_headers=[h_old])
    symbols_only = compute_extraction_contract(public_header_paths=[h_new])
    assert full_l2.scope_fingerprint == symbols_only.scope_fingerprint


def test_merged_headers_field_deduplicates_the_same_header_named_twice(tmp_path):
    # Codex review (PR #624): a side that names the same logical header
    # through BOTH declared_headers and public_header_paths (a full L2 dump
    # that also passes --public-header for that same file, a real CLI
    # combination) must fingerprint identically to a side naming it only
    # once -- without deduplication the first side's merged "headers" list
    # would retain a duplicate entry ["foo.h", "foo.h"], mismatching a
    # single-entry ["foo.h"] side purely on element count.
    h = _write(tmp_path / "include" / "foo.h", "int f(void);\n")
    named_twice = compute_extraction_contract(
        declared_headers=[h], public_header_paths=[h]
    )
    named_once = compute_extraction_contract(declared_headers=[h])
    assert named_twice.scope_fingerprint == named_once.scope_fingerprint


# ---------------------------------------------------------------------------
# unreadable header content fails extraction outright (no silent sentinel)
# ---------------------------------------------------------------------------


def test_unreadable_header_content_raises_snapshot_error(tmp_path):
    missing = tmp_path / "dep" / "gone.h"
    missing.parent.mkdir(parents=True)
    with pytest.raises(SnapshotError):
        compute_extraction_contract(
            l2_frontend_ran=True,
            declared_includes=[IncludeDir(tmp_path / "dep")],
            depfile_resolved_paths=[missing],
        )


# ---------------------------------------------------------------------------
# check_contracts_comparable: the gate itself
# ---------------------------------------------------------------------------


def test_gate_raises_scope_mismatch_error_on_scope_drift(tmp_path):
    # A single declared header's own name is no longer load-bearing scope
    # identity (Codex review, PR #624 follow-up), so this needs a genuine
    # multi-header declared-surface difference to still trigger the gate.
    a = _write(tmp_path / "v1" / "a.h", "int g(void);\n")
    old_h = _write(tmp_path / "v1" / "foo.h", "int f(void);\n")
    new_h = _write(tmp_path / "v2" / "bar.h", "int f(void);\n")
    old = _snap(compute_extraction_contract(declared_headers=[a, old_h]))
    new = _snap(compute_extraction_contract(declared_headers=[a, new_h]))
    with pytest.raises(ScopeMismatchError):
        check_contracts_comparable(old, new)


def test_gate_additive_header_set_carve_out_allows_pure_addition(tmp_path):
    # PR #641 follow-up (pvxs full-version-matrix scan, F8): master added
    # exactly one new public header (include/pvxs/json.h) with nothing else
    # added/removed/renamed -- ordinary evolution, not the "manifest/
    # CLI-flag drift between two extraction runs" mistake this fingerprint
    # exists to catch.
    a = _write(tmp_path / "v1" / "a.h", "int f(void);\n")
    b = _write(tmp_path / "v1" / "b.h", "int g(void);\n")
    a2 = _write(tmp_path / "v2" / "a.h", "int f(void);\n")
    b2 = _write(tmp_path / "v2" / "b.h", "int g(void);\n")
    json_h = _write(tmp_path / "v2" / "json.h", "int h(void);\n")
    old = _snap(compute_extraction_contract(declared_headers=[a, b]))
    new = _snap(compute_extraction_contract(declared_headers=[a2, b2, json_h]))
    assert old.contract.scope_fingerprint != new.contract.scope_fingerprint
    check_contracts_comparable(old, new)  # must not raise


def test_gate_additive_header_set_carve_out_still_raises_when_a_header_is_also_removed(
    tmp_path,
):
    # A header removed alongside one added is NOT a pure addition -- must
    # still raise, exactly as it did before this carve-out existed.
    a = _write(tmp_path / "v1" / "a.h", "int f(void);\n")
    b = _write(tmp_path / "v1" / "b.h", "int g(void);\n")
    a2 = _write(tmp_path / "v2" / "a.h", "int f(void);\n")
    c2 = _write(tmp_path / "v2" / "c.h", "int h(void);\n")
    old = _snap(compute_extraction_contract(declared_headers=[a, b]))
    new = _snap(compute_extraction_contract(declared_headers=[a2, c2]))
    with pytest.raises(ScopeMismatchError):
        check_contracts_comparable(old, new)


def test_gate_additive_header_set_carve_out_still_raises_on_pure_removal(tmp_path):
    a = _write(tmp_path / "v1" / "a.h", "int f(void);\n")
    b = _write(tmp_path / "v1" / "b.h", "int g(void);\n")
    c = _write(tmp_path / "v1" / "c.h", "int h(void);\n")
    a2 = _write(tmp_path / "v2" / "a.h", "int f(void);\n")
    b2 = _write(tmp_path / "v2" / "b.h", "int g(void);\n")
    old = _snap(compute_extraction_contract(declared_headers=[a, b, c]))
    new = _snap(compute_extraction_contract(declared_headers=[a2, b2]))
    with pytest.raises(ScopeMismatchError):
        check_contracts_comparable(old, new)


def test_gate_additive_header_set_carve_out_declines_when_a_side_is_single_header_sentinel(
    tmp_path,
):
    # A lone declared header collapses to a "<single-header>" sentinel with
    # no real per-file identity (Codex review, PR #624 follow-up) -- there
    # is nothing to verify a true superset against, so the carve-out must
    # decline and the gate must still raise, exactly as it did before this
    # carve-out existed.
    a = _write(tmp_path / "v1" / "a.h", "int f(void);\n")
    a2 = _write(tmp_path / "v2" / "a.h", "int f(void);\n")
    b2 = _write(tmp_path / "v2" / "b.h", "int g(void);\n")
    old = _snap(compute_extraction_contract(declared_headers=[a]))
    new = _snap(compute_extraction_contract(declared_headers=[a2, b2]))
    with pytest.raises(ScopeMismatchError):
        check_contracts_comparable(old, new)


def test_gate_additive_header_set_carve_out_covers_public_header_dirs_growth(tmp_path):
    a = _write(tmp_path / "v1" / "a.h", "int f(void);\n")
    b = _write(tmp_path / "v1" / "b.h", "int g(void);\n")
    a2 = _write(tmp_path / "v2" / "a.h", "int f(void);\n")
    b2 = _write(tmp_path / "v2" / "b.h", "int g(void);\n")
    dir1 = tmp_path / "v1" / "dir1"
    dir2 = tmp_path / "v1" / "dir2"
    for d in (dir1, dir2):
        d.mkdir(parents=True)
    dir1b = tmp_path / "v2" / "dir1"
    dir2b = tmp_path / "v2" / "dir2"
    dir3b = tmp_path / "v2" / "dir3"
    for d in (dir1b, dir2b, dir3b):
        d.mkdir(parents=True)
    old = _snap(
        compute_extraction_contract(declared_headers=[a, b], public_header_dirs=[dir1, dir2])
    )
    new = _snap(
        compute_extraction_contract(
            declared_headers=[a2, b2], public_header_dirs=[dir1b, dir2b, dir3b]
        )
    )
    check_contracts_comparable(old, new)  # must not raise


def test_gate_additive_header_set_carve_out_requires_every_differing_field_to_grow(
    tmp_path,
):
    # headers grows (a,b -> a,b,c) but public_header_dirs SHRINKS
    # (dir1,dir2,dir3 -> dir1,dir2) at the same time -- not a pure addition
    # overall, so the gate must still raise even though the headers field
    # alone would have qualified.
    a = _write(tmp_path / "v1" / "a.h", "int f(void);\n")
    b = _write(tmp_path / "v1" / "b.h", "int g(void);\n")
    a2 = _write(tmp_path / "v2" / "a.h", "int f(void);\n")
    b2 = _write(tmp_path / "v2" / "b.h", "int g(void);\n")
    c2 = _write(tmp_path / "v2" / "c.h", "int h(void);\n")
    dir1 = tmp_path / "v1" / "dir1"
    dir2 = tmp_path / "v1" / "dir2"
    dir3 = tmp_path / "v1" / "dir3"
    for d in (dir1, dir2, dir3):
        d.mkdir(parents=True)
    dir1b = tmp_path / "v2" / "dir1"
    dir2b = tmp_path / "v2" / "dir2"
    for d in (dir1b, dir2b):
        d.mkdir(parents=True)
    old = _snap(
        compute_extraction_contract(
            declared_headers=[a, b], public_header_dirs=[dir1, dir2, dir3]
        )
    )
    new = _snap(
        compute_extraction_contract(
            declared_headers=[a2, b2, c2], public_header_dirs=[dir1b, dir2b]
        )
    )
    with pytest.raises(ScopeMismatchError):
        check_contracts_comparable(old, new)


def test_gate_additive_header_set_carve_out_applies_in_diagnostic_mode_too(tmp_path):
    a = _write(tmp_path / "v1" / "a.h", "int f(void);\n")
    b = _write(tmp_path / "v1" / "b.h", "int g(void);\n")
    a2 = _write(tmp_path / "v2" / "a.h", "int f(void);\n")
    b2 = _write(tmp_path / "v2" / "b.h", "int g(void);\n")
    c2 = _write(tmp_path / "v2" / "c.h", "int h(void);\n")
    old = _snap(compute_extraction_contract(declared_headers=[a, b]))
    new = _snap(compute_extraction_contract(declared_headers=[a2, b2, c2]))
    assert check_contracts_comparable(old, new, diagnostic=True) is None


def test_gate_additive_header_set_carve_out_still_checks_profile_afterward(tmp_path):
    # Codex review (PR #641 follow-up): waiving an additive scope mismatch
    # must fall through to the profile check, not skip it entirely -- a
    # release that both adds a header AND changes an unrelated,
    # uncorroborated extraction-profile field (here: compiler_family, not
    # covered by any profile carve-out) must still raise
    # ProfileMismatchError, not be silently treated as fully comparable.
    a = _write(tmp_path / "v1" / "a.h", "int f(void);\n")
    b = _write(tmp_path / "v1" / "b.h", "int g(void);\n")
    a2 = _write(tmp_path / "v2" / "a.h", "int f(void);\n")
    b2 = _write(tmp_path / "v2" / "b.h", "int g(void);\n")
    c2 = _write(tmp_path / "v2" / "c.h", "int h(void);\n")
    old = _snap(
        compute_extraction_contract(
            declared_headers=[a, b], l2_frontend_ran=True, compiler_family="gcc"
        )
    )
    new = _snap(
        compute_extraction_contract(
            declared_headers=[a2, b2, c2], l2_frontend_ran=True, compiler_family="clang"
        )
    )
    assert old.contract.scope_fingerprint != new.contract.scope_fingerprint
    assert old.contract.profile_fingerprint != new.contract.profile_fingerprint
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_header_sequence_carve_out_makes_f8_scenario_fully_comparable(tmp_path):
    # Codex review (PR #641 follow-up, second round): compute_extraction_contract
    # tracks declared-header ORDER in profile_fields["header_sequence"] as a
    # genuine extraction-context fact distinct from scope_fingerprint's
    # order-independent declared SET -- so the exact real pvxs F8 scenario
    # (a pure header addition) unavoidably changes header_sequence too, and
    # with only the scope-side carve-out, check_contracts_comparable still
    # raised ProfileMismatchError on the identical "pure addition" case it
    # had just been fixed to accept. This is the full real-world scenario
    # end-to-end: both the scope AND profile fingerprints differ, and the
    # pair must still be fully comparable (no exception of either kind).
    a = _write(tmp_path / "v1" / "a.h", "int f(void);\n")
    b = _write(tmp_path / "v1" / "b.h", "int g(void);\n")
    a2 = _write(tmp_path / "v2" / "a.h", "int f(void);\n")
    b2 = _write(tmp_path / "v2" / "b.h", "int g(void);\n")
    c2 = _write(tmp_path / "v2" / "c.h", "int h(void);\n")
    old = _snap(compute_extraction_contract(declared_headers=[a, b], l2_frontend_ran=True))
    new = _snap(
        compute_extraction_contract(declared_headers=[a2, b2, c2], l2_frontend_ran=True)
    )
    assert old.contract.scope_fingerprint != new.contract.scope_fingerprint
    assert old.contract.profile_fingerprint != new.contract.profile_fingerprint
    assert old.contract.profile_fields["header_sequence"] != new.contract.profile_fields[
        "header_sequence"
    ]
    assert check_contracts_comparable(old, new) is None  # must not raise either error


def test_gate_additive_header_set_carve_out_ignores_unchanged_single_dir_sentinel(
    tmp_path,
):
    # Codex review (PR #641 follow-up, third round): the real F8 CLI shape
    # is `-H old=<dir> -H new=<dir>` -- a single public_header_dir per side
    # -- so BOTH sides collapse to the identical "<single-header-dir>"
    # sentinel even though old/new point at different physical directories.
    # The scope carve-out's `all(...)` checks every SCOPE_FIELD_KEYS field,
    # not just the ones that actually differ (unlike the profile side,
    # which pre-filters to a `differing` set) -- so this unchanged,
    # sentinel-shaped field was wrongly declining the whole carve-out before
    # it ever reached the genuinely differing "headers" field. Direct repro
    # confirmed this raised ScopeMismatchError before the fix.
    old_dir = tmp_path / "old" / "include"
    new_dir = tmp_path / "new" / "include"
    a1 = _write(old_dir / "a.h", "int f(void);\n")
    b1 = _write(old_dir / "b.h", "int g(void);\n")
    a2 = _write(new_dir / "a.h", "int f(void);\n")
    b2 = _write(new_dir / "b.h", "int g(void);\n")
    c2 = _write(new_dir / "c.h", "int h(void);\n")
    old = _snap(
        compute_extraction_contract(
            declared_headers=[a1, b1], public_header_dirs=[old_dir], l2_frontend_ran=True
        )
    )
    new = _snap(
        compute_extraction_contract(
            declared_headers=[a2, b2, c2],
            public_header_dirs=[new_dir],
            l2_frontend_ran=True,
        )
    )
    assert (
        old.contract.scope_fields["public_header_dirs"]
        == new.contract.scope_fields["public_header_dirs"]
    )
    assert check_contracts_comparable(old, new) is None  # must not raise either error


def test_scope_field_additive_superset_true_for_unchanged_single_entry_sentinel():
    # The pure-function-level pin for the same fix: an unchanged sentinel
    # value must be treated as trivially satisfied, not declined.
    sentinel = json.dumps(["<single-header-dir>"])
    assert _scope_field_is_additive_superset(sentinel, sentinel)


def test_gate_header_sequence_carve_out_still_raises_when_existing_headers_reordered(
    tmp_path,
):
    # A genuine reorder of the EXISTING headers entangled with growth (b,a,c
    # instead of a,b,c) is a real profile-relevant risk -- reordering
    # declared headers can change how a later header's macros/pragmas
    # resolve -- and must still raise, even though the header SET grew.
    a = _write(tmp_path / "v1" / "a.h", "int f(void);\n")
    b = _write(tmp_path / "v1" / "b.h", "int g(void);\n")
    a2 = _write(tmp_path / "v2" / "a.h", "int f(void);\n")
    b2 = _write(tmp_path / "v2" / "b.h", "int g(void);\n")
    c2 = _write(tmp_path / "v2" / "c.h", "int h(void);\n")
    old = _snap(compute_extraction_contract(declared_headers=[a, b], l2_frontend_ran=True))
    new = _snap(
        compute_extraction_contract(declared_headers=[b2, a2, c2], l2_frontend_ran=True)
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_header_sequence_carve_out_declines_when_a_side_is_single_header_sentinel(
    tmp_path,
):
    a = _write(tmp_path / "v1" / "a.h", "int f(void);\n")
    a2 = _write(tmp_path / "v2" / "a.h", "int f(void);\n")
    b2 = _write(tmp_path / "v2" / "b.h", "int g(void);\n")
    old = _snap(compute_extraction_contract(declared_headers=[a], l2_frontend_ran=True))
    new = _snap(compute_extraction_contract(declared_headers=[a2, b2], l2_frontend_ran=True))
    with pytest.raises((ScopeMismatchError, ProfileMismatchError)):
        check_contracts_comparable(old, new)


# ---------------------------------------------------------------------------
# _header_sequence_is_additive_reorder_free: unit-tested directly (mirrors
# how _scope_field_is_additive_superset is only exercised through the gate
# above, but the reorder-detection logic here is intricate enough to also
# warrant pinning as a pure function)
# ---------------------------------------------------------------------------


def test_header_sequence_additive_reorder_free_true_for_pure_append():
    old = json.dumps(["a.h", "b.h"])
    new = json.dumps(["a.h", "b.h", "c.h"])
    assert _header_sequence_is_additive_reorder_free(old, new)


def test_header_sequence_additive_reorder_free_true_for_insertion_in_middle():
    old = json.dumps(["a.h", "c.h"])
    new = json.dumps(["a.h", "b.h", "c.h"])  # b.h inserted between a.h and c.h
    assert _header_sequence_is_additive_reorder_free(old, new)


def test_header_sequence_additive_reorder_free_false_for_pure_reorder():
    old = json.dumps(["a.h", "b.h"])
    new = json.dumps(["b.h", "a.h"])  # same set, no growth, but reordered
    assert not _header_sequence_is_additive_reorder_free(old, new)


def test_header_sequence_additive_reorder_free_false_for_reorder_entangled_with_growth():
    old = json.dumps(["a.h", "b.h"])
    new = json.dumps(["b.h", "a.h", "c.h"])
    assert not _header_sequence_is_additive_reorder_free(old, new)


def test_header_sequence_additive_reorder_free_false_for_pure_removal():
    old = json.dumps(["a.h", "b.h", "c.h"])
    new = json.dumps(["a.h", "b.h"])
    assert not _header_sequence_is_additive_reorder_free(old, new)


def test_header_sequence_additive_reorder_free_declines_on_single_header_sentinel():
    old = json.dumps(["<single-header>"])
    new = json.dumps(["a.h", "b.h"])
    assert not _header_sequence_is_additive_reorder_free(old, new)
    # Same decline when the NEW side (rather than old) is the sentinel.
    assert not _header_sequence_is_additive_reorder_free(
        json.dumps(["a.h", "b.h"]), json.dumps(["<single-header>"])
    )


def test_header_sequence_additive_reorder_free_declines_on_none():
    assert not _header_sequence_is_additive_reorder_free(None, json.dumps(["a.h"]))
    assert not _header_sequence_is_additive_reorder_free(json.dumps(["a.h"]), None)


# ---------------------------------------------------------------------------
# _include_sequence_is_additive_owned_growth (PR #641 follow-up, fourth
# round): resolve_inferred_header_roots (cli_dump_helpers.py) auto-adds a
# header-owning include directory, whose slot token in include_sequence
# encodes the declared-header set IT owns -- so a pure header addition
# changes this field too, independently of header_sequence.
# ---------------------------------------------------------------------------


def _hdrs_slot(idx: int, pairs: list[tuple[str, str]]) -> str:
    return f"{idx}:hdrs:{json.dumps(sorted(pairs))}"


def test_include_sequence_additive_owned_growth_true_for_pure_append():
    old = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h")])])
    new = json.dumps(
        [_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h"), ("c.h", "c.h")])]
    )
    assert _include_sequence_is_additive_owned_growth(old, new)


def test_include_sequence_additive_owned_growth_true_when_unchanged():
    same = json.dumps([_hdrs_slot(0, [("a.h", "a.h")])])
    assert _include_sequence_is_additive_owned_growth(same, same)


def test_include_sequence_additive_owned_growth_false_for_removal():
    old = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h")])])
    new = json.dumps([_hdrs_slot(0, [("a.h", "a.h")])])
    assert not _include_sequence_is_additive_owned_growth(old, new)


def test_include_sequence_additive_owned_growth_false_for_non_owned_slot_drift():
    # An "ext:" (external, non-owned) slot differing is real, unrelated
    # profile drift -- this carve-out has no business waiving it.
    old = json.dumps(["0:ext:" + "a" * 16])
    new = json.dumps(["0:ext:" + "b" * 16])
    assert not _include_sequence_is_additive_owned_growth(old, new)


def test_include_sequence_additive_owned_growth_false_for_slot_count_change():
    old = json.dumps([_hdrs_slot(0, [("a.h", "a.h")])])
    new = json.dumps(
        [_hdrs_slot(0, [("a.h", "a.h")]), "1:ext:" + "a" * 16]
    )
    assert not _include_sequence_is_additive_owned_growth(old, new)


def test_include_sequence_additive_owned_growth_false_for_single_header_sentinel():
    old = json.dumps(["0:hdrs:<single-header>"])
    new = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h")])])
    assert not _include_sequence_is_additive_owned_growth(old, new)


def test_include_sequence_additive_owned_growth_declines_on_none():
    some = json.dumps([_hdrs_slot(0, [("a.h", "a.h")])])
    assert not _include_sequence_is_additive_owned_growth(None, some)
    assert not _include_sequence_is_additive_owned_growth(some, None)


def test_include_sequence_additive_owned_growth_true_when_one_slot_unchanged():
    # Multi-slot case: slot 0 (an external, non-owned dir) is byte-identical
    # on both sides; only slot 1 (the owned root) grows. The per-slot
    # equality short-circuit must let the unchanged slot through without
    # re-parsing it.
    old = json.dumps(["0:ext:" + "a" * 16, _hdrs_slot(1, [("a.h", "a.h")])])
    new = json.dumps(
        ["0:ext:" + "a" * 16, _hdrs_slot(1, [("a.h", "a.h"), ("b.h", "b.h")])]
    )
    assert _include_sequence_is_additive_owned_growth(old, new)


def test_include_sequence_additive_owned_growth_false_for_index_mismatch():
    # A malformed/reordered slot list (index labels don't line up
    # positionally) can never be safely verified.
    old = json.dumps(["0:hdrs:[[\"a.h\", \"a.h\"]]"])
    new = json.dumps(["1:hdrs:[[\"a.h\", \"a.h\"], [\"b.h\", \"b.h\"]]"])
    assert not _include_sequence_is_additive_owned_growth(old, new)


def test_gate_handles_the_real_directory_based_f8_scenario_end_to_end(tmp_path):
    # Codex review (PR #641 follow-up, fourth round): the real production
    # dump path (cli_dump_helpers.py) calls resolve_inferred_header_roots,
    # which auto-adds the header-owning directory as a declared include --
    # so the real `-H old=<dir> -H new=<dir>` F8 invocation changes BOTH
    # header_sequence AND include_sequence together. Confirmed by direct
    # repro before any fix: this raised ProfileMismatchError because the
    # header-sequence-growth carve-out alone only ever considered
    # `differing <= _HEADER_SEQUENCE_FIELDS`, and include_sequence being
    # also in `differing` meant it declined.
    old_dir = tmp_path / "old" / "include"
    new_dir = tmp_path / "new" / "include"
    a1 = _write(old_dir / "a.h", "int f(void);\n")
    b1 = _write(old_dir / "b.h", "int g(void);\n")
    a2 = _write(new_dir / "a.h", "int f(void);\n")
    b2 = _write(new_dir / "b.h", "int g(void);\n")
    c2 = _write(new_dir / "c.h", "int h(void);\n")
    old_headers = [a1, b1]
    new_headers = [a2, b2, c2]
    old_inc_extra, _ = resolve_inferred_header_roots(old_headers, [])
    new_inc_extra, _ = resolve_inferred_header_roots(new_headers, [])
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            declared_headers=old_headers,
            declared_includes=[IncludeDir(p) for p in old_inc_extra],
            public_header_dirs=[old_dir],
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            declared_headers=new_headers,
            declared_includes=[IncludeDir(p) for p in new_inc_extra],
            public_header_dirs=[new_dir],
        )
    )
    assert old.contract.profile_fields["include_sequence"] != new.contract.profile_fields[
        "include_sequence"
    ]
    assert check_contracts_comparable(old, new) is None  # must not raise either error


def test_gate_composes_header_addition_with_corroborated_build_context_change(
    tmp_path,
):
    # Codex review (PR #641 follow-up, fourth round): a release that both
    # adds a header AND makes a corroborated build-context change (e.g.
    # gnu++17 -> gnu++20) has `differing = {"header_sequence",
    # "language_standard"}` -- a set matching NEITHER carve-out's static
    # field-set on its own, even though each delta is independently
    # sanctioned. The two carve-outs must compose: each claims and
    # verifies its own subset of `differing`, and the pair is comparable
    # only once nothing remains unexplained.
    a = _write(tmp_path / "v1" / "a.h", "int f(void);\n")
    b = _write(tmp_path / "v1" / "b.h", "int g(void);\n")
    a2 = _write(tmp_path / "v2" / "a.h", "int f(void);\n")
    b2 = _write(tmp_path / "v2" / "b.h", "int g(void);\n")
    c2 = _write(tmp_path / "v2" / "c.h", "int h(void);\n")
    old = _snap(
        compute_extraction_contract(
            declared_headers=[a, b], l2_frontend_ran=True, language_standard="gnu++17"
        ),
        parsed_with_build_context=True,
    )
    new = _snap(
        compute_extraction_contract(
            declared_headers=[a2, b2, c2],
            l2_frontend_ran=True,
            language_standard="gnu++20",
        ),
        parsed_with_build_context=True,
    )
    assert check_contracts_comparable(old, new) is None  # must not raise either error


def test_gate_still_raises_when_a_new_header_lands_outside_the_old_common_root(
    tmp_path,
):
    # Known, accepted limitation (Codex review, PR #641 follow-up, fourth
    # round), NOT a correctness bug: the scope "headers" field's identity
    # strings are computed relative to a common root derived independently
    # per side. A header added OUTSIDE the old side's common ancestor
    # directory (e.g. existing headers under include/foo/, new one under a
    # sibling include/bar/) shifts the common root upward, so the EXISTING
    # headers' own identity strings change shape too (["a.h","b.h"] ->
    # ["foo/a.h","foo/b.h"]) even though nothing was removed or renamed.
    # This is the conservative, SAFE failure mode (a real hard-fail, never
    # a silently wrong verdict) for a case genuinely outside the real pvxs
    # F8 scenario (which adds a header in the SAME directory as the
    # existing ones) -- --diagnostic-comparison remains the correct
    # workaround, same as any other case this carve-out declines.
    a = _write(tmp_path / "include" / "foo" / "a.h", "int f(void);\n")
    b = _write(tmp_path / "include" / "foo" / "b.h", "int g(void);\n")
    a2 = _write(tmp_path / "v2" / "include" / "foo" / "a.h", "int f(void);\n")
    b2 = _write(tmp_path / "v2" / "include" / "foo" / "b.h", "int g(void);\n")
    c2 = _write(tmp_path / "v2" / "include" / "bar" / "c.h", "int h(void);\n")
    old = _snap(compute_extraction_contract(declared_headers=[a, b]))
    new = _snap(compute_extraction_contract(declared_headers=[a2, b2, c2]))
    with pytest.raises(ScopeMismatchError):
        check_contracts_comparable(old, new)


def test_gate_raises_profile_mismatch_error_on_profile_drift(tmp_path):
    dep_old = _write(tmp_path / "d1" / "dep.h", "struct Dep { int x; };\n")
    dep_new = _write(tmp_path / "d2" / "dep.h", "struct Dep { int x; int y; };\n")
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            declared_includes=[IncludeDir(tmp_path / "d1")],
            depfile_resolved_paths=[dep_old],
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            declared_includes=[IncludeDir(tmp_path / "d2")],
            depfile_resolved_paths=[dep_new],
        )
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_is_lenient_when_neither_side_has_a_contract():
    check_contracts_comparable(_snap(None), _snap(None))  # must not raise


def test_gate_is_lenient_on_mixed_pair_for_a_given_fingerprint(tmp_path):
    # old has a real profile_fingerprint; new has none at all (e.g. a stored
    # pre-ADR-050 baseline). Neither side has scope_fingerprint here, so the
    # whole comparison must stay lenient -- a mixed pair on one fingerprint
    # never hard-fails just because only one side ever had it.
    old_contract = compute_extraction_contract(
        l2_frontend_ran=True, target_triple="x86_64-linux-gnu"
    )
    new_contract = compute_extraction_contract(public_header_paths=[tmp_path / "foo.h"])
    check_contracts_comparable(_snap(old_contract), _snap(new_contract))  # no raise


def test_gate_still_checks_scope_when_profile_is_mixed(tmp_path):
    # A symbols-only side (scope_fingerprint only) compared against a full
    # L2 side (both fingerprints) must still get its scope checked. Uses a
    # 2-header declared set (a single header's own name is no longer
    # load-bearing scope identity — Codex review, PR #624 follow-up).
    a = _write(tmp_path / "v1" / "a.h", "int g(void);\n")
    old_h = _write(tmp_path / "v1" / "foo.h", "int f(void);\n")
    new_h = _write(tmp_path / "v2" / "bar.h", "int f(void);\n")
    symbols_only = compute_extraction_contract(public_header_paths=[a, old_h])
    full_l2 = compute_extraction_contract(
        declared_headers=[a, new_h], l2_frontend_ran=True
    )
    with pytest.raises(ScopeMismatchError):
        check_contracts_comparable(_snap(symbols_only), _snap(full_l2))


def test_gate_platform_identity_carve_out_allows_genuine_arch_difference():
    old_contract = compute_extraction_contract(
        l2_frontend_ran=True, target_triple="x86_64-linux-gnu"
    )
    new_contract = compute_extraction_contract(
        l2_frontend_ran=True, target_triple="aarch64-linux-gnu"
    )
    old = _snap(old_contract, elf=ElfMetadata(machine="EM_X86_64"))
    new = _snap(new_contract, elf=ElfMetadata(machine="EM_AARCH64"))
    check_contracts_comparable(old, new)  # must not raise


def test_gate_platform_identity_carve_out_still_raises_when_binaries_agree():
    # Same target_triple mismatch, but the binaries themselves are the same
    # architecture -- a misconfigured extraction, not a legitimate
    # cross-architecture compare, so it must still raise.
    old_contract = compute_extraction_contract(
        l2_frontend_ran=True, target_triple="x86_64-linux-gnu"
    )
    new_contract = compute_extraction_contract(
        l2_frontend_ran=True, target_triple="aarch64-linux-gnu"
    )
    old = _snap(old_contract, elf=ElfMetadata(machine="EM_X86_64"))
    new = _snap(new_contract, elf=ElfMetadata(machine="EM_X86_64"))
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_platform_identity_carve_out_covers_elf_class_change():
    # Codex review (PR #624): EM_RISCV shares e_machine/EI_DATA across word
    # sizes, so an RV32 -> RV64 change (a genuine elf_class_changed) must
    # still be recognized as a real binary-platform axis difference, not
    # masked by identical machine/endianness alone.
    old_contract = compute_extraction_contract(l2_frontend_ran=True, pointer_width=32)
    new_contract = compute_extraction_contract(l2_frontend_ran=True, pointer_width=64)
    old = _snap(old_contract, elf=ElfMetadata(machine="EM_RISCV", elf_class=32))
    new = _snap(new_contract, elf=ElfMetadata(machine="EM_RISCV", elf_class=64))
    check_contracts_comparable(old, new)  # must not raise


def test_gate_carve_out_does_not_cover_a_co_occurring_non_platform_field(tmp_path):
    # The carve-out is scoped to target/pointer-width/endianness alone: a
    # target mismatch alongside a genuinely different compiler_family must
    # still raise even if the binaries' own architecture differs too.
    old_contract = compute_extraction_contract(
        l2_frontend_ran=True,
        target_triple="x86_64-linux-gnu",
        compiler_family="gcc",
    )
    new_contract = compute_extraction_contract(
        l2_frontend_ran=True,
        target_triple="aarch64-linux-gnu",
        compiler_family="clang",
    )
    old = _snap(old_contract, elf=ElfMetadata(machine="EM_X86_64"))
    new = _snap(new_contract, elf=ElfMetadata(machine="EM_AARCH64"))
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_platform_identity_carve_out_covers_pe():
    old_contract = compute_extraction_contract(
        l2_frontend_ran=True, target_triple="x86_64-pc-windows-msvc"
    )
    new_contract = compute_extraction_contract(
        l2_frontend_ran=True, target_triple="aarch64-pc-windows-msvc"
    )
    old = _snap(old_contract, pe=PeMetadata(machine="IMAGE_FILE_MACHINE_AMD64"))
    new = _snap(new_contract, pe=PeMetadata(machine="IMAGE_FILE_MACHINE_ARM64"))
    check_contracts_comparable(old, new)  # must not raise


def test_gate_platform_identity_carve_out_covers_macho():
    old_contract = compute_extraction_contract(
        l2_frontend_ran=True, target_triple="x86_64-apple-darwin"
    )
    new_contract = compute_extraction_contract(
        l2_frontend_ran=True, target_triple="arm64-apple-darwin"
    )
    old = _snap(old_contract, macho=MachoMetadata(cpu_type="X86_64"))
    new = _snap(new_contract, macho=MachoMetadata(cpu_type="ARM64"))
    check_contracts_comparable(old, new)  # must not raise


def test_gate_carve_out_does_not_apply_without_any_binary_platform_metadata():
    # Neither side carries elf/pe/macho metadata at all --
    # _binary_platform_components returns None for both, so the carve-out
    # cannot confirm a genuine architecture difference and the mismatch
    # still raises.
    old_contract = compute_extraction_contract(
        l2_frontend_ran=True, target_triple="x86_64-linux-gnu"
    )
    new_contract = compute_extraction_contract(
        l2_frontend_ran=True, target_triple="aarch64-linux-gnu"
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(_snap(old_contract), _snap(new_contract))


def test_gate_build_context_carve_out_allows_corroborated_std_raise():
    # Real CI incident (PR #624 follow-up, examples/case98_cxx_standard_floor_raised):
    # both sides were actually reconciled against real build-system evidence
    # (parsed_with_build_context=True), so a genuine language_standard raise
    # is exactly what CXX_STANDARD_FLOOR_RAISED/ABI_RELEVANT_BUILD_FLAG_CHANGED
    # exist to surface as a RISK finding, not a reason to refuse a verdict.
    old_contract = compute_extraction_contract(
        l2_frontend_ran=True, language_standard="gnu++17"
    )
    new_contract = compute_extraction_contract(
        l2_frontend_ran=True, language_standard="c++20"
    )
    old = _snap(old_contract, parsed_with_build_context=True)
    new = _snap(new_contract, parsed_with_build_context=True)
    check_contracts_comparable(old, new)  # must not raise


def test_gate_build_context_carve_out_requires_both_sides_corroborated():
    # Only one side actually went through build-context reconciliation --
    # exactly the "manifest/CLI-flag drift" mistake the gate exists to
    # catch (e.g. a stale cached dump compared against a freshly
    # build-reconciled one), so this must still raise.
    old_contract = compute_extraction_contract(
        l2_frontend_ran=True, language_standard="gnu++17"
    )
    new_contract = compute_extraction_contract(
        l2_frontend_ran=True, language_standard="c++20"
    )
    old = _snap(old_contract, parsed_with_build_context=False)
    new = _snap(new_contract, parsed_with_build_context=True)
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_build_context_carve_out_does_not_cover_a_co_occurring_other_field():
    # Scoped to language_standard/macro_ops alone: a std-floor raise
    # alongside a genuinely different compiler_family must still raise even
    # when both sides are build-context corroborated.
    old_contract = compute_extraction_contract(
        l2_frontend_ran=True,
        language_standard="gnu++17",
        compiler_family="gcc",
    )
    new_contract = compute_extraction_contract(
        l2_frontend_ran=True,
        language_standard="c++20",
        compiler_family="clang",
    )
    old = _snap(old_contract, parsed_with_build_context=True)
    new = _snap(new_contract, parsed_with_build_context=True)
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_carve_out_does_not_waive_pointer_width_via_unrelated_machine_change():
    # Codex review (PR #624): the carve-out must verify the SPECIFIC
    # differing profile field against its OWN corresponding binary
    # component, not merely that "some" component of the platform identity
    # changed somewhere. Here only pointer_width differs in the profile (a
    # bogus/misconfigured extraction), and the binaries' machine genuinely
    # differs too (a real but UNRELATED architecture change) -- but
    # elf_class (pointer_width's corresponding binary field) is IDENTICAL on
    # both sides, so the pointer_width mismatch is not corroborated and must
    # still raise instead of being waived by the coincidental machine change.
    old_contract = compute_extraction_contract(l2_frontend_ran=True, pointer_width=32)
    new_contract = compute_extraction_contract(l2_frontend_ran=True, pointer_width=64)
    old = _snap(old_contract, elf=ElfMetadata(machine="EM_X86_64", elf_class=64))
    new = _snap(new_contract, elf=ElfMetadata(machine="EM_AARCH64", elf_class=64))
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_carve_out_cannot_verify_pointer_width_on_pe_and_still_raises():
    # PE metadata has no distinct word-size field (unlike ELF's elf_class),
    # so a pointer_width-only profile mismatch can never be corroborated for
    # a PE snapshot -- the carve-out must not waive it just because
    # `machine` also happens to differ.
    old_contract = compute_extraction_contract(l2_frontend_ran=True, pointer_width=32)
    new_contract = compute_extraction_contract(l2_frontend_ran=True, pointer_width=64)
    old = _snap(old_contract, pe=PeMetadata(machine="IMAGE_FILE_MACHINE_I386"))
    new = _snap(new_contract, pe=PeMetadata(machine="IMAGE_FILE_MACHINE_AMD64"))
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_carve_out_riscv_word_size_change_verified_via_full_axis():
    # Codex review (PR #624): EM_RISCV shares e_machine across RV32/RV64, so
    # a target_triple change that's really just an expression of a genuine
    # word-size change (riscv32-... vs. riscv64-...) must not fail
    # verification on its own narrow "machine" component alone when
    # elf_class already confirms the architecture genuinely differs.
    old_contract = compute_extraction_contract(
        l2_frontend_ran=True, target_triple="riscv32-unknown-elf", pointer_width=32
    )
    new_contract = compute_extraction_contract(
        l2_frontend_ran=True, target_triple="riscv64-unknown-elf", pointer_width=64
    )
    old = _snap(old_contract, elf=ElfMetadata(machine="EM_RISCV", elf_class=32))
    new = _snap(new_contract, elf=ElfMetadata(machine="EM_RISCV", elf_class=64))
    check_contracts_comparable(old, new)  # must not raise


# ---------------------------------------------------------------------------
# diagnostic=True mode: --diagnostic-comparison's escape hatch
# ---------------------------------------------------------------------------


def test_gate_diagnostic_mode_returns_descriptor_instead_of_raising_on_scope(tmp_path):
    # 2-header declared set: a single header's own name is no longer
    # load-bearing scope identity (Codex review, PR #624 follow-up).
    a = _write(tmp_path / "v1" / "a.h", "int g(void);\n")
    old_h = _write(tmp_path / "v1" / "foo.h", "int f(void);\n")
    new_h = _write(tmp_path / "v2" / "bar.h", "int f(void);\n")
    old = _snap(compute_extraction_contract(declared_headers=[a, old_h]))
    new = _snap(compute_extraction_contract(declared_headers=[a, new_h]))
    result = check_contracts_comparable(old, new, diagnostic=True)
    assert isinstance(result, ComparabilityMismatch)
    assert result.kind == "scope"


def test_gate_diagnostic_mode_returns_descriptor_instead_of_raising_on_profile(
    tmp_path,
):
    dep_old = _write(tmp_path / "d1" / "dep.h", "struct Dep { int x; };\n")
    dep_new = _write(tmp_path / "d2" / "dep.h", "struct Dep { int x; int y; };\n")
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            declared_includes=[IncludeDir(tmp_path / "d1")],
            depfile_resolved_paths=[dep_old],
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            declared_includes=[IncludeDir(tmp_path / "d2")],
            depfile_resolved_paths=[dep_new],
        )
    )
    result = check_contracts_comparable(old, new, diagnostic=True)
    assert isinstance(result, ComparabilityMismatch)
    assert result.kind == "profile"


def test_gate_diagnostic_mode_returns_none_when_comparable(tmp_path):
    old_h = _write(tmp_path / "v1" / "foo.h", "int f(void);\n")
    new_h = _write(tmp_path / "v2" / "foo.h", "int f(void);\n")
    old = _snap(compute_extraction_contract(declared_headers=[old_h]))
    new = _snap(compute_extraction_contract(declared_headers=[new_h]))
    assert check_contracts_comparable(old, new, diagnostic=True) is None
