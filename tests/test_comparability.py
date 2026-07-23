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

import os
from pathlib import Path

import pytest

from abicheck.comparability import (
    ComparabilityMismatch,
    IncludeDir,
    check_contracts_comparable,
    compute_extraction_contract,
)
from abicheck.elf_metadata import ElfMetadata
from abicheck.errors import ProfileMismatchError, ScopeMismatchError, SnapshotError
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


def test_2_different_header_name_produces_different_scope_fingerprint(tmp_path):
    old_h = _write(tmp_path / "v1" / "foo.h", "int add(int, int);\n")
    new_h = _write(tmp_path / "v2" / "bar.h", "int add(int, int);\n")
    old = compute_extraction_contract(declared_headers=[old_h])
    new = compute_extraction_contract(declared_headers=[new_h])
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
    def _raising_commonpath(paths):
        raise ValueError("simulated: no common anchor")

    monkeypatch.setattr(os.path, "commonpath", _raising_commonpath)
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


# ---------------------------------------------------------------------------
# diagnostic=True mode: --diagnostic-comparison's escape hatch
# ---------------------------------------------------------------------------


def test_gate_diagnostic_mode_returns_descriptor_instead_of_raising_on_scope(tmp_path):
    old_h = _write(tmp_path / "v1" / "foo.h", "int f(void);\n")
    new_h = _write(tmp_path / "v2" / "bar.h", "int f(void);\n")
    old = _snap(compute_extraction_contract(declared_headers=[old_h]))
    new = _snap(compute_extraction_contract(declared_headers=[new_h]))
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
