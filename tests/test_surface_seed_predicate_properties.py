"""Primitive-level property tests for the confirmed-public-header record
seed's own load-bearing primitives, `surface._record_nested_in_known_record`
and `surface._record_is_confirmed_public_seed`.

Split out of `test_surface.py` purely to keep that file under the
AI-readiness file-size cap (it is otherwise part of the same test area and
uses the same `_rec` helper convention).

The two helpers here are the load-bearing primitives behind the "confirmed-
public-header-origin record" seed in `compute_public_surface` -- an earlier
draft of that seed shipped several real gaps (promoting a `GENERATED`-origin
type, promoting a private/protected nested class, promoting a function-local
record, and -- the most recent -- misreading an unrelated record's own
trailing-segment *alias* as evidence that an unrelated namespace is really a
class), each caught only after landing, via automated review rather than a
hand-written test. Per this repo's own "Primitive-level property tests"
convention (AGENTS.md), these test the primitives' actual contracts as
invariants, decoupled from the one `compute_public_surface` caller and from
any one example input, rather than only pinning the reported cases. A
hand-written example test only forecloses the specific input it names --
these are what would have caught each gap *before* it needed an external
review to find.
"""

from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.model import AbiSnapshot, Function, RecordType, ScopeOrigin
from abicheck.policy.public_surface_closure import (
    _record_exact_identities,
    _record_is_confirmed_public_seed,
    _record_nested_in_known_record,
)
from abicheck.surface import classify_change_surface, compute_public_surface


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


def _fn(name, origin=ScopeOrigin.UNKNOWN):
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}",
        return_type="void",
        params=[],
        origin=origin,
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
    hand-picked `Outer::Secret` example. *record_identities* here is always
    the exact-identity `set[str]` `_record_exact_identities` produces --
    never the ambiguous, alias-including `record_by_name` dict (see
    `TestExactIdentitiesRejectsAliasCollisions` below for why that
    distinction is itself load-bearing)."""

    @given(leaf=_IDENT)
    def test_a_bare_name_is_never_nested(self, leaf: str) -> None:
        """No `"::"` at all means no enclosing scope to check -- true
        regardless of how large or unrelated `record_identities` is."""
        record_identities = {leaf}
        assert _record_nested_in_known_record(leaf, record_identities) is False

    @given(owner=_IDENT, leaf=_IDENT)
    def test_owner_registered_as_a_record_means_nested(
        self, owner: str, leaf: str
    ) -> None:
        """Appending any leaf identifier onto a qualified name whose owner
        prefix is itself in `record_identities` is ALWAYS flagged nested --
        for an arbitrary owner spelling and an arbitrary leaf, not just the
        one `Outer`/`Secret` pair a hand-written example test hard-codes."""
        qname = f"{owner}::{leaf}"
        record_identities = {owner}
        assert _record_nested_in_known_record(qname, record_identities) is True

    @given(owner=_IDENT, leaf=_IDENT)
    def test_unregistered_owner_means_not_nested(self, owner: str, leaf: str) -> None:
        """The inverse: when the immediate owner prefix is NOT in
        `record_identities` (a namespace, not a known record), the type is
        never flagged nested -- regardless of what else `record_identities`
        contains, as long as it excludes this exact owner spelling."""
        qname = f"{owner}::{leaf}"
        record_identities: set[str] = set()
        assert _record_nested_in_known_record(qname, record_identities) is False

    @given(segments=st.lists(_IDENT, min_size=3, max_size=6))
    def test_only_the_immediate_parent_is_consulted(self, segments: list[str]) -> None:
        """Arbitrary-depth chain (``a::b::c::...``): registering every
        ancestor EXCEPT the immediate parent as a known record must still
        read as "not nested" -- a deeper-but-not-immediate record ancestor
        does not count. Generalizes the two-level `Outer::Secret` example to
        an arbitrary depth."""
        qname = "::".join(segments)
        immediate_parent = "::".join(segments[:-1])
        all_but_immediate = {"::".join(segments[:k]) for k in range(1, len(segments) - 1)}
        assert immediate_parent not in all_but_immediate
        assert _record_nested_in_known_record(qname, all_but_immediate) is False
        # Registering the immediate parent too now flips it to nested.
        all_but_immediate.add(immediate_parent)
        assert _record_nested_in_known_record(qname, all_but_immediate) is True


class TestExactIdentitiesRejectsAliasCollisions:
    """Regression for the fourth review finding (Codex, fresh evidence,
    filed against commit 277a0a4): `_index_surface_types`'s `record_by_name`
    index deliberately also registers each record under an ambiguous
    trailing-segment *alias* (e.g. `other::api` also registers bare key
    `api`, so an unqualified reference to `api::Mode` resolves during the
    reachability closure walk) -- but that same alias key is NOT evidence
    that `api` itself names a class. Passing `record_by_name` (rather than
    `_record_exact_identities`'s output) into `_record_nested_in_known_record`
    let an unrelated record's own alias collide with a genuine namespace of
    the same spelling, wrongly excluding every real `api::*` public type as
    "nested" and hiding its ABI break. `_record_exact_identities` must
    produce ONLY exact qualified identities (`rec.qualified_name or
    rec.name`), never a tail alias, so this collision cannot occur."""

    def test_unrelated_records_tail_alias_does_not_read_as_a_namespace_owner(
        self,
    ) -> None:
        # `other::api` is a real class living in an unrelated namespace; its
        # own bare leaf happens to spell the SAME string as the namespace
        # `api` that `api::Thing` is genuinely declared in.
        unrelated = _rec("api", origin=ScopeOrigin.UNKNOWN, qualified_name="other::api")
        record_identities = _record_exact_identities(
            _FakeSnapshot(types=[unrelated])
        )
        # The exact-identity set holds `other::api` -- never the bare `api`
        # alias that would have collided with the namespace-scoped type.
        assert record_identities == {"other::api"}
        assert _record_nested_in_known_record("api::Thing", record_identities) is False

    def test_a_record_truly_nested_in_a_same_named_owner_is_still_detected(self) -> None:
        # The positive case must still work: when `api` really IS a known
        # record's own exact identity (not merely an alias of some other
        # record), nesting is still correctly detected.
        owner = _rec("api", origin=ScopeOrigin.UNKNOWN, qualified_name="api")
        record_identities = _record_exact_identities(_FakeSnapshot(types=[owner]))
        assert record_identities == {"api"}
        assert _record_nested_in_known_record("api::Thing", record_identities) is True


class _FakeSnapshot:
    """Minimal stand-in exposing only the `.types` attribute
    `_record_exact_identities` reads -- avoids constructing a full
    `AbiSnapshot` for a test that only exercises one field."""

    def __init__(self, types: list[RecordType]) -> None:
        self.types = types


class TestUnrelatedRecordAliasDoesNotHideANamespacedTypeEndToEnd:
    """Same regression as `TestExactIdentitiesRejectsAliasCollisions`, but
    end-to-end through `compute_public_surface`/`classify_change_surface`
    (not just the two extracted primitives directly) -- confirming the real
    caller's own wiring, not only the primitives in isolation."""

    def test_unrelated_records_bare_leaf_does_not_wrongly_nest_a_namespaced_type(
        self,
    ) -> None:
        snap = AbiSnapshot(
            library="l",
            version="1",
            # An unrelated public function is required so header-derived
            # public visibility (and therefore scoping) actually engages --
            # otherwise `compute_public_surface` treats every type as public
            # regardless of this test's own bug (ADR-016: no headers means
            # no scoping at all).
            functions=[_fn("other_api", origin=ScopeOrigin.PUBLIC_HEADER)],
            types=[
                # An unrelated record in a DIFFERENT namespace, whose bare
                # leaf name happens to collide with the `api` namespace
                # below purely as a string.
                _rec(
                    "api",
                    origin=ScopeOrigin.UNKNOWN,
                    qualified_name="other::api",
                    source_header="other.h",
                ),
                # The genuinely public, namespace-scoped (not nested) type
                # nothing else references -- the oneDPL `discard_iterator`
                # shape this whole seed exists for.
                _rec(
                    "Thing",
                    origin=ScopeOrigin.PUBLIC_HEADER,
                    qualified_name="api::Thing",
                    source_header="api.h",
                ),
            ],
        )
        surf = compute_public_surface(snap)
        c = Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="api::Thing", description="")
        assert classify_change_surface(c, surf, surf) == (True, None)


class TestRecordExactIdentitiesProperties:
    """`_record_exact_identities` must produce exactly one identity per
    record -- `rec.qualified_name` when set (castxml/clang), else
    `rec.name` (DWARF, whose `.name` is already the fully-qualified
    spelling) -- and NEVER a trailing-segment alias, for arbitrary inputs."""

    @given(qualified_name=_IDENT, bare_name=_IDENT)
    def test_qualified_name_wins_when_set(
        self, qualified_name: str, bare_name: str
    ) -> None:
        rec = _rec(bare_name, qualified_name=qualified_name)
        identities = _record_exact_identities(_FakeSnapshot(types=[rec]))
        assert identities == {qualified_name}

    @given(bare_name=_IDENT)
    def test_bare_name_used_when_qualified_name_unset(self, bare_name: str) -> None:
        rec = _rec(bare_name, qualified_name=None)
        identities = _record_exact_identities(_FakeSnapshot(types=[rec]))
        assert identities == {bare_name}

    @given(owner=_IDENT, leaf=_IDENT)
    def test_no_alias_key_is_ever_produced_for_a_namespaced_dwarf_style_name(
        self, owner: str, leaf: str
    ) -> None:
        """DWARF bakes the full scope chain directly into `.name` (no
        separate `.qualified_name`) -- the exact-identity set must contain
        only that one full spelling, never the trailing `leaf` alias
        `_index_surface_types`'s own `record_by_name` index would add."""
        qname = f"{owner}::{leaf}"
        rec = _rec(qname, qualified_name=None)
        identities = _record_exact_identities(_FakeSnapshot(types=[rec]))
        assert identities == {qname}
        assert leaf not in identities or leaf == qname


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
        record_identities = {owner_key} if nested else set()
        expected = (
            has_header
            and origin is ScopeOrigin.PUBLIC_HEADER
            and has_qualified_name
            and not internal_namespace
            and not nested
        )
        assert _record_is_confirmed_public_seed(rec, record_identities) is expected


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
        assert _record_is_confirmed_public_seed(rec, set()) is False
