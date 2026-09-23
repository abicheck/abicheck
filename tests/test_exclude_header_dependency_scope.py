"""``--exclude-header`` scopes a matched header out like a toolchain header,
including when it is only reached through another header's ``#include``.

The integration twin (real headers, both backends) lives in
``test_checkout_root_independence_integration.py``; this file states the
contract on hand-built snapshots so it runs in the fast lane.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.extract.header_exclusions import (
    apply_header_exclusions,
    header_matches_exclusion,
    scoping_header_predicate,
)
from abicheck.model import AbiSnapshot, Function, Param, RecordType, Visibility
from abicheck.workflows.header_exclusion_audit import record_achieved_header_exclusions

_SEGMENTS = st.sampled_from(
    ["w", "old", "new", "include", "eve-1", "eve", "detail", "lib", "a b"]
)
_FILES = st.sampled_from(["kumi.hpp", "api.hpp", "fftw3.h", "x.h"])
_PATTERNS = st.sampled_from(
    ["kumi.hpp", "*/eve-*/*", "**/detail/*", "fftw*.h", "*.hpp", "nomatch"]
)


@settings(max_examples=300, deadline=None)
@given(
    st.lists(_SEGMENTS, min_size=1, max_size=5), _FILES, st.lists(_PATTERNS, max_size=3)
)
def test_matcher_agrees_with_the_parse_root_filter(dirs, name, patterns) -> None:
    """One rule for both places a pattern is applied: a header is dropped
    from the parse roots exactly when the scope treats it as excluded."""
    path = "/" + "/".join(dirs) + "/" + name
    filtered_out = not apply_header_exclusions([Path(path)], patterns)
    assert header_matches_exclusion(path, patterns) is filtered_out


def test_predicate_without_patterns_is_the_dependency_predicate() -> None:
    is_dep = scoping_header_predicate(["/w/include/lib/api.hpp"], [])
    assert is_dep("/usr/include/stdio.h") is True
    assert is_dep("/w/include/eve-1/eve/detail/kumi.hpp") is False


def test_predicate_widens_to_matching_headers_only() -> None:
    is_dep = scoping_header_predicate(["/w/include/lib/api.hpp"], ["*/eve-*/*"])
    assert is_dep("/w/include/eve-1/eve/detail/kumi.hpp") is True
    assert is_dep("/w/include/lib/api.hpp") is False
    assert is_dep("/usr/include/stdio.h") is True
    assert is_dep(None) is False


_API = "/w/include/lib/api.hpp"
_KUMI = "/w/include/eve-1/eve/detail/kumi.hpp"


def _snapshot(scope: str | None = "filtered") -> AbiSnapshot:
    return AbiSnapshot(
        library="libapi.so",
        version="1",
        functions=[
            Function(
                name="lib::take",
                mangled="_ZN3lib4takeERKN4kumi8foldableE",
                return_type="int",
                params=[Param(name="f", type="const kumi::foldable &")],
                visibility=Visibility.PUBLIC,
                source_header=_API,
            ),
            Function(
                name="kumi::extra_helper",
                mangled="_ZN4kumi12extra_helperEv",
                return_type="int",
                visibility=Visibility.PUBLIC,
                source_header=_KUMI,
            ),
        ],
        types=[
            RecordType(
                name="kumi::foldable", kind="struct", size_bits=64, source_header=_KUMI
            ),
            RecordType(
                name="kumi::ExtraInternal",
                kind="struct",
                size_bits=32,
                source_header=_KUMI,
            ),
        ],
        from_headers=True,
        dependency_scope=scope,
    )


def _names(snap: AbiSnapshot) -> set[str]:
    return {f.name for f in snap.functions} | {t.name for t in snap.types}


def test_transitively_included_excluded_header_is_scoped_out() -> None:
    out = record_achieved_header_exclusions(
        _snapshot(), [Path(_API)], ["*/eve-*/*"], extracted_now=True
    )
    # The pattern matched no parse root, only an included header -- it still
    # counts as achieved, and is recorded.
    assert out.excluded_header_patterns == ("*/eve-*/*",)
    names = _names(out)
    assert "kumi::extra_helper" not in names
    assert "kumi::ExtraInternal" not in names
    # Referenced from the library's own public signature: kept.
    assert {"lib::take", "kumi::foldable"} <= names


@pytest.mark.parametrize(
    ("scope", "extracted_now"),
    [("full", True), ("filtered", False), (None, True)],
)
def test_nothing_is_dropped_outside_a_fresh_filtered_dump(scope, extracted_now) -> None:
    """``--include-system-declarations`` keeps toolchain headers, so it keeps
    excluded ones too; and a loaded snapshot keeps its own recorded surface."""
    snap = _snapshot(scope)
    out = record_achieved_header_exclusions(
        snap, [Path(_API)], ["*/eve-*/*"], extracted_now=extracted_now
    )
    assert _names(out) == _names(snap)


def test_unmatched_pattern_changes_nothing() -> None:
    snap = _snapshot()
    out = record_achieved_header_exclusions(
        snap, [Path(_API)], ["nomatch"], extracted_now=True
    )
    assert out.excluded_header_patterns == ()
    assert _names(out) == _names(snap)
