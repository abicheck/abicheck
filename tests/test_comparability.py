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
    IncludeDir,
    compute_extraction_contract,
    manifest_tu_scope_field,
)
from abicheck.dump_manifest import parse_manifest
from abicheck.elf_metadata import ElfMetadata
from abicheck.errors import ProfileMismatchError, ScopeMismatchError, SnapshotError
from abicheck.macho_metadata import MachoMetadata
from abicheck.model import AbiSnapshot, ExtractionContract


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
# manifest_tu_scope_field / scope_fingerprint TU-level coverage (ADR-050 D1,
# G32 Phase E prerequisite): scope_fingerprint must hash each TU's own name,
# ordered includes/forced_includes (incl. project_owned), required, and
# contributes_to_abi -- not just the manifest's flat `roots`/public-header
# fields, which alone can't tell two structurally different manifests apart.
# ---------------------------------------------------------------------------


def _manifest(tmp_path: Path, body: str):
    base = tmp_path / "manifest_dir"
    base.mkdir(exist_ok=True)
    return parse_manifest(body, base_dir=base, source="<test>")


def test_manifest_tu_scope_field_is_stable_json_for_identical_manifests(tmp_path):
    body = (
        "roots: [a.h]\n"
        "translation_units:\n"
        "  - name: tu_a\n"
        "    forced_includes: [a.h]\n"
        "    includes: [vendor]\n"
    )
    m1 = _manifest(tmp_path, body)
    m2 = _manifest(tmp_path, body)
    assert manifest_tu_scope_field(m1) == manifest_tu_scope_field(m2)


def test_manifest_scope_fingerprint_differs_when_tu_renamed(tmp_path):
    old = _manifest(
        tmp_path,
        "roots: [a.h]\ntranslation_units:\n  - name: tu_a\n    forced_includes: [a.h]\n",
    )
    new = _manifest(
        tmp_path,
        "roots: [a.h]\ntranslation_units:\n  - name: tu_b\n    forced_includes: [a.h]\n",
    )
    old_c = compute_extraction_contract(
        declared_headers=list(old.roots), manifest_tu_scope=manifest_tu_scope_field(old)
    )
    new_c = compute_extraction_contract(
        declared_headers=list(new.roots), manifest_tu_scope=manifest_tu_scope_field(new)
    )
    assert old_c.scope_fingerprint != new_c.scope_fingerprint


def test_manifest_scope_fingerprint_differs_when_includes_reordered(tmp_path):
    old = _manifest(
        tmp_path,
        "roots: [a.h]\ntranslation_units:\n"
        "  - name: tu_a\n    forced_includes: [a.h]\n    includes: [vendor, extra]\n",
    )
    new = _manifest(
        tmp_path,
        "roots: [a.h]\ntranslation_units:\n"
        "  - name: tu_a\n    forced_includes: [a.h]\n    includes: [extra, vendor]\n",
    )
    old_c = compute_extraction_contract(
        declared_headers=list(old.roots), manifest_tu_scope=manifest_tu_scope_field(old)
    )
    new_c = compute_extraction_contract(
        declared_headers=list(new.roots), manifest_tu_scope=manifest_tu_scope_field(new)
    )
    assert old_c.scope_fingerprint != new_c.scope_fingerprint


def test_manifest_scope_fingerprint_differs_when_contributes_to_abi_flipped(tmp_path):
    old = _manifest(
        tmp_path,
        "roots: [a.h]\ntranslation_units:\n"
        "  - name: tu_a\n    forced_includes: [a.h]\n"
        "  - name: tu_b\n    forced_includes: [a.h]\n"
        "    required: false\n    contributes_to_abi: false\n",
    )
    new = _manifest(
        tmp_path,
        "roots: [a.h]\ntranslation_units:\n"
        "  - name: tu_a\n    forced_includes: [a.h]\n"
        "  - name: tu_b\n    forced_includes: [a.h]\n"
        "    required: true\n    contributes_to_abi: true\n",
    )
    old_c = compute_extraction_contract(
        declared_headers=list(old.roots), manifest_tu_scope=manifest_tu_scope_field(old)
    )
    new_c = compute_extraction_contract(
        declared_headers=list(new.roots), manifest_tu_scope=manifest_tu_scope_field(new)
    )
    assert old_c.scope_fingerprint != new_c.scope_fingerprint


def test_manifest_scope_fingerprint_differs_on_project_owned_bit(tmp_path):
    old = _manifest(
        tmp_path,
        "roots: [a.h]\ntranslation_units:\n"
        "  - name: tu_a\n    forced_includes: [a.h]\n"
        "    includes: [{path: ../src, project_owned: false}]\n",
    )
    new = _manifest(
        tmp_path,
        "roots: [a.h]\ntranslation_units:\n"
        "  - name: tu_a\n    forced_includes: [a.h]\n"
        "    includes: [{path: ../src, project_owned: true}]\n",
    )
    old_c = compute_extraction_contract(
        declared_headers=list(old.roots), manifest_tu_scope=manifest_tu_scope_field(old)
    )
    new_c = compute_extraction_contract(
        declared_headers=list(new.roots), manifest_tu_scope=manifest_tu_scope_field(new)
    )
    assert old_c.scope_fingerprint != new_c.scope_fingerprint


def test_manifest_scope_fingerprint_matches_across_mount_points(tmp_path):
    # Two checkouts of the identical manifest structure at different absolute
    # mount points must still fingerprint identically -- TU paths normalize
    # relative to the manifest's own base_dir (ADR-050: "for the manifest
    # path, both fingerprints' roots are simply the manifest file's own
    # directory").
    body = (
        "roots: [a.h]\ntranslation_units:\n"
        "  - name: tu_a\n    forced_includes: [a.h]\n    includes: [../src]\n"
    )
    old_base = tmp_path / "checkout1" / "manifest_dir"
    new_base = tmp_path / "checkout2" / "manifest_dir"
    old_base.mkdir(parents=True)
    new_base.mkdir(parents=True)
    old = parse_manifest(body, base_dir=old_base, source="<old>")
    new = parse_manifest(body, base_dir=new_base, source="<new>")
    old_c = compute_extraction_contract(
        declared_headers=list(old.roots), manifest_tu_scope=manifest_tu_scope_field(old)
    )
    new_c = compute_extraction_contract(
        declared_headers=list(new.roots), manifest_tu_scope=manifest_tu_scope_field(new)
    )
    assert old_c.scope_fingerprint == new_c.scope_fingerprint


def test_legacy_scope_fingerprint_unaffected_by_translation_units_field(tmp_path):
    # A plain, non-manifest declared_headers call (no manifest_tu_scope
    # given) must still leave scope_fingerprint driven only by the header
    # identity -- adding the "translation_units" field must not itself
    # introduce spurious mismatches between two ordinary legacy dumps.
    old_h = _write(tmp_path / "v1" / "foo.h", "int f(void);\n")
    new_h = _write(tmp_path / "v2" / "foo.h", "int f(void);\n")
    old = compute_extraction_contract(declared_headers=[old_h])
    new = compute_extraction_contract(declared_headers=[new_h])
    assert old.scope_fingerprint == new.scope_fingerprint
    assert old.scope_fields["translation_units"] == "[]"
