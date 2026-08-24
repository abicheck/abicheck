"""Primitive-level property tests for the confirmed-public-header record
seed's own load-bearing primitives, `surface._record_nested_in_known_record`
and `surface._record_is_confirmed_public_seed`.

Split out of `test_surface.py` purely to keep that file under the
AI-readiness file-size cap (it is otherwise part of the same test area and
uses the same `_rec` helper convention).

The two helpers here are the load-bearing primitives behind the "confirmed-
public-header-origin record" seed in `compute_public_surface` -- an earlier
draft of that seed shipped two real gaps (promoting a `GENERATED`-origin
type, and promoting a private/protected nested class), each caught only
after landing, via automated review rather than a hand-written test. Per
this repo's own "Primitive-level property tests" convention (AGENTS.md),
these test the primitives' actual contracts as invariants, decoupled from
the one `compute_public_surface` caller and from any one example input,
rather than only pinning the two reported cases. A hand-written example
test only forecloses the specific input it names -- these are what would
have caught either gap *before* it needed an external review to find.
"""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from abicheck.model import RecordType, ScopeOrigin
from abicheck.surface import (
    _record_is_confirmed_public_seed,
    _record_nested_in_known_record,
)


def _rec(
    name: str,
    *,
    origin: ScopeOrigin = ScopeOrigin.UNKNOWN,
    source_header: str | None = None,
    qualified_name: str | None = None,
) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        size_bits=64,
        origin=origin,
        source_header=source_header,
        qualified_name=qualified_name,
    )


_IDENT = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu"), max_codepoint=0x7A),
    min_size=1,
    max_size=8,
).filter(lambda s: "::" not in s)


class TestRecordNestedInKnownRecordProperties:
    """`_record_nested_in_known_record` looks at exactly the ONE immediate
    enclosing scope (`qname.rsplit("::", 1)[0]`) -- these properties state
    that contract for arbitrary identifiers/nesting depth, not just one
    hand-picked `Outer::Secret` example."""

    @given(leaf=_IDENT)
    def test_a_bare_name_is_never_nested(self, leaf: str) -> None:
        """No `"::"` at all means no enclosing scope to check -- true
        regardless of how large or unrelated `record_by_name` is."""
        record_by_name = {leaf: [_rec(leaf)]}
        assert _record_nested_in_known_record(leaf, record_by_name) is False

    @given(owner=_IDENT, leaf=_IDENT)
    def test_owner_registered_as_a_record_means_nested(
        self, owner: str, leaf: str
    ) -> None:
        """Appending any leaf identifier onto a qualified name whose owner
        prefix is itself a key in `record_by_name` is ALWAYS flagged nested
        -- for an arbitrary owner spelling and an arbitrary leaf, not just
        the one `Outer`/`Secret` pair a hand-written example test hard-codes."""
        qname = f"{owner}::{leaf}"
        record_by_name = {owner: [_rec(owner)]}
        assert _record_nested_in_known_record(qname, record_by_name) is True

    @given(owner=_IDENT, leaf=_IDENT)
    def test_unregistered_owner_means_not_nested(self, owner: str, leaf: str) -> None:
        """The inverse: when the immediate owner prefix is NOT a key in
        `record_by_name` (a namespace, not a known record), the type is
        never flagged nested -- regardless of what else `record_by_name`
        contains, as long as it excludes this exact owner spelling."""
        qname = f"{owner}::{leaf}"
        record_by_name: dict[str, list[RecordType]] = {}
        assert _record_nested_in_known_record(qname, record_by_name) is False

    @given(segments=st.lists(_IDENT, min_size=3, max_size=6))
    def test_only_the_immediate_parent_is_consulted(self, segments: list[str]) -> None:
        """Arbitrary-depth chain (``a::b::c::...``): registering every
        ancestor EXCEPT the immediate parent as a known record must still
        read as "not nested" -- a deeper-but-not-immediate record ancestor
        does not count. Generalizes the two-level `Outer::Secret` example to
        an arbitrary depth."""
        qname = "::".join(segments)
        immediate_parent = "::".join(segments[:-1])
        all_but_immediate = {
            "::".join(segments[:k]): [_rec("x")] for k in range(1, len(segments) - 1)
        }
        assert immediate_parent not in all_but_immediate
        assert _record_nested_in_known_record(qname, all_but_immediate) is False
        # Registering the immediate parent too now flips it to nested.
        all_but_immediate[immediate_parent] = [_rec("x")]
        assert _record_nested_in_known_record(qname, all_but_immediate) is True


class TestRecordIsConfirmedPublicSeedExhaustive:
    """`_record_is_confirmed_public_seed` is a finite boolean predicate over
    five independent conditions (source_header presence, origin, a truthy
    qualified_name, internal-namespace membership, nested-in-a-record) --
    small enough to check EVERY combination exhaustively rather than sample
    it, so a future edit that silently drops or inverts one condition fails
    immediately instead of needing a new bug report to notice: each of the
    five conditions here was independently the subject of a real,
    previously-shipped gap (the qualified_name condition specifically
    closes the function-local-record gap -- see
    ``TestFunctionLocalRecordExcluded`` below for why ``None`` is
    indistinguishable from a legitimate global-scope record without it)."""

    _ORIGINS = (
        ScopeOrigin.PUBLIC_HEADER,
        ScopeOrigin.PRIVATE_HEADER,
        ScopeOrigin.SYSTEM_HEADER,
        ScopeOrigin.GENERATED,
        ScopeOrigin.UNKNOWN,
        ScopeOrigin.EXPORT_ONLY,
    )

    @pytest.mark.parametrize("origin", _ORIGINS)
    @pytest.mark.parametrize("has_header", [True, False])
    @pytest.mark.parametrize("has_qualified_name", [True, False])
    @pytest.mark.parametrize("internal_namespace", [True, False])
    @pytest.mark.parametrize("nested", [True, False])
    def test_exhaustive_truth_table(
        self, origin, has_header, has_qualified_name, internal_namespace, nested
    ) -> None:
        name = "ns::detail::Type" if internal_namespace else "ns::Type"
        rec = _rec(
            "Type",
            origin=origin,
            source_header="a.h" if has_header else None,
            qualified_name=name if has_qualified_name else None,
        )
        owner_key = "ns::detail" if internal_namespace else "ns"
        record_by_name = {owner_key: [_rec("owner")]} if nested else {}
        expected = (
            has_header
            and origin is ScopeOrigin.PUBLIC_HEADER
            and has_qualified_name
            and not internal_namespace
            and not nested
        )
        assert _record_is_confirmed_public_seed(rec, record_by_name) is expected


class TestFunctionLocalRecordExcluded:
    """Regression for the third review finding (Codex, fresh evidence): a
    named class local to a function/method body (e.g. a helper struct
    declared inside an inline function's body) is retained by castxml's
    `parse_types()` with no scope-kind filter at all, while
    `_qualified_type_name()`'s ancestor walk stops the instant it crosses a
    non-Namespace/Struct/Class/Union context -- so a function-local record's
    `qualified_name` is `None`, the identical value a genuinely global-scope
    (no enclosing namespace) record also produces. Requiring
    `qualified_name` truthy therefore excludes BOTH -- a real, accepted
    trade-off (see `_record_is_confirmed_public_seed`'s own docstring),
    since a header-only library's public utility types are essentially
    always namespaced in practice."""

    def test_qualified_name_none_is_never_seeded_regardless_of_bare_name(self) -> None:
        # `rec.name` alone ("Local") looks exactly like an ordinary,
        # unnamespaced public type -- only `qualified_name is None` signals
        # the ambiguous (global-scope-or-function-local) case.
        rec = _rec(
            "Local",
            origin=ScopeOrigin.PUBLIC_HEADER,
            source_header="a.h",
            qualified_name=None,
        )
        assert _record_is_confirmed_public_seed(rec, {}) is False
