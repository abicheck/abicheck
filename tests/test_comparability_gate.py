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
    SCOPE_FIELD_KEYS,
    ComparabilityMismatch,
    IncludeDir,
    _fingerprint_matches_fields,
    _header_sequence_is_additive_reorder_free,
    _include_sequence_is_additive_owned_growth,
    _scope_field_is_additive_superset,
    _scope_growth_corroborated,
    _scope_newly_added_headers,
    _sha256_of,
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


def _real_scope_fingerprint(scope_fields: dict[str, str]) -> str:
    """The genuine scope_fingerprint compute_extraction_contract would
    produce for a hand-built scope_fields dict -- needed once
    check_contracts_comparable started verifying that a contract's own
    fingerprint actually reproduces from its own fields (Codex review, PR
    #641 follow-up, sixth P1); a hand-typed placeholder like "s-old" no
    longer passes that check."""
    return _sha256_of(*[scope_fields[k] for k in SCOPE_FIELD_KEYS])


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


def test_scope_newly_added_headers_returns_the_added_set():
    old = json.dumps(["a.h", "b.h"])
    new = json.dumps(["a.h", "b.h", "c.h"])
    assert _scope_newly_added_headers(old, new) == {"c.h"}


def test_scope_newly_added_headers_empty_when_unchanged():
    same = json.dumps(["a.h", "b.h"])
    assert _scope_newly_added_headers(same, same) == set()


def test_scope_newly_added_headers_none_on_none():
    some = json.dumps(["a.h"])
    assert _scope_newly_added_headers(None, some) is None
    assert _scope_newly_added_headers(some, None) is None


def test_scope_newly_added_headers_none_on_malformed_json():
    assert _scope_newly_added_headers("not-json", json.dumps(["a.h"])) is None


def test_scope_newly_added_headers_none_on_single_header_sentinel():
    sentinel = json.dumps(["<single-header>"])
    assert _scope_newly_added_headers(sentinel, json.dumps(["a.h", "b.h"])) is None
    assert _scope_newly_added_headers(json.dumps(["a.h", "b.h"]), sentinel) is None


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
#
# scope_new_headers is required (Codex review, PR #641 follow-up, ninth
# P1): the appended sequence entries must correspond to headers genuinely
# new to the declared scope, not merely an additive-shaped sequence on its
# own. _ANY_NEW_HEADERS below is a permissive stand-in covering every
# header name used in this whole shape-only test block, so these tests
# keep isolating the SHAPE logic (trailing-append/reorder/removal) --
# see test_gate_*_correspondence_* further down for the dedicated
# correspondence-check tests.
# ---------------------------------------------------------------------------

_ANY_NEW_HEADERS = frozenset({"a.h", "b.h", "c.h"})


def test_header_sequence_additive_reorder_free_true_for_pure_append():
    old = json.dumps(["a.h", "b.h"])
    new = json.dumps(["a.h", "b.h", "c.h"])
    assert _header_sequence_is_additive_reorder_free(old, new, _ANY_NEW_HEADERS)


def test_header_sequence_additive_reorder_free_false_for_insertion_in_middle():
    # Codex review, PR #641 follow-up (seventh P1): inserting b.h BETWEEN
    # a.h and c.h keeps a.h/c.h's relative order to each other, but c.h is
    # now parsed with b.h's macros/pragmas already in effect -- a genuinely
    # different preprocessing context than before, even though the shape
    # superficially looks like a pure addition. Only a strictly-trailing
    # append (nothing inserted before or between existing headers) is safe.
    old = json.dumps(["a.h", "c.h"])
    new = json.dumps(["a.h", "b.h", "c.h"])  # b.h inserted between a.h and c.h
    assert not _header_sequence_is_additive_reorder_free(old, new, _ANY_NEW_HEADERS)


def test_header_sequence_additive_reorder_free_false_for_insertion_before_all():
    # Same risk, at the front: b.h's preprocessing context changes even
    # though it's still the same set/relative-order otherwise.
    old = json.dumps(["b.h", "c.h"])
    new = json.dumps(["a.h", "b.h", "c.h"])  # a.h inserted before everything
    assert not _header_sequence_is_additive_reorder_free(old, new, _ANY_NEW_HEADERS)


def test_header_sequence_additive_reorder_free_false_for_pure_reorder():
    old = json.dumps(["a.h", "b.h"])
    new = json.dumps(["b.h", "a.h"])  # same set, no growth, but reordered
    assert not _header_sequence_is_additive_reorder_free(old, new, _ANY_NEW_HEADERS)


def test_header_sequence_additive_reorder_free_false_for_reorder_entangled_with_growth():
    old = json.dumps(["a.h", "b.h"])
    new = json.dumps(["b.h", "a.h", "c.h"])
    assert not _header_sequence_is_additive_reorder_free(old, new, _ANY_NEW_HEADERS)


def test_header_sequence_additive_reorder_free_false_for_pure_removal():
    old = json.dumps(["a.h", "b.h", "c.h"])
    new = json.dumps(["a.h", "b.h"])
    assert not _header_sequence_is_additive_reorder_free(old, new, _ANY_NEW_HEADERS)


def test_header_sequence_additive_reorder_free_declines_on_single_header_sentinel():
    old = json.dumps(["<single-header>"])
    new = json.dumps(["a.h", "b.h"])
    assert not _header_sequence_is_additive_reorder_free(old, new, _ANY_NEW_HEADERS)
    # Same decline when the NEW side (rather than old) is the sentinel.
    assert not _header_sequence_is_additive_reorder_free(
        json.dumps(["a.h", "b.h"]), json.dumps(["<single-header>"]), _ANY_NEW_HEADERS
    )


def test_header_sequence_additive_reorder_free_declines_on_none():
    assert not _header_sequence_is_additive_reorder_free(
        None, json.dumps(["a.h"]), _ANY_NEW_HEADERS
    )
    assert not _header_sequence_is_additive_reorder_free(
        json.dumps(["a.h"]), None, _ANY_NEW_HEADERS
    )


def test_header_sequence_additive_reorder_free_declines_when_scope_new_headers_is_none():
    # Codex review, PR #641 follow-up (ninth P1): a shape-valid trailing
    # append must still decline when the caller has no verified set of
    # newly-added scope headers to check the appended entry against.
    old = json.dumps(["a.h", "b.h"])
    new = json.dumps(["a.h", "b.h", "c.h"])
    assert not _header_sequence_is_additive_reorder_free(old, new, None)


def test_header_sequence_additive_reorder_free_declines_when_appended_not_in_scope_new_headers():
    # Codex review, PR #641 follow-up (ninth P1): the exact scenario --
    # c.h is appended to the sequence, but the scope only grew by d.h (an
    # unrelated header), so this must decline even though the shape itself
    # (trailing append) is otherwise perfectly valid.
    old = json.dumps(["a.h", "b.h"])
    new = json.dumps(["a.h", "b.h", "c.h"])
    assert not _header_sequence_is_additive_reorder_free(old, new, frozenset({"d.h"}))


def test_header_sequence_additive_reorder_free_true_when_appended_matches_scope_new_headers():
    old = json.dumps(["a.h", "b.h"])
    new = json.dumps(["a.h", "b.h", "c.h"])
    assert _header_sequence_is_additive_reorder_free(old, new, frozenset({"c.h"}))


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
    assert _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_true_when_unchanged():
    same = json.dumps([_hdrs_slot(0, [("a.h", "a.h")])])
    assert _include_sequence_is_additive_owned_growth(same, same, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_false_for_removal():
    old = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h")])])
    new = json.dumps([_hdrs_slot(0, [("a.h", "a.h")])])
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_false_for_non_owned_slot_drift():
    # An "ext:" (external, non-owned) slot differing is real, unrelated
    # profile drift -- this carve-out has no business waiving it.
    old = json.dumps(["0:ext:" + "a" * 16])
    new = json.dumps(["0:ext:" + "b" * 16])
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_false_for_slot_count_change():
    old = json.dumps([_hdrs_slot(0, [("a.h", "a.h")])])
    new = json.dumps(
        [_hdrs_slot(0, [("a.h", "a.h")]), "1:ext:" + "a" * 16]
    )
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_false_for_single_header_sentinel():
    old = json.dumps(["0:hdrs:<single-header>"])
    new = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h")])])
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_declines_on_none():
    some = json.dumps([_hdrs_slot(0, [("a.h", "a.h")])])
    assert not _include_sequence_is_additive_owned_growth(None, some, _ANY_NEW_HEADERS)
    assert not _include_sequence_is_additive_owned_growth(some, None, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_true_when_one_slot_unchanged():
    # Multi-slot case: slot 0 (an external, non-owned dir) is byte-identical
    # on both sides; only slot 1 (the owned root) grows. The per-slot
    # equality short-circuit must let the unchanged slot through without
    # re-parsing it.
    old = json.dumps(["0:ext:" + "a" * 16, _hdrs_slot(1, [("a.h", "a.h")])])
    new = json.dumps(
        ["0:ext:" + "a" * 16, _hdrs_slot(1, [("a.h", "a.h"), ("b.h", "b.h")])]
    )
    assert _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_false_for_index_mismatch():
    # A malformed/reordered slot list (index labels don't line up
    # positionally) can never be safely verified.
    old = json.dumps(["0:hdrs:[[\"a.h\", \"a.h\"]]"])
    new = json.dumps(["1:hdrs:[[\"a.h\", \"a.h\"], [\"b.h\", \"b.h\"]]"])
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_false_for_non_positional_index():
    # Codex review, PR #641 follow-up (seventh P2): a slot index that is
    # IDENTICAL on both sides but not a real positional index (the
    # per-slot loop only checks old_idx == new_idx, never that the shared
    # index is actually valid) must still be declined -- otherwise a
    # fabricated slot label lets malformed evidence through as if it were
    # genuine additive owned-header growth.
    old = json.dumps(["bogus:hdrs:[[\"a.h\", \"a.h\"]]"])
    new = json.dumps(["bogus:hdrs:[[\"a.h\", \"a.h\"], [\"b.h\", \"b.h\"]]"])
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_true_with_unchanged_trailing_sys_bucket():
    # Codex review, PR #641 follow-up (tenth P1): the real production
    # construction appends an unnumbered trailing "sys:..." entry for any
    # depfile header outside every declared include root -- the seventh
    # P2 fix's _slot_indices_match_position required EVERY slot's prefix
    # to equal its position, which this "sys:" entry never can (it owns no
    # IncludeDir and thus no position). An unchanged system bucket must
    # not block an otherwise-legitimate owned-header addition.
    sys_bucket = "sys:" + "a" * 16
    old = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h")]), sys_bucket])
    new = json.dumps(
        [_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h"), ("c.h", "c.h")]), sys_bucket]
    )
    assert _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_false_when_sys_bucket_not_trailing():
    # A "sys:"-prefixed entry anywhere but the last position is not the
    # real production shape (the bucket is always appended last, if
    # present) -- must still be declined as an unverifiable position.
    old = json.dumps(["sys:" + "a" * 16, _hdrs_slot(1, [("a.h", "a.h")])])
    new = json.dumps(
        ["sys:" + "a" * 16, _hdrs_slot(1, [("a.h", "a.h"), ("b.h", "b.h")])]
    )
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_false_for_unchanged_delimiterless_slot():
    # Codex review, PR #641 follow-up (eleventh P2): a malformed slot with
    # NO ":" delimiter at all, e.g. the bare string "0", still passes
    # slot.partition(":")[0] == str(i) trivially ("0".partition(":")[0] is
    # "0" itself). If that slot happens to be byte-identical on both
    # sides, the per-slot loop's own equality short-circuit never
    # re-examines it, so it could ride alongside a genuinely-growing slot
    # 1 and still return additive-safe. Confirmed by direct repro before
    # any fix: this was accepted as safe growth.
    old = json.dumps(["0", _hdrs_slot(1, [("a.h", "a.h")])])
    new = json.dumps(["0", _hdrs_slot(1, [("a.h", "a.h"), ("b.h", "b.h")])])
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_declines_when_scope_new_headers_is_none():
    # Codex review, PR #641 follow-up (ninth P1): a shape-valid owned-pair
    # growth must still decline when the caller has no verified set of
    # newly-added scope headers to check the newly-owned pair against.
    old = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h")])])
    new = json.dumps(
        [_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h"), ("c.h", "c.h")])]
    )
    assert not _include_sequence_is_additive_owned_growth(old, new, None)


def test_include_sequence_additive_owned_growth_declines_when_new_pair_not_in_scope_new_headers():
    # Codex review, PR #641 follow-up (ninth P1): the exact scenario --
    # c.h is newly owned in the include_sequence slot, but the scope only
    # grew by d.h (an unrelated header), so this must decline even though
    # the shape itself (owned-pair superset growth) is otherwise valid.
    old = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h")])])
    new = json.dumps(
        [_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h"), ("c.h", "c.h")])]
    )
    assert not _include_sequence_is_additive_owned_growth(old, new, frozenset({"d.h"}))


def test_include_sequence_additive_owned_growth_true_when_new_pair_matches_scope_new_headers():
    old = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h")])])
    new = json.dumps(
        [_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h"), ("c.h", "c.h")])]
    )
    assert _include_sequence_is_additive_owned_growth(old, new, frozenset({"c.h"}))


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


def test_gate_header_sequence_carve_out_declines_when_only_public_header_dirs_grew(
    tmp_path,
):
    # Codex review, PR #641 follow-up (eighth P1): scope_fingerprint hashes
    # ALL of SCOPE_FIELD_KEYS together (headers AND public_header_dirs), so
    # an unrelated public_header_dirs addition alone -- with the declared
    # "headers" set completely unchanged -- makes scope_fingerprint differ
    # and trivially satisfies _scope_growth_corroborated's all(...) check
    # (an unchanged field is a same-value "superset" of itself). That let a
    # header_sequence growth get corroborated even though the "headers"
    # field the carve-out actually needs evidence about never moved at all
    # -- the exact silent-false-negative shape
    # test_gate_header_sequence_carve_out_declines_without_scope_corroboration
    # above already guards against, just reached via a different route
    # (public_header_dirs instead of scope_fingerprint being unchanged
    # entirely). x.h is declared identically on both sides (old via
    # --public-header, new via -H) so this is a real, previously-silent
    # false negative: a real removal inside x.h between old and new would
    # be invisible, not reported.
    a = _write(tmp_path / "v1" / "a.h", "int f(void);\n")
    b = _write(tmp_path / "v1" / "b.h", "int g(void);\n")
    x_old = _write(tmp_path / "v1" / "x.h", "int h(void);\n")
    a2 = _write(tmp_path / "v2" / "a.h", "int f(void);\n")
    b2 = _write(tmp_path / "v2" / "b.h", "int g(void);\n")
    x2 = _write(tmp_path / "v2" / "x.h", "int h(void);\n")
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    d3 = tmp_path / "d3"
    for d in (d1, d2, d3):
        d.mkdir(parents=True, exist_ok=True)
    old = _snap(
        compute_extraction_contract(
            declared_headers=[a, b],
            public_header_paths=[x_old],
            public_header_dirs=[d1, d2],
            l2_frontend_ran=True,
        )
    )
    new = _snap(
        compute_extraction_contract(
            declared_headers=[a2, b2, x2],
            public_header_dirs=[d1, d2, d3],
            l2_frontend_ran=True,
        )
    )
    assert old.contract.scope_fields["headers"] == new.contract.scope_fields["headers"]
    assert old.contract.scope_fingerprint != new.contract.scope_fingerprint
    assert not _scope_growth_corroborated(old.contract, new.contract)
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_header_sequence_carve_out_declines_when_appended_header_is_not_the_new_scope_header(
    tmp_path,
):
    # Codex review, PR #641 follow-up (ninth P1): scope_growth_corroborated
    # only proves the scope grew by SOME header, not that it grew by the
    # SAME header the sequence carve-out is waiving. Here the scope grows
    # by c.h (newly declared, never parsed at all), while the
    # header_sequence carve-out separately waives d.h being appended (d.h
    # was already declared identically on both sides via --public-header,
    # but only fed to the L2 frontend on the new side). Both look additive
    # in isolation, but neither corroborates the other: a real removal
    # inside d.h between old and new would still be invisible on the old
    # side, and c.h was never parsed on either side. Confirmed by direct
    # repro before any fix: this silently returned None (comparable).
    a = _write(tmp_path / "v1" / "a.h", "int f(void);\n")
    b = _write(tmp_path / "v1" / "b.h", "int g(void);\n")
    d_old = _write(tmp_path / "v1" / "d.h", "int dd(void);\n")
    a2 = _write(tmp_path / "v2" / "a.h", "int f(void);\n")
    b2 = _write(tmp_path / "v2" / "b.h", "int g(void);\n")
    d2 = _write(tmp_path / "v2" / "d.h", "int dd(void);\n")
    c2 = _write(tmp_path / "v2" / "c.h", "int cc(void);\n")
    old = _snap(
        compute_extraction_contract(
            declared_headers=[a, b],
            public_header_paths=[d_old],
            l2_frontend_ran=True,
        )
    )
    new = _snap(
        compute_extraction_contract(
            declared_headers=[a2, b2, d2],
            public_header_paths=[c2],
            l2_frontend_ran=True,
        )
    )
    assert old.contract.scope_fields["headers"] == json.dumps(["a.h", "b.h", "d.h"])
    assert new.contract.scope_fields["headers"] == json.dumps(["a.h", "b.h", "c.h", "d.h"])
    assert old.contract.profile_fields["header_sequence"] == json.dumps(["a.h", "b.h"])
    assert new.contract.profile_fields["header_sequence"] == json.dumps(["a.h", "b.h", "d.h"])
    assert _scope_growth_corroborated(old.contract, new.contract)
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_include_sequence_carve_out_allows_growth_alongside_unchanged_sys_bucket(
    tmp_path,
):
    # Codex review, PR #641 follow-up (tenth P1): a real depfile commonly
    # includes headers outside every declared include root (system
    # headers, the C standard library, ...), which compute_extraction_contract
    # buckets into an unnumbered trailing "sys:..." include_sequence
    # entry. _slot_indices_match_position (seventh P2 fix) required EVERY
    # slot's index to match its position, which this entry never can --
    # so a pure header addition with an unchanged system bucket wrongly
    # raised ProfileMismatchError. Confirmed by direct repro before any
    # fix: reproduced [a.h, b.h] -> [a.h, b.h, c.h] with the same system
    # header depfile-resolved on both sides.
    a = _write(tmp_path / "v1" / "a.h", "int f(void);\n")
    b = _write(tmp_path / "v1" / "b.h", "int g(void);\n")
    a2 = _write(tmp_path / "v2" / "a.h", "int f(void);\n")
    b2 = _write(tmp_path / "v2" / "b.h", "int g(void);\n")
    c2 = _write(tmp_path / "v2" / "c.h", "int h(void);\n")
    sys_h = _write(tmp_path / "sys" / "stdio.h", "void printf(void);\n")
    old_headers = [a, b]
    new_headers = [a2, b2, c2]
    old_inc_extra, _ = resolve_inferred_header_roots(old_headers, [])
    new_inc_extra, _ = resolve_inferred_header_roots(new_headers, [])
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            declared_headers=old_headers,
            declared_includes=[IncludeDir(p) for p in old_inc_extra],
            depfile_resolved_paths=[a, b, sys_h],
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            declared_headers=new_headers,
            declared_includes=[IncludeDir(p) for p in new_inc_extra],
            depfile_resolved_paths=[a2, b2, c2, sys_h],
        )
    )
    old_slots = json.loads(old.contract.profile_fields["include_sequence"])
    new_slots = json.loads(new.contract.profile_fields["include_sequence"])
    assert old_slots[-1] == new_slots[-1]
    assert old_slots[-1].startswith("sys:")
    assert check_contracts_comparable(old, new) is None  # must not raise either error


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


def test_gate_rejects_unknown_profile_delta_even_with_a_waived_recognized_one():
    # Codex review, PR #641 follow-up (P1): `differing` only ever iterates
    # PROFILE_FIELD_KEYS, so a contract carrying an extra field this build
    # doesn't recognize (a newer schema key) was invisible to `unexplained`
    # -- if it happened to co-occur with an otherwise-legitimate,
    # carve-out-waived delta (additive header_sequence growth,
    # corroborated by real scope growth), the unrecognized delta slipped
    # through entirely and the pair was wrongly reported comparable.
    old_fields = {
        "header_sequence": json.dumps(["a.h", "b.h"]),
        "future_field": "one",
    }
    new_fields = {
        "header_sequence": json.dumps(["a.h", "b.h", "c.h"]),
        "future_field": "two",
    }
    old_scope_fields = {
        "headers": json.dumps(["a.h", "b.h"]),
        "public_header_dirs": json.dumps([]),
    }
    new_scope_fields = {
        "headers": json.dumps(["a.h", "b.h", "c.h"]),
        "public_header_dirs": json.dumps([]),
    }
    old = _snap(
        ExtractionContract(
            profile_fingerprint="p-old",
            scope_fingerprint=_real_scope_fingerprint(old_scope_fields),
            profile_fields=old_fields,
            scope_fields=old_scope_fields,
        )
    )
    new = _snap(
        ExtractionContract(
            profile_fingerprint="p-new",
            scope_fingerprint=_real_scope_fingerprint(new_scope_fields),
            profile_fields=new_fields,
            scope_fields=new_scope_fields,
        )
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_rejects_unknown_profile_delta_alone_too():
    old = _snap(
        ExtractionContract(
            profile_fingerprint="p-old",
            scope_fingerprint=None,
            profile_fields={"future_field": "one"},
            scope_fields={},
        )
    )
    new = _snap(
        ExtractionContract(
            profile_fingerprint="p-new",
            scope_fingerprint=None,
            profile_fields={"future_field": "two"},
            scope_fields={},
        )
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


# ---------------------------------------------------------------------------
# Codex review, PR #641 follow-up (P2): carve-out helpers must decline, not
# crash, on malformed JSON in profile_fields/scope_fields
# ---------------------------------------------------------------------------


def test_scope_field_additive_superset_declines_on_malformed_json():
    assert not _scope_field_is_additive_superset("not-json", json.dumps(["a.h"]))
    assert not _scope_field_is_additive_superset(json.dumps(["a.h"]), "not-json")


def test_scope_field_additive_superset_declines_on_non_list_json():
    assert not _scope_field_is_additive_superset('{"a": 1}', json.dumps(["a.h"]))


def test_header_sequence_additive_reorder_free_declines_on_malformed_json():
    assert not _header_sequence_is_additive_reorder_free(
        "not-json", json.dumps(["a.h", "b.h"]), _ANY_NEW_HEADERS
    )
    assert not _header_sequence_is_additive_reorder_free(
        json.dumps(["a.h"]), "not-json", _ANY_NEW_HEADERS
    )


def test_include_sequence_additive_owned_growth_declines_on_malformed_outer_json():
    assert not _include_sequence_is_additive_owned_growth(
        "not-json", json.dumps([_hdrs_slot(0, [("a.h", "a.h")])]), _ANY_NEW_HEADERS
    )


def test_include_sequence_additive_owned_growth_declines_on_malformed_inner_json():
    old = json.dumps(["0:hdrs:not-json"])
    new = json.dumps([_hdrs_slot(0, [("a.h", "a.h"), ("b.h", "b.h")])])
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_gate_scope_carve_out_declines_instead_of_crashing_on_malformed_scope_field():
    # The gate-level end-to-end pin: a malformed scope_fields value must
    # surface as the ordinary ScopeMismatchError, not an unhandled
    # JSONDecodeError escaping check_contracts_comparable.
    old = _snap(
        ExtractionContract(
            profile_fingerprint=None,
            scope_fingerprint="s-old",
            profile_fields={},
            scope_fields={"headers": "not-json", "public_header_dirs": json.dumps([])},
        )
    )
    new = _snap(
        ExtractionContract(
            profile_fingerprint=None,
            scope_fingerprint="s-new",
            profile_fields={},
            scope_fields={
                "headers": json.dumps(["a.h", "b.h"]),
                "public_header_dirs": json.dumps([]),
            },
        )
    )
    with pytest.raises(ScopeMismatchError):
        check_contracts_comparable(old, new)


def test_gate_rejects_unknown_scope_delta_even_with_equal_known_fields():
    # Codex review, PR #641 follow-up (third P1): the scope carve-out's
    # `all(...)` only ever checks SCOPE_FIELD_KEYS -- so a contract
    # carrying an extra scope_fields key this build doesn't recognize (a
    # newer schema field) was invisible to it. If "headers"/
    # "public_header_dirs" happened to be equal (or additive), the whole
    # scope_fingerprint mismatch was wrongly waived without ever examining
    # the unrecognized field.
    old = _snap(
        ExtractionContract(
            profile_fingerprint=None,
            scope_fingerprint="s-old",
            profile_fields={},
            scope_fields={
                "headers": json.dumps(["a.h", "b.h"]),
                "public_header_dirs": json.dumps([]),
                "future_scope": "old",
            },
        )
    )
    new = _snap(
        ExtractionContract(
            profile_fingerprint=None,
            scope_fingerprint="s-new",
            profile_fields={},
            scope_fields={
                "headers": json.dumps(["a.h", "b.h"]),
                "public_header_dirs": json.dumps([]),
                "future_scope": "new",
            },
        )
    )
    with pytest.raises(ScopeMismatchError):
        check_contracts_comparable(old, new)


def test_gate_rejects_unknown_scope_delta_even_with_additive_known_fields():
    # Same as above, but with headers growing additively -- the unknown
    # field must still block the carve-out even when the known fields
    # alone would have been waived.
    old = _snap(
        ExtractionContract(
            profile_fingerprint=None,
            scope_fingerprint="s-old",
            profile_fields={},
            scope_fields={
                "headers": json.dumps(["a.h", "b.h"]),
                "public_header_dirs": json.dumps([]),
                "future_scope": "old",
            },
        )
    )
    new = _snap(
        ExtractionContract(
            profile_fingerprint=None,
            scope_fingerprint="s-new",
            profile_fields={},
            scope_fields={
                "headers": json.dumps(["a.h", "b.h", "c.h"]),
                "public_header_dirs": json.dumps([]),
                "future_scope": "new",
            },
        )
    )
    with pytest.raises(ScopeMismatchError):
        check_contracts_comparable(old, new)


def test_scope_field_additive_superset_declines_on_non_string_list_members():
    # Codex review, PR #641 follow-up (second P2): valid JSON can decode to
    # a list whose members aren't plain strings (e.g. a dict) -- downstream
    # `in _SCOPE_SINGLE_ENTRY_SENTINELS`/`set(...)` checks require hashable
    # strings, so this must decline rather than raise TypeError.
    assert not _scope_field_is_additive_superset(
        json.dumps([{}]), json.dumps(["a.h"])
    )
    assert not _scope_field_is_additive_superset(
        json.dumps(["a.h"]), json.dumps([{}])
    )


def test_header_sequence_additive_reorder_free_declines_on_non_string_list_members():
    assert not _header_sequence_is_additive_reorder_free(
        json.dumps([{}]), json.dumps(["a.h", "b.h"]), _ANY_NEW_HEADERS
    )


def test_include_sequence_additive_owned_growth_declines_on_non_string_outer_members():
    assert not _include_sequence_is_additive_owned_growth(
        json.dumps([{}]), json.dumps([_hdrs_slot(0, [("a.h", "a.h")])]), _ANY_NEW_HEADERS
    )


def test_gate_scope_carve_out_declines_instead_of_crashing_on_non_string_scope_field():
    old = _snap(
        ExtractionContract(
            profile_fingerprint=None,
            scope_fingerprint="s-old",
            profile_fields={},
            scope_fields={
                "headers": json.dumps([{}]),
                "public_header_dirs": json.dumps([]),
            },
        )
    )
    new = _snap(
        ExtractionContract(
            profile_fingerprint=None,
            scope_fingerprint="s-new",
            profile_fields={},
            scope_fields={
                "headers": json.dumps(["a.h", "b.h"]),
                "public_header_dirs": json.dumps([]),
            },
        )
    )
    with pytest.raises(ScopeMismatchError):
        check_contracts_comparable(old, new)


def test_include_sequence_additive_owned_growth_declines_on_malformed_pair_member():
    # Codex review, PR #641 follow-up (third P2): a bare string like "xx" is
    # itself iterable, so `tuple("xx")` silently succeeds as `("x", "x")`
    # instead of raising -- old_owned's one real pair happens to equal what
    # "xx" decodes to, so without validation new_owned >= old_owned is
    # wrongly True (the malformed member masquerades as the exact pair
    # already owned, not evidence of anything new) and the carve-out
    # wrongly accepts this as additive growth.
    old = json.dumps([_hdrs_slot(0, [("x", "x")])])
    new = json.dumps(["0:hdrs:" + json.dumps(["xx"])])
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_include_sequence_additive_owned_growth_declines_on_wrong_arity_pair():
    # A genuinely new, valid pair growing alongside a malformed (3-element)
    # one: new_owned's valid pair alone already satisfies the superset
    # check against old_owned, so without validating each pair's arity the
    # malformed third element is silently ignored rather than treated as
    # untrustworthy evidence.
    old = json.dumps([_hdrs_slot(0, [("a.h", "a.h")])])
    new = json.dumps(
        [
            "0:hdrs:"
            + json.dumps([["a.h", "a.h"], ["b.h", "b.h", "extra"]])
        ]
    )
    assert not _include_sequence_is_additive_owned_growth(old, new, _ANY_NEW_HEADERS)


def test_gate_rejects_opaque_scope_mismatch_with_no_recognized_differing_field():
    # Codex review, PR #641 follow-up (fourth P1): the scope-side
    # equivalent of the opaque profile-fingerprint mismatch rejected
    # elsewhere -- a deserialized/externally-constructed contract can carry
    # a scope_fingerprint that doesn't match what this version would
    # recompute from scope_fields. If every recognized SCOPE_FIELD_KEYS
    # field is identical between old and new, nothing here explains the
    # differing fingerprint at all, so this must raise rather than treat
    # "nothing changed" as "verified safe."
    old = _snap(
        ExtractionContract(
            profile_fingerprint=None,
            scope_fingerprint="s-old",
            profile_fields={},
            scope_fields={
                "headers": json.dumps(["a.h", "b.h"]),
                "public_header_dirs": json.dumps([]),
            },
        )
    )
    new = _snap(
        ExtractionContract(
            profile_fingerprint=None,
            scope_fingerprint="s-new",
            profile_fields={},
            scope_fields={
                "headers": json.dumps(["a.h", "b.h"]),
                "public_header_dirs": json.dumps([]),
            },
        )
    )
    with pytest.raises(ScopeMismatchError):
        check_contracts_comparable(old, new)


def test_gate_rejects_opaque_scope_mismatch_in_diagnostic_mode_too():
    old = _snap(
        ExtractionContract(
            profile_fingerprint=None,
            scope_fingerprint="s-old",
            profile_fields={},
            scope_fields={
                "headers": json.dumps(["a.h", "b.h"]),
                "public_header_dirs": json.dumps([]),
            },
        )
    )
    new = _snap(
        ExtractionContract(
            profile_fingerprint=None,
            scope_fingerprint="s-new",
            profile_fields={},
            scope_fields={
                "headers": json.dumps(["a.h", "b.h"]),
                "public_header_dirs": json.dumps([]),
            },
        )
    )
    result = check_contracts_comparable(old, new, diagnostic=True)
    assert isinstance(result, ComparabilityMismatch)
    assert result.kind == "scope"


def test_gate_rejects_unknown_profile_delta_present_empty_vs_absent():
    # Codex review, PR #641 follow-up (fifth P1): `.get(k, "")` conflates
    # "key absent entirely" with "key present with an empty string value"
    # -- a newer-schema field added on only one side with an empty value
    # (`future_profile: ""` vs. no key at all) compared "" == "" and was
    # wrongly invisible to unknown_differing, even when combined with an
    # otherwise-legitimate, corroborated header_sequence growth.
    old_fields = {
        "header_sequence": json.dumps(["a.h", "b.h"]),
        "future_profile": "",
    }
    new_fields = {
        "header_sequence": json.dumps(["a.h", "b.h", "c.h"]),
        # future_profile absent entirely on this side
    }
    old_scope_fields = {
        "headers": json.dumps(["a.h", "b.h"]),
        "public_header_dirs": json.dumps([]),
    }
    new_scope_fields = {
        "headers": json.dumps(["a.h", "b.h", "c.h"]),
        "public_header_dirs": json.dumps([]),
    }
    old = _snap(
        ExtractionContract(
            profile_fingerprint="p-old",
            scope_fingerprint=_real_scope_fingerprint(old_scope_fields),
            profile_fields=old_fields,
            scope_fields=old_scope_fields,
        )
    )
    new = _snap(
        ExtractionContract(
            profile_fingerprint="p-new",
            scope_fingerprint=_real_scope_fingerprint(new_scope_fields),
            profile_fields=new_fields,
            scope_fields=new_scope_fields,
        )
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_rejects_unknown_scope_delta_present_empty_vs_absent():
    # headers grows additively (a genuinely recognized, otherwise-waivable
    # delta) so scope_differing is non-empty and the opaque-mismatch check
    # alone wouldn't catch this -- isolating the empty-vs-absent gap in
    # scope_unknown_differing specifically.
    old = _snap(
        ExtractionContract(
            profile_fingerprint=None,
            scope_fingerprint="s-old",
            profile_fields={},
            scope_fields={
                "headers": json.dumps(["a.h", "b.h"]),
                "public_header_dirs": json.dumps([]),
                "future_scope": "",
            },
        )
    )
    new = _snap(
        ExtractionContract(
            profile_fingerprint=None,
            scope_fingerprint="s-new",
            profile_fields={},
            scope_fields={
                "headers": json.dumps(["a.h", "b.h", "c.h"]),
                "public_header_dirs": json.dumps([]),
                # future_scope absent entirely on this side
            },
        )
    )
    with pytest.raises(ScopeMismatchError):
        check_contracts_comparable(old, new)


def test_fingerprint_matches_fields_true_for_real_computation():
    fields = {"headers": json.dumps(["a.h", "b.h"]), "public_header_dirs": json.dumps([])}
    real = _sha256_of(*[fields[k] for k in SCOPE_FIELD_KEYS])
    assert _fingerprint_matches_fields(real, fields, SCOPE_FIELD_KEYS)


def test_fingerprint_matches_fields_false_for_arbitrary_string():
    fields = {"headers": json.dumps(["a.h", "b.h"]), "public_header_dirs": json.dumps([])}
    assert not _fingerprint_matches_fields("not-a-real-hash", fields, SCOPE_FIELD_KEYS)


def test_gate_rejects_scope_mismatch_when_fingerprint_does_not_match_own_fields():
    # Codex review, PR #641 follow-up (sixth P1): every carve-out reasons
    # entirely from scope_fields ("this recognized field grew additively,
    # so the mismatch is explained") -- but that only holds if the stored
    # fingerprint was genuinely computed from those fields. Confirmed by
    # direct repro before any fix: two arbitrary, unrelated fingerprint
    # strings with headers genuinely growing additively still made
    # check_contracts_comparable return None.
    old_scope_fields = {
        "headers": json.dumps(["a.h", "b.h"]),
        "public_header_dirs": json.dumps([]),
    }
    new_scope_fields = {
        "headers": json.dumps(["a.h", "b.h", "c.h"]),
        "public_header_dirs": json.dumps([]),
    }
    old = _snap(
        ExtractionContract(
            profile_fingerprint=None,
            scope_fingerprint="s-old",  # arbitrary, NOT a real hash of old_scope_fields
            profile_fields={},
            scope_fields=old_scope_fields,
        )
    )
    new = _snap(
        ExtractionContract(
            profile_fingerprint=None,
            scope_fingerprint="s-new",  # arbitrary, NOT a real hash of new_scope_fields
            profile_fields={},
            scope_fields=new_scope_fields,
        )
    )
    with pytest.raises(ScopeMismatchError):
        check_contracts_comparable(old, new)


def test_gate_rejects_scope_mismatch_unverified_fingerprint_in_diagnostic_mode_too():
    old_scope_fields = {
        "headers": json.dumps(["a.h", "b.h"]),
        "public_header_dirs": json.dumps([]),
    }
    new_scope_fields = {
        "headers": json.dumps(["a.h", "b.h", "c.h"]),
        "public_header_dirs": json.dumps([]),
    }
    old = _snap(
        ExtractionContract(
            profile_fingerprint=None,
            scope_fingerprint="s-old",
            profile_fields={},
            scope_fields=old_scope_fields,
        )
    )
    new = _snap(
        ExtractionContract(
            profile_fingerprint=None,
            scope_fingerprint="s-new",
            profile_fields={},
            scope_fields=new_scope_fields,
        )
    )
    result = check_contracts_comparable(old, new, diagnostic=True)
    assert isinstance(result, ComparabilityMismatch)
    assert result.kind == "scope"


def test_gate_rejects_profile_mismatch_when_fingerprint_does_not_match_own_fields():
    old_fields = {"header_sequence": json.dumps(["a.h", "b.h"])}
    new_fields = {"header_sequence": json.dumps(["a.h", "b.h", "c.h"])}
    old_scope_fields = {
        "headers": json.dumps(["a.h", "b.h"]),
        "public_header_dirs": json.dumps([]),
    }
    new_scope_fields = {
        "headers": json.dumps(["a.h", "b.h", "c.h"]),
        "public_header_dirs": json.dumps([]),
    }
    old = _snap(
        ExtractionContract(
            profile_fingerprint="p-old",  # arbitrary, NOT a real hash of old_fields
            scope_fingerprint=_real_scope_fingerprint(old_scope_fields),
            profile_fields=old_fields,
            scope_fields=old_scope_fields,
        )
    )
    new = _snap(
        ExtractionContract(
            profile_fingerprint="p-new",  # arbitrary, NOT a real hash of new_fields
            scope_fingerprint=_real_scope_fingerprint(new_scope_fields),
            profile_fields=new_fields,
            scope_fields=new_scope_fields,
        )
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_rejects_profile_mismatch_unverified_fingerprint_in_diagnostic_mode_too():
    old_fields = {"header_sequence": json.dumps(["a.h", "b.h"])}
    new_fields = {"header_sequence": json.dumps(["a.h", "b.h", "c.h"])}
    old_scope_fields = {
        "headers": json.dumps(["a.h", "b.h"]),
        "public_header_dirs": json.dumps([]),
    }
    new_scope_fields = {
        "headers": json.dumps(["a.h", "b.h", "c.h"]),
        "public_header_dirs": json.dumps([]),
    }
    old = _snap(
        ExtractionContract(
            profile_fingerprint="p-old",
            scope_fingerprint=_real_scope_fingerprint(old_scope_fields),
            profile_fields=old_fields,
            scope_fields=old_scope_fields,
        )
    )
    new = _snap(
        ExtractionContract(
            profile_fingerprint="p-new",
            scope_fingerprint=_real_scope_fingerprint(new_scope_fields),
            profile_fields=new_fields,
            scope_fields=new_scope_fields,
        )
    )
    result = check_contracts_comparable(old, new, diagnostic=True)
    assert isinstance(result, ComparabilityMismatch)
    assert result.kind == "profile"
