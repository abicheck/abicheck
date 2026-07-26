"""ADR-050 D2 (G32 Phase A, slice 1) — the check_contracts_comparable gate.

Split out of test_comparability.py (Codex review, PR #641 follow-up: the
parent file crossed the file-size hard cap once this round's corroboration/
opaque-mismatch regression tests landed) — see that file's own module
docstring for the fingerprint-computation tests this one doesn't repeat.
Scope here is exactly the gate's own behavior: the scope/profile carve-outs,
their composition, the pure carve-out helper functions, and
--diagnostic-comparison mode.
"""

from __future__ import annotations

import json
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
from abicheck.errors import ProfileMismatchError, ScopeMismatchError
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


# ---------------------------------------------------------------------------
# Codex review, PR #641 follow-up (P1 x2): sequence carve-outs must be
# corroborated by genuine scope growth, and an opaque profile mismatch must
# not be silently waived
# ---------------------------------------------------------------------------


def test_gate_header_sequence_carve_out_declines_without_scope_corroboration(tmp_path):
    # A header already declared identically on both sides via
    # --public-header (so scope's "headers" field -- and therefore
    # scope_fingerprint -- is completely UNCHANGED), but fed to the L2
    # frontend via -H only on the new side, produces the identical
    # additive-growth SHAPE in header_sequence that the real F8 scenario
    # does. Unlike F8, there is no genuine new declared-surface content here
    # -- the old snapshot simply never parsed x.h's AST at all, so a real
    # removal inside it between old and new would be silently invisible,
    # not reported. The carve-out must decline (and this must still raise)
    # because the scope-level check never independently confirms any
    # growth at all.
    a = _write(tmp_path / "v1" / "a.h", "int f(void);\n")
    b = _write(tmp_path / "v1" / "b.h", "int g(void);\n")
    x_old = _write(tmp_path / "v1" / "x.h", "int h(void);\n")
    a2 = _write(tmp_path / "v2" / "a.h", "int f(void);\n")
    b2 = _write(tmp_path / "v2" / "b.h", "int g(void);\n")
    x2 = _write(tmp_path / "v2" / "x.h", "int h(void);\n")
    old = _snap(
        compute_extraction_contract(
            declared_headers=[a, b],
            public_header_paths=[x_old],
            l2_frontend_ran=True,
        )
    )
    new = _snap(
        compute_extraction_contract(
            declared_headers=[a2, b2, x2],
            l2_frontend_ran=True,
        )
    )
    assert old.contract.scope_fingerprint == new.contract.scope_fingerprint
    assert old.contract.profile_fields["header_sequence"] != new.contract.profile_fields[
        "header_sequence"
    ]
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_include_sequence_carve_out_declines_without_scope_corroboration(
    tmp_path,
):
    # Same shape as the header-sequence case above, but for include_sequence:
    # x.h is already declared identically on both sides (old via
    # --public-header, new via -H), so scope_fingerprint is completely
    # unchanged, but the auto-added header-owning directory's "hdrs:..."
    # owned-token (resolve_inferred_header_roots) grows from {a,b} to
    # {a,b,x} purely because x is now fed to the L2 frontend on the new
    # side -- the identical single owned slot on both sides, so this isn't
    # merely declined by the carve-out's own slot-count check. A genuinely
    # differing, verified-additive scope growth must corroborate the
    # carve-out, not just an additive-looking include_sequence on its own.
    a = _write(tmp_path / "v1" / "a.h", "int f(void);\n")
    b = _write(tmp_path / "v1" / "b.h", "int g(void);\n")
    x_old = _write(tmp_path / "v1" / "x.h", "int h(void);\n")
    a2 = _write(tmp_path / "v2" / "a.h", "int f(void);\n")
    b2 = _write(tmp_path / "v2" / "b.h", "int g(void);\n")
    x2 = _write(tmp_path / "v2" / "x.h", "int h(void);\n")
    old_headers = [a, b]
    new_headers = [a2, b2, x2]
    old_inc_extra, _ = resolve_inferred_header_roots(old_headers, [])
    new_inc_extra, _ = resolve_inferred_header_roots(new_headers, [])
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            declared_headers=old_headers,
            declared_includes=[IncludeDir(p) for p in old_inc_extra],
            public_header_paths=[x_old],
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            declared_headers=new_headers,
            declared_includes=[IncludeDir(p) for p in new_inc_extra],
        )
    )
    assert old.contract.scope_fingerprint == new.contract.scope_fingerprint
    assert old.contract.profile_fields["include_sequence"] != new.contract.profile_fields[
        "include_sequence"
    ]
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_rejects_opaque_profile_mismatch_with_no_recognized_differing_field():
    # Codex review, PR #641 follow-up (P1): a deserialized contract whose
    # profile_fields was entirely absent/malformed
    # (serialization._extraction_contract_from_dict substitutes {}) still
    # carries its original profile_fingerprint string. Every
    # PROFILE_FIELD_KEYS comparison then trivially reads "" == "", so
    # `differing` is empty even though the fingerprints genuinely differ --
    # this must NOT be silently treated as "nothing to explain, therefore
    # comparable."
    old = _snap(
        ExtractionContract(
            profile_fingerprint="old-opaque-hash",
            scope_fingerprint=None,
            profile_fields={},
            scope_fields={},
        )
    )
    new = _snap(
        ExtractionContract(
            profile_fingerprint="new-opaque-hash",
            scope_fingerprint=None,
            profile_fields={},
            scope_fields={},
        )
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_rejects_opaque_profile_mismatch_in_diagnostic_mode_too():
    old = _snap(
        ExtractionContract(
            profile_fingerprint="old-opaque-hash",
            scope_fingerprint=None,
            profile_fields={},
            scope_fields={},
        )
    )
    new = _snap(
        ExtractionContract(
            profile_fingerprint="new-opaque-hash",
            scope_fingerprint=None,
            profile_fields={},
            scope_fields={},
        )
    )
    result = check_contracts_comparable(old, new, diagnostic=True)
    assert isinstance(result, ComparabilityMismatch)
    assert result.kind == "profile"
