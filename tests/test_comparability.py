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

from pathlib import Path

import pytest

from abicheck.comparability import (
    IncludeDir,
    check_contracts_comparable,
    compute_extraction_contract,
)
from abicheck.elf_metadata import ElfMetadata
from abicheck.errors import ProfileMismatchError, ScopeMismatchError, SnapshotError
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


def test_2_different_header_name_produces_different_scope_fingerprint(tmp_path):
    old_h = _write(tmp_path / "v1" / "foo.h", "int add(int, int);\n")
    new_h = _write(tmp_path / "v2" / "bar.h", "int add(int, int);\n")
    old = compute_extraction_contract(declared_headers=[old_h])
    new = compute_extraction_contract(declared_headers=[new_h])
    assert old.scope_fingerprint != new.scope_fingerprint


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


# ---------------------------------------------------------------------------
# generated driver TU exclusion (test 11)
# ---------------------------------------------------------------------------


def test_11_generated_driver_tu_excluded_from_bucketing(tmp_path):
    old_h = _write(tmp_path / "old" / "foo.h", "int f(void);\n")
    new_h = _write(tmp_path / "new" / "foo.h", "int f(void);\n")
    driver_old = _write(
        tmp_path / "old" / "__driver__.cpp", '#include "/abs/old/foo.h"\n'
    )
    driver_new = _write(
        tmp_path / "new" / "__driver__.cpp", '#include "/abs/new/foo.h"\n'
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
    old_h = _write(tmp_path / "v1" / "foo.h", "int f(void);\n")
    new_h = _write(tmp_path / "v2" / "bar.h", "int f(void);\n")
    old = _snap(compute_extraction_contract(declared_headers=[old_h]))
    new = _snap(compute_extraction_contract(declared_headers=[new_h]))
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
    # L2 side (both fingerprints) must still get its scope checked.
    old_h = _write(tmp_path / "v1" / "foo.h", "int f(void);\n")
    new_h = _write(tmp_path / "v2" / "bar.h", "int f(void);\n")
    symbols_only = compute_extraction_contract(public_header_paths=[old_h])
    full_l2 = compute_extraction_contract(
        declared_headers=[new_h], l2_frontend_ran=True
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
