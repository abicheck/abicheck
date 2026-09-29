"""A pattern one side *achieved* but the other side had nothing to match.

Snapshots record the ``--exclude-header`` patterns that actually removed a
header, not the shared request. oneDNN validation: ``--exclude-header
"*_ze*"`` on both sides, the old release has no ``dnnl_ze.h``, so the
pattern lands on NEW only -- and the comparability gate refused the pair
(exit 16) after both sides had been parsed. The asymmetry is harmless
exactly when the un-narrowed side parsed no header the pattern matches.
"""

from __future__ import annotations

import itertools
from fnmatch import fnmatch

import pytest

from abicheck.comparability import ScopeMismatchError, check_contracts_comparable
from abicheck.extract.header_exclusions import exclusion_asymmetry_reason_for
from abicheck.model import AbiSnapshot, Function, Visibility


def _snap(headers: list[str], patterns: tuple[str, ...] = ()) -> AbiSnapshot:
    snap = AbiSnapshot(
        library="libdnnl.so.3",
        version="1",
        functions=[
            Function(
                name=f"f{i}",
                mangled=f"f{i}",
                return_type="int",
                visibility=Visibility.PUBLIC,
                source_header=h,
            )
            for i, h in enumerate(headers)
        ],
    )
    if patterns:
        snap.excluded_header_patterns = patterns
        snap.excluded_header_matching = "glob"
    return snap


def test_onednn_shape_is_comparable():
    old = _snap(["/o/include/dnnl.h", "/o/include/dnnl_sycl.h"])
    new = _snap(["/n/include/dnnl.h", "/n/include/dnnl_sycl.h"], ("*_ze*",))
    assert exclusion_asymmetry_reason_for(old, new) is None
    assert exclusion_asymmetry_reason_for(new, old) is None
    check_contracts_comparable(old, new)  # does not raise


def test_refused_when_other_side_parsed_a_matching_header():
    old = _snap(["/o/include/dnnl.h", "/o/include/dnnl_ze.h"])
    new = _snap(["/n/include/dnnl.h"], ("*_ze*",))
    assert exclusion_asymmetry_reason_for(old, new) is not None
    with pytest.raises(ScopeMismatchError):
        check_contracts_comparable(old, new)


def test_refused_when_other_side_has_no_header_evidence():
    old = AbiSnapshot(library="libdnnl.so.3", version="1")
    new = _snap(["/n/include/dnnl.h"], ("*_ze*",))
    assert exclusion_asymmetry_reason_for(old, new) is not None


def test_refused_under_non_glob_rule():
    old = _snap(["/o/include/dnnl.h"])
    new = _snap(["/n/include/dnnl.h"], ("dnnl_ze.h",))
    new.excluded_header_matching = "exact"
    assert exclusion_asymmetry_reason_for(old, new) is not None


_HEADERS = ["/p/a.h", "/p/b_ze.h", "/p/sub/c.hpp"]
_PATTERNS = ["*_ze*", "c.hpp", "sub/*", "zz.h"]


def _oracle_ok(old_h, old_p, new_h, new_p) -> bool:
    """Independent statement: comparable iff every pattern recorded on one
    side only matches no header the other side parsed (bare name, full path,
    or any path suffix), and the side lacking it has header evidence."""

    def matches(h: str, p: str) -> bool:
        parts = h.split("/")
        suffixes = ["/".join(parts[i:]) for i in range(len(parts))]
        return any(fnmatch(s, p) for s in suffixes)

    for extra, other in (
        (set(new_p) - set(old_p), old_h),
        (set(old_p) - set(new_p), new_h),
    ):
        if extra and not other:
            return False
        if any(matches(h, p) for p in extra for h in other):
            return False
    return True


def test_exhaustive_small_domain_matches_oracle():
    subsets_h = [
        list(c)
        for r in range(len(_HEADERS) + 1)
        for c in itertools.combinations(_HEADERS, r)
    ]
    subsets_p = [
        tuple(c) for r in range(3) for c in itertools.combinations(_PATTERNS, r)
    ]
    disagreements = []
    accepted = refused = 0
    for oh, op, nh, np_ in itertools.product(
        subsets_h, subsets_p, subsets_h, subsets_p
    ):
        got = exclusion_asymmetry_reason_for(_snap(oh, op), _snap(nh, np_)) is None
        want = _oracle_ok(oh, op, nh, np_)
        accepted += got
        refused += not got
        if got != want:
            disagreements.append((oh, op, nh, np_, got))
    assert not disagreements, disagreements[:10]
    # Vacuity guard: the domain exercises both outcomes.
    assert accepted and refused
