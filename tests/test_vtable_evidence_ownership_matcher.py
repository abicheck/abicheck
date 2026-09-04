# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Property tests for ``compare.vtable_evidence._owned_virtual_signatures``'s
own matching logic (ADR-063 Track 2, 5B closure; Codex review on PR #1049).

``compare/AGENTS.md``'s "Conventions" section requires a standalone
property-test class for a reusable matching/grouping primitive added to
this package -- ``_owned_virtual_signatures`` is exactly that: it decides
whether a ``Function``'s ``owner_class_of(...)`` result "matches" a query
identity via an eager namespace-suffix intersection (see its own
docstring). ``tests/test_virtual_method_addition_vtable_evidence.py``
exercises it only through hand-picked examples, several of which use
``vtable_transition_is_evidenced`` -- the primitive's own one production
caller -- as the oracle; a regression in the matching logic itself could
make both agree on a wrong answer.

These tests use an **independent** reference matcher (``_reference_match``
below, hand-written from a simple segment-tuple model, never calling
``_namespace_suffix_spellings`` or ``owner_class_of``) and inject fake
``owner_class_of``/``namespace_suffix_spellings`` callables built from that
same segment-tuple model -- exercising the primitive's real eager-matching
*logic* directly, decoupled from the production string parsers under test
elsewhere (``tests/test_type_reachability_stdlib_spellings.py``,
``tests/test_kde_compat_detectors.py`` for ``owner_class_of``).
"""

from __future__ import annotations

from hypothesis import given, strategies as st

from abicheck.compare.vtable_evidence import _owned_virtual_signatures
from abicheck.model import Function, Visibility

# A simple, template-free identity model: a tuple of plain identifier
# segments, e.g. ("ns", "Outer", "Inner"). Deliberately excludes template
# arguments/operator spellings -- the real _namespace_suffix_spellings'
# depth-tracking behavior for those is that function's own contract, tested
# directly in test_type_reachability_stdlib_spellings.py; this primitive's
# own contract is just "do two suffix-spelling sets intersect".
_segment = st.text(alphabet="abcAB", min_size=1, max_size=3)
_segments = st.lists(_segment, min_size=1, max_size=3).map(tuple)


def _spelling(segments: tuple[str, ...]) -> str:
    return "::".join(segments)


def _suffix_spellings(segments: tuple[str, ...]) -> list[str]:
    """Every suffix of *segments*, spelled -- the reference model's own
    ``namespace_suffix_spellings`` implementation, independent of
    ``model.namespace_spelling._namespace_suffix_spellings``'s char-by-char
    depth-tracking parser."""
    return [_spelling(segments[i:]) for i in range(len(segments))]


def _reference_match(query: tuple[str, ...], owner: tuple[str, ...]) -> bool:
    """Independent oracle: *query* and *owner* match iff they share ANY
    common trailing run of segments of length >= 1 -- the same relation
    ``_owned_virtual_signatures``'s eager set-intersection over *every*
    suffix spelling (down to the bare leaf) computes, derived here from
    first principles (plain tuple slicing over every possible common
    suffix length) rather than by calling any production spelling helper.

    Note this is deliberately *not* "one tuple is a suffix of the other":
    a shared bare leaf alone (the shortest possible suffix, length 1)
    already counts, since the eager matching under test intersects the
    *bare leaf* spelling too -- e.g. query ``("AA", "A")`` and owner
    ``("A", "A")`` share no whole-tuple suffix relation at all, but both
    spell a length-1 suffix of plain ``"A"``, which is exactly the shared
    (if spurious) match the module's own docstring says is the safe
    direction for this eager design.
    """
    for k in range(1, min(len(query), len(owner)) + 1):
        if query[-k:] == owner[-k:]:
            return True
    return False


def _virtual_fn(mangled: str) -> Function:
    return Function(
        name=mangled,
        mangled=mangled,
        return_type="void",
        visibility=Visibility.PUBLIC,
        is_virtual=True,
    )


def _non_virtual_fn(mangled: str) -> Function:
    return Function(
        name=mangled,
        mangled=mangled,
        return_type="void",
        visibility=Visibility.PUBLIC,
        is_virtual=False,
    )


@st.composite
def _owner_populations(draw: st.DrawFn):
    """A query identity plus a small population of (mangled, owner,
    is_virtual) triples, each mangled name unique."""
    query = draw(_segments)
    n = draw(st.integers(min_value=0, max_value=6))
    owners = draw(st.lists(_segments, min_size=n, max_size=n))
    virtuals = draw(st.lists(st.booleans(), min_size=n, max_size=n))
    mangled_names = [f"_Z{i}fn" for i in range(n)]
    return query, list(zip(mangled_names, owners, virtuals, strict=True))


def _build(
    query: tuple[str, ...],
    population: list[tuple[str, tuple[str, ...], bool]],
) -> tuple[str, dict[str, Function], dict[str, str]]:
    funcs: dict[str, Function] = {}
    owner_by_mangled: dict[str, str] = {}
    for mangled, owner_segments, is_virtual in population:
        funcs[mangled] = (_virtual_fn if is_virtual else _non_virtual_fn)(mangled)
        owner_by_mangled[mangled] = _spelling(owner_segments)
    query_spelling = _spelling(query)
    return query_spelling, funcs, owner_by_mangled


def _fake_owner_class_of(owner_by_mangled: dict[str, str]):
    def _resolve(fn: Function) -> str | None:
        return owner_by_mangled.get(fn.mangled)

    return _resolve


def _fake_namespace_suffix_spellings(identity: str) -> list[str]:
    segments = tuple(identity.split("::")) if identity else ()
    return _suffix_spellings(segments)


class TestOwnedVirtualSignaturesMatching:
    """Standalone property tests for the eager namespace-suffix matcher in
    ``_owned_virtual_signatures``, independent of ``_owned_virtual_
    signatures``'s own one production caller (Codex review, PR #1049)."""

    @given(_owner_populations())
    def test_soundness_every_result_is_a_real_virtual_owned_function(
        self, data
    ) -> None:
        """Every mangled name returned really is: (a) present in *funcs*,
        (b) ``is_virtual``, and (c) owned by something the independent
        oracle agrees matches the query."""
        query_segments, population = data
        query, funcs, owner_by_mangled = _build(query_segments, population)
        result = _owned_virtual_signatures(
            query,
            funcs,
            owner_class_of=_fake_owner_class_of(owner_by_mangled),
            namespace_suffix_spellings=_fake_namespace_suffix_spellings,
        )
        for mangled in result:
            assert mangled in funcs
            assert funcs[mangled].is_virtual
            owner_segments = next(o for m, o, _ in population if m == mangled)
            assert _reference_match(query_segments, owner_segments)

    @given(_owner_populations())
    def test_completeness_every_oracle_match_is_returned(self, data) -> None:
        """The converse of soundness: nothing the independent oracle deems
        a match is ever silently dropped."""
        query_segments, population = data
        query, funcs, owner_by_mangled = _build(query_segments, population)
        result = _owned_virtual_signatures(
            query,
            funcs,
            owner_class_of=_fake_owner_class_of(owner_by_mangled),
            namespace_suffix_spellings=_fake_namespace_suffix_spellings,
        )
        for mangled, owner_segments, is_virtual in population:
            if is_virtual and _reference_match(query_segments, owner_segments):
                assert mangled in result

    @given(_owner_populations())
    def test_non_virtual_functions_never_appear_even_when_owner_matches(
        self, data
    ) -> None:
        query_segments, population = data
        query, funcs, owner_by_mangled = _build(query_segments, population)
        result = _owned_virtual_signatures(
            query,
            funcs,
            owner_class_of=_fake_owner_class_of(owner_by_mangled),
            namespace_suffix_spellings=_fake_namespace_suffix_spellings,
        )
        for mangled, owner_segments, is_virtual in population:
            if not is_virtual and _reference_match(query_segments, owner_segments):
                assert mangled not in result

    @given(_segments)
    def test_a_virtual_function_with_no_resolvable_owner_never_matches(
        self, query_segments
    ) -> None:
        """A free (owner-less) virtual -- ``owner_class_of`` returning
        ``None``, the shape a real free function or an unresolved owner
        produces -- must never appear in any result, regardless of query.
        Exercises the matcher's own early-exit for a falsy owner."""
        mangled = "_Zfree"
        funcs = {mangled: _virtual_fn(mangled)}
        result = _owned_virtual_signatures(
            _spelling(query_segments),
            funcs,
            owner_class_of=lambda fn: None,
            namespace_suffix_spellings=_fake_namespace_suffix_spellings,
        )
        assert result == set()

    @given(
        query_segments=_segments,
        population=st.lists(
            st.tuples(_segments, st.booleans()), min_size=0, max_size=4
        ),
        noise_owner=_segments,
    )
    def test_unrelated_owner_noise_never_changes_the_result(
        self, query_segments, population, noise_owner
    ) -> None:
        """Stability: a virtual function whose owner shares no suffix with
        the query at all -- confirmed via the independent oracle -- never
        changes the result, whether or not it's already present."""
        if _reference_match(query_segments, noise_owner):
            return  # not the unrelated case this property is about
        named_population = [
            (f"_Z{i}fn", owner, is_virtual)
            for i, (owner, is_virtual) in enumerate(population)
        ]
        query, funcs, owner_by_mangled = _build(query_segments, named_population)
        before = _owned_virtual_signatures(
            query,
            funcs,
            owner_class_of=_fake_owner_class_of(owner_by_mangled),
            namespace_suffix_spellings=_fake_namespace_suffix_spellings,
        )
        noise_mangled = "_Znoise"
        funcs_with_noise = {
            **funcs,
            noise_mangled: _virtual_fn(noise_mangled),
        }
        owner_by_mangled_with_noise = {
            **owner_by_mangled,
            noise_mangled: _spelling(noise_owner),
        }
        after = _owned_virtual_signatures(
            query,
            funcs_with_noise,
            owner_class_of=_fake_owner_class_of(owner_by_mangled_with_noise),
            namespace_suffix_spellings=_fake_namespace_suffix_spellings,
        )
        assert after == before

    @given(_segments)
    def test_a_function_owned_by_exactly_the_query_identity_always_matches(
        self, segments
    ) -> None:
        """Reflexivity: an exact-identity owner always matches its own
        query, regardless of how many segments it has -- the base case the
        eager suffix matching is built on."""
        mangled = "_Zself"
        query = _spelling(segments)
        funcs = {mangled: _virtual_fn(mangled)}
        owner_by_mangled = {mangled: query}
        result = _owned_virtual_signatures(
            query,
            funcs,
            owner_class_of=_fake_owner_class_of(owner_by_mangled),
            namespace_suffix_spellings=_fake_namespace_suffix_spellings,
        )
        assert mangled in result

    @given(_segments, st.integers(min_value=0, max_value=2))
    def test_equivalence_under_a_shorter_query_suffix_spelling(
        self, owner_segments, drop
    ) -> None:
        """A function owned by a fully-qualified identity still matches
        when the query is spelled as any of that identity's own namespace
        suffixes (e.g. querying "Inner" still finds an "ns::Outer::Inner"
        owner) -- the eager-matching behavior the module's own docstring
        states is deliberate, tested here independent of any caller."""
        drop = min(drop, len(owner_segments) - 1)
        query_segments = owner_segments[drop:]
        mangled = "_Zqualified"
        funcs = {mangled: _virtual_fn(mangled)}
        owner_by_mangled = {mangled: _spelling(owner_segments)}
        result = _owned_virtual_signatures(
            _spelling(query_segments),
            funcs,
            owner_class_of=_fake_owner_class_of(owner_by_mangled),
            namespace_suffix_spellings=_fake_namespace_suffix_spellings,
        )
        assert mangled in result
