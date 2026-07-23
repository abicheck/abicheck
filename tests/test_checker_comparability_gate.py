"""ADR-050 D2 (G32 Phase A, slice 2) — check_contracts_comparable wired into
checker.compare(). Every snapshot produced by a real dump() still has
contract=None today (dumper.py wiring is separate, not-yet-started work —
see abicheck/comparability.py's module docstring), so these tests build
AbiSnapshot.contract by hand via compute_extraction_contract() to exercise
the wiring itself."""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.comparability import IncludeDir, compute_extraction_contract
from abicheck.errors import ProfileMismatchError, ScopeMismatchError
from abicheck.model import AbiSnapshot, Function, Visibility


def _snap(version: str, contract=None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=[
            Function(
                name="f",
                mangled="_Z1fv",
                return_type="void",
                visibility=Visibility.PUBLIC,
            )
        ],
        contract=contract,
    )


def test_compare_raises_scope_mismatch_by_default(tmp_path):
    old_h = tmp_path / "v1" / "foo.h"
    new_h = tmp_path / "v2" / "bar.h"
    old_h.parent.mkdir(parents=True)
    new_h.parent.mkdir(parents=True)
    old_h.write_text("int f(void);\n")
    new_h.write_text("int f(void);\n")
    old = _snap("1.0", compute_extraction_contract(declared_headers=[old_h]))
    new = _snap("2.0", compute_extraction_contract(declared_headers=[new_h]))
    with pytest.raises(ScopeMismatchError):
        compare(old, new)


def test_compare_raises_profile_mismatch_by_default(tmp_path):
    dep_old = tmp_path / "d1" / "dep.h"
    dep_new = tmp_path / "d2" / "dep.h"
    dep_old.parent.mkdir(parents=True)
    dep_new.parent.mkdir(parents=True)
    dep_old.write_text("struct Dep { int x; };\n")
    dep_new.write_text("struct Dep { int x; int y; };\n")
    old = _snap(
        "1.0",
        compute_extraction_contract(
            l2_frontend_ran=True,
            declared_includes=[IncludeDir(tmp_path / "d1")],
            depfile_resolved_paths=[dep_old],
        ),
    )
    new = _snap(
        "2.0",
        compute_extraction_contract(
            l2_frontend_ran=True,
            declared_includes=[IncludeDir(tmp_path / "d2")],
            depfile_resolved_paths=[dep_new],
        ),
    )
    with pytest.raises(ProfileMismatchError):
        compare(old, new)


def test_compare_with_no_contract_on_either_side_is_unaffected():
    # The ordinary case today: no dumper.py wiring yet, so contract=None on
    # both sides -- compare() must behave exactly as it always has.
    old = _snap("1.0")
    new = _snap("2.0")
    result = compare(old, new)
    assert result.contract_coverage is None
    assert result.assurance is None


def test_compare_diagnostic_comparison_downgrades_mismatch_to_tentative_diff(
    tmp_path,
):
    old_h = tmp_path / "v1" / "foo.h"
    new_h = tmp_path / "v2" / "bar.h"
    old_h.parent.mkdir(parents=True)
    new_h.parent.mkdir(parents=True)
    old_h.write_text("int f(void);\n")
    new_h.write_text("int f(void);\n")
    old = _snap("1.0", compute_extraction_contract(declared_headers=[old_h]))
    new = _snap("2.0", compute_extraction_contract(declared_headers=[new_h]))
    result = compare(old, new, diagnostic_comparison=True)
    assert result.assurance == "none"
    # CodeRabbit review (PR #624): the escape hatch's stated purpose is "the
    # caller can still see a result but knows not to trust it" -- that
    # requires knowing *why*, not just a bare assurance=="none". The
    # non-diagnostic (raising) path already surfaces this via the exception
    # message; the diagnostic path must surface the same reason through the
    # existing coverage_warnings disclosure instead of discarding it.
    assert any("scope" in w.lower() for w in result.coverage_warnings)


def test_compare_contract_coverage_partial_when_exactly_one_side_has_a_contract(
    tmp_path,
):
    h = tmp_path / "foo.h"
    h.write_text("int f(void);\n")
    old = _snap("1.0", compute_extraction_contract(declared_headers=[h]))
    new = _snap("2.0", contract=None)
    result = compare(old, new)
    assert result.contract_coverage == "partial"
    assert result.assurance is None  # comparable pair; no mismatch to bypass


def test_compare_contract_coverage_partial_when_only_profile_fingerprint_is_mixed(
    tmp_path,
):
    # Codex review (PR #624): both sides carry a real contract, but only the
    # full-L2 side has a profile_fingerprint (the symbols-only side has only
    # scope provenance) -- check_contracts_comparable correctly skips the
    # profile check for this pair (an ordinary depth difference), but
    # contract_coverage must still disclose that the profile axis was never
    # actually checked, not report full coverage just because neither
    # `contract` object is None.
    h_old = tmp_path / "old" / "foo.h"
    h_new = tmp_path / "new" / "foo.h"
    h_old.parent.mkdir(parents=True)
    h_new.parent.mkdir(parents=True)
    h_old.write_text("int f(void);\n")
    h_new.write_text("int f(void);\n")
    symbols_only = compute_extraction_contract(public_header_paths=[h_old])
    full_l2 = compute_extraction_contract(
        declared_headers=[h_new], l2_frontend_ran=True
    )
    old = _snap("1.0", symbols_only)
    new = _snap("2.0", full_l2)
    result = compare(old, new)
    assert result.contract_coverage == "partial"


def test_compare_contract_coverage_none_when_both_sides_comparable(tmp_path):
    h_old = tmp_path / "v1" / "foo.h"
    h_new = tmp_path / "v2" / "foo.h"
    h_old.parent.mkdir(parents=True)
    h_new.parent.mkdir(parents=True)
    h_old.write_text("int f(void);\n")
    h_new.write_text("int f(void);\n")
    old = _snap("1.0", compute_extraction_contract(declared_headers=[h_old]))
    new = _snap("2.0", compute_extraction_contract(declared_headers=[h_new]))
    result = compare(old, new)
    assert result.contract_coverage is None
    assert result.assurance is None
