# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""``type_reachability.directly_referenced_stdlib_type_spellings`` -- split
out of ``test_type_reachability.py`` (already at the file-size hard cap)
rather than folded in, matching this codebase's own ``CLAUDE.md``-documented
convention for a large test file gaining a new, independently-scoped test
class.

Threaded from ``contract_pipeline.build_contract_stage()`` into
``contract_evaluation._confirmed_type_matches`` (plan
``public-contract-default.md``, 2026-08-08 update, plus several Codex review
rounds tightening the ambiguity guards -- see the class/test docstrings
below for the individual findings and fixes).
"""

from __future__ import annotations

from abicheck.model import (
    AbiSnapshot,
    EnumType,
    Function,
    Param,
    RecordType,
    ScopeOrigin,
    TypeField,
    Visibility,
)
from abicheck.type_reachability import (
    _typedef_candidate_spellings,
    directly_referenced_stdlib_type_spellings,
    directly_referenced_stdlib_types,
)


def _fn(
    name: str,
    return_type: str = "void",
    params: list[Param] | None = None,
    visibility: Visibility = Visibility.PUBLIC,
    origin: ScopeOrigin = ScopeOrigin.UNKNOWN,
) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type=return_type,
        params=params or [],
        visibility=visibility,
        origin=origin,
    )


class TestDirectlyReferencedStdlibTypeSpellings:
    """``directly_referenced_stdlib_type_spellings`` -- the companion to
    :func:`directly_referenced_stdlib_types` that also returns each
    identity's namespace/ABI-tag-stripped bare form, so a caller matching
    against a bare ``RecordType.name``/``Change.symbol`` spelling (rather
    than the fully-qualified identity) can do so directly.

    Threaded from ``contract_pipeline.build_contract_stage()`` into
    ``contract_evaluation._confirmed_type_matches`` (plan
    ``public-contract-default.md``, 2026-08-08 update).
    """

    def test_castxml_style_bare_name_gains_a_stripped_companion_spelling(self) -> None:
        """castxml/direct-clang record RecordType.name bare and the
        namespace-qualified spelling separately in qualified_name -- both
        must be returned, since a signature is spelled with the bare form
        while the identity (what directly_referenced_stdlib_types returns)
        is the qualified one."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "api",
                    params=[Param(name="a", type="vector<int, std::allocator<int> >")],
                )
            ],
            types=[
                RecordType(
                    name="vector<int, std::allocator<int> >",
                    kind="class",
                    qualified_name="std::vector<int, std::allocator<int> >",
                )
            ],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset(
            {"std::vector<int, std::allocator<int> >"}
        )
        assert directly_referenced_stdlib_type_spellings(snap) == frozenset(
            {
                "std::vector<int, std::allocator<int> >",
                "vector<int, std::allocator<int> >",
            }
        )

    def test_dwarf_style_qualified_name_has_no_separate_bare_companion(self) -> None:
        """DWARF bakes the qualified spelling straight into RecordType.name
        (no separate qualified_name field) -- the identity and the stripped
        form differ here too (the stripped form is what a DWARF-backed
        signature actually spells), so both must still be present."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("api", params=[Param(name="a", type="std::string")])],
            types=[RecordType(name="std::string", kind="class")],
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})
        assert directly_referenced_stdlib_type_spellings(snap) == frozenset(
            {"std::string", "string"}
        )

    def test_empty_snapshot_returns_empty(self) -> None:
        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        assert directly_referenced_stdlib_type_spellings(snap) == frozenset()

    def test_never_narrower_than_the_identity_set(self) -> None:
        """For an unambiguous, header-committed reference (this fixture's
        shape), directly_referenced_stdlib_type_spellings is a pure
        superset of directly_referenced_stdlib_types's own identity set --
        NOT a universal invariant (an ambiguous stripped-spelling collision
        or an EXPORT_ONLY-only root can drop an identity entirely, by
        design, per this module's own documented ambiguity guards), just
        the common case this fixture exercises."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("api", params=[Param(name="a", type="std::string")])],
            types=[RecordType(name="std::string", kind="class")],
        )
        identities = directly_referenced_stdlib_types(snap)
        spellings = directly_referenced_stdlib_type_spellings(snap)
        assert identities <= spellings

    def test_ambiguous_bare_spelling_drops_both_the_spelling_and_both_identities(
        self,
    ) -> None:
        """Codex review, fresh evidence, two rounds: two distinct stdlib
        identities can reduce to the same namespace-stripped bare spelling
        (here, std::basic_string<char> and its dual-ABI std::__cxx11::
        sibling both stripping to bare "basic_string<char>") --
        directly_referenced_stdlib_types deliberately records both (its own
        documented false-negative-over-false-positive policy for its
        emission-gating consumer), but this function's own consumer
        (contract_evaluation) treats a hit as an authoritative
        IN_CONTRACT/COMPLETE claim, which the shared bare spelling cannot
        support: the signature that caused the match cannot say which of the
        two it means.

        An earlier revision of this fix dropped only the derived bare
        spelling while keeping each identity's own exact spelling, on the
        reasoning that an exact match can only ever name the one record it
        is the identity of -- true about the string comparison alone, but
        wrong here: in this exact scenario *neither* identity is ever named
        by its own literal qualified spelling anywhere, only the shared bare
        form is, so both identities' presence in `identities` is *itself*
        already the product of the same ambiguous match, and this module
        has no per-identity provenance to tell that apart from a genuinely
        unambiguous exact mention. Both identities must be dropped, not just
        the derived spelling."""
        old_abi = "std::basic_string<char>"
        new_abi = "std::__cxx11::basic_string<char>"
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("api", params=[Param(name="a", type="basic_string<char>")])],
            types=[
                RecordType(name=old_abi, kind="class"),
                RecordType(name=new_abi, kind="class"),
            ],
        )
        identities = directly_referenced_stdlib_types(snap)
        assert identities == frozenset({old_abi, new_abi})

        spellings = directly_referenced_stdlib_type_spellings(snap)
        assert spellings == frozenset()

    def test_an_identity_reached_via_its_own_exact_spelling_survives_a_sibling_collision(
        self,
    ) -> None:
        """The ambiguity-drop above must not be so broad it also drops an
        identity that genuinely *was* reached through its own unambiguous,
        literal qualified spelling -- distinguished here from the previous
        test only by how std::basic_string<char> is reached: through a
        nested template-argument occurrence naming it exactly (same
        empirical convention as the other "reached via exact identity"
        tests in this file), not through the shared bare form. Its
        std::__cxx11:: sibling is present but genuinely unreferenced.

        Codex review, fresh evidence, closing a further gap: the *identity*
        itself survives (it has its own independent exact-match proof), but
        its *derived bare spelling* must not -- an unreferenced sibling
        elsewhere in the same snapshot still shares that bare form, and a
        completely different, unrelated layout finding emitted for that
        sibling carries the identical bare Change.symbol. Collision counting
        spans every stdlib identity the snapshot carries, not only the
        referenced subset, precisely so this case is caught: dropping only
        the derived spelling while keeping the identity is the correct
        outcome here, not "no collision at all" as an earlier revision of
        this test asserted."""
        old_abi = "std::basic_string<char>"
        new_abi = "std::__cxx11::basic_string<char>"
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "api",
                    params=[Param(name="a", type="Holder<std::basic_string<char> >")],
                )
            ],
            types=[
                RecordType(name=old_abi, kind="class"),
                RecordType(name=new_abi, kind="class"),
            ],
        )
        identities = directly_referenced_stdlib_types(snap)
        assert identities == frozenset({old_abi})

        spellings = directly_referenced_stdlib_type_spellings(snap)
        assert spellings == frozenset({old_abi})
        assert "basic_string<char>" not in spellings

    def test_exact_match_provenance_is_independent_of_declaration_order(self) -> None:
        """Codex review, fresh evidence: an identity first matched via an
        ambiguous derived (bare) spelling in one declaration, whose own
        unambiguous exact spelling appears only in a *later* declaration,
        must still be recognized as exactly matched -- the underlying scan's
        performance-motivated early exit (stop once every stdlib candidate is
        accounted for via any route) must not silently skip that later,
        proof-bearing declaration. Confirmed empirically before the fix:
        reversing the two declarations' order flipped this function's result
        between "confirmed" and empty for the identical identity."""
        base_types = [
            RecordType(name="exception", kind="class", qualified_name="std::exception")
        ]
        base_enums = [EnumType(name="exception", qualified_name="mine::exception")]
        ambiguous_fn = _fn("api1", params=[Param(name="a", type="exception")])
        exact_fn = _fn("api2", params=[Param(name="b", type="Holder<std::exception>")])

        ambiguous_first = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[ambiguous_fn, exact_fn],
            types=base_types,
            enums=base_enums,
        )
        exact_first = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[exact_fn, ambiguous_fn],
            types=base_types,
            enums=base_enums,
        )

        assert directly_referenced_stdlib_type_spellings(ambiguous_first) == frozenset(
            {"std::exception"}
        )
        assert directly_referenced_stdlib_type_spellings(exact_first) == frozenset(
            {"std::exception"}
        )

    def test_bare_spelling_colliding_with_an_unrelated_non_stdlib_record_is_dropped(
        self,
    ) -> None:
        """Codex review, fresh evidence: a directly-referenced std::vector<int>
        and a completely unrelated, unreferenced user type mine::vector<int>
        can both reduce to the identical bare backend spelling "vector<int>"
        (castxml/direct-clang drop the enclosing namespace for any type, not
        only stdlib ones). Without the collision guard, an unrelated finding
        on mine::vector<int> (symbol="vector<int>", its own bare
        RecordType.name) would wrongly match the stdlib candidate's bare
        spelling.

        std::vector<int> is reached here via its *exact*, unambiguous
        identity rather than the colliding bare spelling: a nested
        template-argument occurrence (a std:: type spelled inside another
        type's own template arguments, empirically confirmed by
        check_fp_rate.py's "vector<int, std::allocator<int> >" fixture --
        the outer type is bare, but a nested std:: argument carries its
        "std::" prefix) -- this is deliberate, not incidental: the existing,
        already-shipped `_spelling_index` collision guard means the bare
        spelling alone could never have reached std::vector<int> here in
        the first place (confirmed empirically -- the naive bare-only
        construction resolves `directly_referenced_stdlib_types` to the
        empty set), so this test must reach it a different way to actually
        exercise the new guard rather than accidentally testing the old
        one."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "api",
                    params=[Param(name="a", type="Holder<std::vector<int> >")],
                )
            ],
            types=[
                RecordType(
                    name="vector<int>", kind="class", qualified_name="std::vector<int>"
                ),
                # Unrelated, never referenced by any public declaration --
                # shares the identical bare spelling by castxml convention.
                RecordType(
                    name="vector<int>", kind="class", qualified_name="mine::vector<int>"
                ),
            ],
        )
        identities = directly_referenced_stdlib_types(snap)
        assert identities == frozenset({"std::vector<int>"})

        spellings = directly_referenced_stdlib_type_spellings(snap)
        assert spellings == frozenset({"std::vector<int>"})
        assert "vector<int>" not in spellings

    def test_bare_spelling_colliding_with_an_unrelated_enum_is_dropped(self) -> None:
        """Codex review, fresh evidence beyond the record-vs-record collision
        above: a directly-referenced stdlib *record* (std::exception) can
        collide with an unrelated *enum*'s bare spelling the identical way
        two records can -- CastXML/direct-clang can record both with bare
        name="exception". _partition_snapshot_types's own non_stdlib_identities
        is built only from snapshot.types, so it alone cannot see this
        collision; the guard must also consult snapshot.enums."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "api",
                    # Nested template-argument occurrence, same reasoning as
                    # the record-collision test above: reaches std::exception
                    # via its exact identity, independent of the collision
                    # this test exercises.
                    params=[Param(name="a", type="Holder<std::exception>")],
                )
            ],
            types=[
                RecordType(
                    name="exception", kind="class", qualified_name="std::exception"
                ),
            ],
            enums=[
                # Unrelated, never referenced by any public declaration --
                # shares the identical bare spelling by castxml convention.
                EnumType(name="exception", qualified_name="mine::exception"),
            ],
        )
        identities = directly_referenced_stdlib_types(snap)
        assert identities == frozenset({"std::exception"})

        spellings = directly_referenced_stdlib_type_spellings(snap)
        assert spellings == frozenset({"std::exception"})
        assert "exception" not in spellings

    def test_identity_reached_only_via_the_colliding_bare_spelling_is_dropped(
        self,
    ) -> None:
        """Codex review, fresh evidence, closing the gap the record/enum
        collision tests above left open: those tests deliberately reached
        the stdlib identity via a nested *exact* template-argument
        occurrence, so they never actually exercised what happens when the
        *only* evidence for the stdlib identity is the bare spelling that
        collides with the unrelated enum. Here, std::exception is reached
        purely through the ambiguous bare "exception" mention (the original,
        enum-blind scan-level guard in _spelling_index only ever protects
        against a *record* colliding with another *record*, never an enum,
        so directly_referenced_stdlib_types itself still returns
        std::exception here) -- with no independent, unambiguous evidence
        that the signature meant std::exception rather than the unrelated
        mine::exception enum, the identity itself must be dropped, not just
        its derived spelling."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("api", params=[Param(name="a", type="exception")])],
            types=[
                RecordType(
                    name="exception", kind="class", qualified_name="std::exception"
                ),
            ],
            enums=[
                EnumType(name="exception", qualified_name="mine::exception"),
            ],
        )
        identities = directly_referenced_stdlib_types(snap)
        assert identities == frozenset({"std::exception"})

        spellings = directly_referenced_stdlib_type_spellings(snap)
        assert spellings == frozenset()

    def test_export_only_root_does_not_prove_public_header_commitment(self) -> None:
        """Codex review, fresh evidence: directly_referenced_stdlib_types()
        deliberately treats ScopeOrigin.EXPORT_ONLY (exported by the binary,
        absent from every header) as a real reachability root for its own
        emission-gating consumer -- but this function's own consumer,
        contract_evaluation.py's `public` domain, needs proof of
        *public-header* commitment specifically. An EXPORT_ONLY root
        supplies exactly the evidence the separate `exports` domain exists
        to judge, never a header commitment, so it must not confirm a
        finding under contract=public."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "api",
                    params=[Param(name="a", type="vector<int, std::allocator<int> >")],
                    origin=ScopeOrigin.EXPORT_ONLY,
                )
            ],
            types=[
                RecordType(
                    name="vector<int, std::allocator<int> >",
                    kind="class",
                    qualified_name="std::vector<int, std::allocator<int> >",
                )
            ],
        )
        # The underlying scan still treats it as referenced -- confirms the
        # gap is specifically in this function's own public-domain-specific
        # filtering, not a regression in the shared emission-gating scan.
        assert directly_referenced_stdlib_types(snap) == frozenset(
            {"std::vector<int, std::allocator<int> >"}
        )
        assert (
            directly_referenced_stdlib_type_spellings(
                snap, exclude_export_only_roots=True
            )
            == frozenset()
        )

    def test_stripped_spelling_colliding_with_an_unrelated_typedef_key_is_dropped(
        self,
    ) -> None:
        """Codex review, fresh evidence: a typedef alias whose *key* happens
        to equal an unreferenced stdlib identity's stripped bare spelling,
        but whose target is unrelated, must not let a bare signature mention
        of that alias (referencing the alias's own target, nothing to do
        with the stdlib type) confirm the unreferenced stdlib type.
        ``typedefs={"exception": "mine::Thing"}`` alongside an unreferenced
        ``std::exception`` reproduces exactly that: the underlying scan
        still matches the bare string "exception" against std::exception's
        stripped candidate (text-only matching, no typedef resolution), but
        neither a non-stdlib record nor enum spells "exception" here -- the
        collision is with the typedef key alone, a route the record/enum
        collision guard above cannot see."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "api",
                    params=[Param(name="a", type="exception")],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            types=[
                RecordType(
                    name="exception",
                    kind="class",
                    qualified_name="std::exception",
                )
            ],
            typedefs={"exception": "mine::Thing"},
        )
        # The underlying scan still treats it as referenced via the bare
        # spelling -- confirms the gap is specifically in this function's
        # own typedef-key collision handling, not a regression upstream.
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::exception"})
        assert directly_referenced_stdlib_type_spellings(snap) == frozenset()

    def test_stripped_spelling_colliding_with_a_typedef_keys_derived_suffix_is_dropped(
        self,
    ) -> None:
        """Codex review, fresh evidence: the collision isn't always the
        typedef key's own literal spelling -- a namespace-qualified key like
        ``"mine::vector<int>"`` is spelled bare in a real signature the same
        "drop the enclosing namespace" way every other declaration is, so
        its *derived* suffix (``"vector<int>"``) is what actually collides
        with the unreferenced ``std::vector<int>``'s own stripped spelling,
        never the raw dict key. A collision guard checking only exact
        ``snapshot.typedefs`` keys misses this entirely."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "api",
                    params=[Param(name="a", type="vector<int>")],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            types=[
                RecordType(
                    name="vector<int>",
                    kind="class",
                    qualified_name="std::vector<int>",
                )
            ],
            typedefs={"mine::vector<int>": "mine::Thing<int>"},
        )
        # The underlying scan still treats it as referenced via the bare
        # spelling -- confirms the gap is specifically in this function's
        # own typedef-suffix collision handling, not a regression upstream.
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::vector<int>"})
        assert directly_referenced_stdlib_type_spellings(snap) == frozenset()

    def test_stripped_spelling_colliding_with_an_ambiguous_typedef_spelling_is_dropped(
        self,
    ) -> None:
        """Codex review, fresh evidence: two typedefs that disagree on the
        target for the same derived spelling make that spelling *ambiguous*
        -- ``_typedef_spelling_targets()`` deliberately drops an ambiguous
        spelling from its own resolved index rather than picking either
        candidate. A collision check that reads only that resolved index
        (``.get(stripped)`` returning ``None``) cannot tell "ambiguous,
        drop it" apart from "no typedef reaches this spelling at all" --
        and treating the former as the latter is exactly as unsafe as
        missing a resolved, disagreeing collision.
        ``typedefs={"exception": "mine::Thing", "mine::exception":
        "mine::Other"}`` makes bare "exception" ambiguous between the two,
        alongside an unreferenced ``std::exception`` record."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "api",
                    params=[Param(name="a", type="exception")],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            types=[
                RecordType(
                    name="exception",
                    kind="class",
                    qualified_name="std::exception",
                )
            ],
            typedefs={
                "exception": "mine::Thing",
                "mine::exception": "mine::Other",
            },
        )
        # The underlying scan still treats it as referenced via the bare
        # spelling -- confirms the gap is specifically in this function's
        # own ambiguous-typedef collision handling, not a regression
        # upstream.
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::exception"})
        assert directly_referenced_stdlib_type_spellings(snap) == frozenset()

    def test_typedef_target_naming_the_same_identity_is_not_a_collision(self) -> None:
        """Codex review, fresh evidence: a typedef whose target genuinely
        *is* this same stdlib identity -- just spelled fully-qualified,
        not in the identity's own stripped bare form -- must not be
        treated as an unrelated collision.
        ``typedefs={"mine::vector<int>": "std::vector<int>"}`` alongside
        the *same* ``std::vector<int>`` record is a legitimate alias for
        the identity being evaluated, not evidence of something else; a
        comparison checking only ``target == stripped`` (never
        ``target == identity``) rejected it anyway, silently discarding a
        genuine public layout break."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "api",
                    params=[Param(name="a", type="vector<int>")],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            types=[
                RecordType(
                    name="vector<int>",
                    kind="class",
                    qualified_name="std::vector<int>",
                )
            ],
            typedefs={"mine::vector<int>": "std::vector<int>"},
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::vector<int>"})
        assert directly_referenced_stdlib_type_spellings(snap) == frozenset(
            {"std::vector<int>", "vector<int>"}
        )

    def test_export_only_record_reached_transitively_does_not_prove_public_header_commitment(
        self,
    ) -> None:
        """Codex review, fresh evidence: the EXPORT_ONLY exclusion must also
        apply to a record reached *transitively*, not just to a seed
        declaration. A public header exposing only an opaque ``Wrapper *``
        alongside a snapshot that also carries a *complete* EXPORT_ONLY
        definition of ``Wrapper`` (known only from the binary, absent from
        every header) reaches that export-only definition once ``Wrapper``
        itself is seeded from the header -- the seed-level EXPORT_ONLY
        exclusion never runs again for a record walked transitively, so a
        std:: field on that export-only definition was still confirmed."""
        vec = RecordType(
            name="vector<int, std::allocator<int> >",
            kind="class",
            qualified_name="std::vector<int, std::allocator<int> >",
        )
        wrapper = RecordType(
            name="Wrapper",
            kind="struct",
            origin=ScopeOrigin.EXPORT_ONLY,
            fields=[TypeField(name="v", type="vector<int, std::allocator<int> >")],
        )
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "api",
                    params=[Param(name="w", type="Wrapper *")],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            types=[vec, wrapper],
        )
        # The underlying scan still walks the export-only record's field --
        # confirms the gap is specifically in this function's own
        # transitive EXPORT_ONLY exclusion, not a regression upstream.
        assert directly_referenced_stdlib_types(snap) == frozenset(
            {"std::vector<int, std::allocator<int> >"}
        )
        assert (
            directly_referenced_stdlib_type_spellings(
                snap, exclude_export_only_roots=True
            )
            == frozenset()
        )

    def test_typedef_target_recursion_never_fabricates_exact_provenance(
        self,
    ) -> None:
        """Codex review, fresh evidence: a match found only by recursively
        scanning a typedef's *target* string must never count as exact
        provenance -- the real declaration only ever wrote the (possibly
        ambiguous) alias, not the target's own spelling.
        ``EnumType(name="exception", qualified_name="mine::exception")``
        alongside ``typedefs={"other::exception": "std::exception"}`` and a
        parameter spelled bare "exception" reproduces this: the enum's bare
        spelling collides with std::exception's stripped form (correctly
        excluding the derived spelling per the collision guard above), but
        the typedef's fully-qualified target text, scanned recursively,
        previously satisfied the exact-match check anyway, letting the
        identity survive through the "identity in exact" fallback."""
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "api",
                    params=[Param(name="a", type="exception")],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            types=[
                RecordType(
                    name="exception",
                    kind="class",
                    qualified_name="std::exception",
                )
            ],
            enums=[EnumType(name="exception", qualified_name="mine::exception")],
            typedefs={"other::exception": "std::exception"},
        )
        # The underlying scan still treats it as referenced via the bare
        # spelling -- confirms the gap is specifically in this function's
        # own exact-provenance handling of typedef-target recursion, not a
        # regression upstream.
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::exception"})
        assert directly_referenced_stdlib_type_spellings(snap) == frozenset()

    def test_an_unambiguous_typedef_alias_still_confers_exact_provenance(
        self,
    ) -> None:
        """Codex review, fresh evidence: the fix above must not blanket-reject
        *every* typedef-target recursion -- a genuinely unambiguous alias
        (one with no enum/record collision at all) is still real proof the
        declaration named that identity, even when the target's own bare
        form happens to collide with an unrelated stdlib sibling elsewhere
        in the snapshot. ``typedefs={"std::string":
        "std::__cxx11::basic_string<char>"}`` alongside both dual-ABI
        ``basic_string<char>`` records reproduces this: the __cxx11 sibling
        (the typedef's actual, unambiguous target) must survive via its own
        alias's exactness even though its stripped bare form is ambiguous
        against the old-ABI sibling's mere *presence* in the snapshot (the
        collision guard counts every stdlib identity the snapshot carries,
        referenced or not -- see its own comment)."""
        old_abi = RecordType(
            name="basic_string<char>",
            kind="class",
            qualified_name="std::basic_string<char>",
        )
        new_abi = RecordType(
            name="basic_string<char>",
            kind="class",
            qualified_name="std::__cxx11::basic_string<char>",
        )
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn(
                    "api",
                    params=[Param(name="a", type="string")],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                )
            ],
            types=[old_abi, new_abi],
            typedefs={"std::string": "std::__cxx11::basic_string<char>"},
        )
        # The underlying scan reaches only the __cxx11 sibling -- confirms
        # the gap is specifically in this function's own exact-provenance
        # decision, not a regression upstream.
        assert directly_referenced_stdlib_types(snap) == frozenset(
            {"std::__cxx11::basic_string<char>"}
        )
        assert directly_referenced_stdlib_type_spellings(snap) == frozenset(
            {"std::__cxx11::basic_string<char>"}
        )

    def test_typedef_provenance_survives_regardless_of_declaration_order(
        self,
    ) -> None:
        """Codex review, fresh evidence: the scan's own ``_resolved_typedefs``
        walk-once cache must not also gate *provenance*. Two declarations --
        one spelling an alias ("bad") that itself collides with an unrelated
        enum and resolves through an intermediate typedef ("Good") to the
        __cxx11 sibling, one spelling that intermediate typedef ("Good")
        directly and unambiguously -- must produce the identical result
        regardless of which declaration is scanned first: whichever comes
        second previously found "Good" already resolved by the first and
        recorded no provenance of its own, silently making the result
        depend on declaration order."""
        old_abi = RecordType(
            name="basic_string<char>",
            kind="class",
            qualified_name="std::basic_string<char>",
        )
        new_abi = RecordType(
            name="basic_string<char>",
            kind="class",
            qualified_name="std::__cxx11::basic_string<char>",
        )
        enum = EnumType(name="bad", qualified_name="mine::bad")
        typedefs = {"ns::bad": "Good", "Good": "std::__cxx11::basic_string<char>"}
        bad_fn = _fn(
            "api_bad",
            params=[Param(name="a", type="bad")],
            origin=ScopeOrigin.PUBLIC_HEADER,
        )
        good_fn = _fn(
            "api_good",
            params=[Param(name="a", type="Good")],
            origin=ScopeOrigin.PUBLIC_HEADER,
        )
        forward = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[bad_fn, good_fn],
            types=[old_abi, new_abi],
            enums=[enum],
            typedefs=dict(typedefs),
        )
        reverse = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[good_fn, bad_fn],
            types=[old_abi, new_abi],
            enums=[enum],
            typedefs=dict(typedefs),
        )
        expected = frozenset({"std::__cxx11::basic_string<char>"})
        assert directly_referenced_stdlib_type_spellings(forward) == expected
        assert directly_referenced_stdlib_type_spellings(reverse) == expected

    def test_typedef_provenance_propagation_recurses_through_a_further_alias(
        self,
    ) -> None:
        """`_StdlibReferenceScan._propagate_typedef_provenance`'s own
        recursive branch -- reached only when a *second* declaration
        spells an already-resolved typedef alias whose target itself names
        a *further*, distinct typedef alias not yet visited in that
        propagation call. `First -> Second -> Third -> std::string`: the
        first declaration (spelling `First`) resolves the whole chain via
        `scan`'s own recursive typedef branch, marking `Second`/`Third`
        resolved along the way. A second declaration spelling `Second`
        directly then hits `scan`'s "already resolved" branch, which
        propagates `Second`'s own provenance through `_propagate_typedef_
        provenance` -- and that propagation must itself recurse through
        `Third` to reach `std::string`, not stop at the first hop."""
        typedefs = {"First": "Second", "Second": "Third", "Third": "std::string"}
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn("use_first", params=[Param(name="a", type="First")]),
                _fn("use_second", params=[Param(name="b", type="Second")]),
            ],
            types=[RecordType(name="std::string", kind="class")],
            typedefs=dict(typedefs),
        )
        assert directly_referenced_stdlib_type_spellings(snap) == frozenset(
            {"std::string", "string"}
        )

    def test_propagate_typedef_provenance_skips_an_already_seen_alias(self) -> None:
        """`_propagate_typedef_provenance`'s own cycle/diamond guard --
        ``if alias in seen: continue`` -- fires only when *one* propagation
        call's own target text names the same alias twice. Built so a
        single top-level declaration reaches it without a second
        declaration: `A -> B -> "C C"` (`B`'s target repeats `C`) means the
        *second* `C` occurrence is handled by `scan`'s own "already
        resolved" branch, calling `_propagate_typedef_provenance` with
        `C`'s target `"D D"` -- which itself repeats `D`, so that single
        propagation call's own loop meets `D` a second time after already
        adding it to `seen` on the first. `D`'s own target is deliberately
        spelled in its namespace-stripped *bare* form, not the identity's
        own self-key, so the deepest propagation call's stdlib-pattern match
        also exercises the ``spelling != identity`` (derived-spelling, not
        exact) branch -- correctly recording nothing for that route, since a
        derived spelling is never trustworthy exact-match provenance."""
        typedefs = {
            "A": "B",
            "B": "C C",
            "C": "D D",
            "D": "vector<int, std::allocator<int> >",
        }
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("use_a", params=[Param(name="a", type="A")])],
            types=[
                RecordType(
                    name="vector<int, std::allocator<int> >",
                    kind="class",
                    qualified_name="std::vector<int, std::allocator<int> >",
                )
            ],
            typedefs=dict(typedefs),
        )
        assert directly_referenced_stdlib_type_spellings(snap) == frozenset(
            {
                "std::vector<int, std::allocator<int> >",
                "vector<int, std::allocator<int> >",
            }
        )


class TestCommittedRootsScoping:
    """`compare --post-manifest` scopes the committed public contract down
    to an explicit allowlist and demotes any *export* finding whose symbol
    isn't in it -- but `directly_referenced_stdlib_type_spellings`'s own
    scan previously seeded from *every* still-PUBLIC, header-committed
    declaration regardless, so an uncommitted-by-manifest function whose
    signature mentions a stdlib template type could still confirm an
    unrelated dependency-layout finding as IN_CONTRACT/COMPLETE, even
    though no committed export references that type at all."""

    def test_committed_roots_excludes_an_uncommitted_seed_declaration(self) -> None:
        vec = RecordType(
            name="vector<int, std::allocator<int> >",
            kind="class",
            qualified_name="std::vector<int, std::allocator<int> >",
        )
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn("stable", origin=ScopeOrigin.PUBLIC_HEADER),
                _fn(
                    "api",
                    params=[Param(name="a", type="vector<int, std::allocator<int> >")],
                    origin=ScopeOrigin.PUBLIC_HEADER,
                ),
            ],
            types=[vec],
        )
        # Without a manifest, `api`'s signature is a legitimate root --
        # confirms the gap is specifically in the allowlist-scoping check,
        # not a regression in the ordinary unscoped path.
        assert directly_referenced_stdlib_type_spellings(snap) == frozenset(
            {
                "std::vector<int, std::allocator<int> >",
                "vector<int, std::allocator<int> >",
            }
        )
        # An active manifest committing only `stable` must exclude `api` as
        # a seed entirely, so the stdlib type it names is never confirmed.
        assert (
            directly_referenced_stdlib_type_spellings(
                snap, committed_roots=frozenset({"stable"})
            )
            == frozenset()
        )


class TestTypedefCandidateSpellingsExactKeyCollision:
    """`_typedef_candidate_spellings`'s exact-key registration is guarded by
    the same non-stdlib collision check every derived candidate already
    goes through: a typedef key that collides with a real, unrelated
    non-stdlib record's own signature spelling must not be registered as a
    reachable typedef candidate at all, mirroring
    `_typedef_spelling_targets`'s identical guard on its own exact key."""

    def test_colliding_exact_key_is_excluded_but_a_non_colliding_derived_form_survives(
        self,
    ) -> None:
        non_stdlib_identities = frozenset({"api::Alias"})
        # "Alias" itself collides with the unrelated "api::Alias" record's
        # own bare signature spelling -- excluded. "ns::Alias"'s own derived
        # suffix "Alias" collides too, but its *full* key does not.
        typedefs = {"Alias": "mine::Thing", "ns::Alias": "mine::OtherThing"}
        candidates = _typedef_candidate_spellings(typedefs, non_stdlib_identities)
        assert "Alias" not in candidates
        assert "ns::Alias" in candidates


class TestDeepTypedefChainDoesNotRecurse:
    """Codex review, fresh evidence: a snapshot with a long chain of
    typedef aliases (`A0 -> A1 -> A2 -> ... -> std::string`) previously
    raised a real `RecursionError` on two independent paths -- `scan`'s own
    recursive self-call following the chain to resolve a fresh alias, and
    `_propagate_typedef_provenance`'s own recursive call re-deriving
    provenance for an already-resolved alias reached a second time.
    Confirmed empirically (both scenarios below reproduced `RecursionError`
    on the pre-fix code with a several-thousand-alias chain, comfortably
    inside what a generated/vendored header set can produce) and fixed by
    converting both to an explicit worklist -- mirroring
    `_finditer_allow_nested`'s own stack-based rewrite for the identical
    class of unbounded-nesting risk."""

    @staticmethod
    def _chain(length: int) -> dict[str, str]:
        typedefs = {f"A{i}": f"A{i + 1}" for i in range(length)}
        typedefs[f"A{length}"] = "std::string"
        return typedefs

    def test_single_declaration_naming_the_outermost_alias_of_a_long_chain(
        self,
    ) -> None:
        # Exercises `scan`'s own recursive alias-following for a *fresh*
        # resolution -- a single declaration naming the chain's outermost
        # alias must walk every intermediate link down to the stdlib target.
        length = 1200
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("use_outer", params=[Param(name="a", type="A0")])],
            types=[RecordType(name="std::string", kind="class")],
            typedefs=self._chain(length),
        )
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::string"})

    def test_deepest_first_declarations_exercise_provenance_propagation(self) -> None:
        # Exercises `_propagate_typedef_provenance`'s own recursive call --
        # declarations exposed deepest-first mean every alias but the very
        # first one encountered is *already* resolved by the time its own
        # declaration is scanned, routing through the propagation path for
        # (almost) the entire chain.
        length = 1200
        typedefs = self._chain(length)
        fns = [
            _fn(f"use{i}", params=[Param(name="a", type=f"A{i}")])
            for i in range(length - 1, -1, -1)
        ]
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=fns,
            types=[RecordType(name="std::string", kind="class")],
            typedefs=typedefs,
        )
        assert directly_referenced_stdlib_type_spellings(snap) == frozenset(
            {"std::string", "string"}
        )


class TestRecordFieldExactnessRespectsTheOwningRecordsOwnProvenance:
    """Codex review, fresh evidence: a stdlib type named directly inside a
    reached record's own field was previously credited as unconditionally
    exact/trusted regardless of how ambiguously the record *itself* was
    reached -- `_walk_reached_records` always scanned a record's fields
    with the default `via_typedef=False`, even for a record reached only
    through a typedef alias that collides with an unrelated enum's own
    bare spelling. A public signature spelling ambiguous bare `Alias`
    (colliding with `EnumType(name="Alias", ...)`) that also resolves,
    via the qualified typedef key `other::Alias`, to `Wrapper` -- whose own
    field is `std::exception` -- must not confirm `std::exception` at all:
    `Wrapper`'s own reachability was never proven, since `Alias` could
    just as well have meant the enum."""

    def test_stdlib_type_in_an_ambiguously_reached_records_field_is_not_confirmed(
        self,
    ) -> None:
        enum = EnumType(name="Alias", qualified_name="mine::Alias")
        wrapper = RecordType(
            name="Wrapper",
            kind="struct",
            fields=[TypeField(name="e", type="std::exception")],
        )
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("api", params=[Param(name="a", type="Alias")])],
            types=[wrapper, RecordType(name="std::exception", kind="class")],
            enums=[enum],
            typedefs={"other::Alias": "Wrapper"},
        )
        # directly_referenced_stdlib_types (the OTHER, emission-gating
        # function) is unaffected -- it still reaches Wrapper's field
        # regardless of ambiguity, matching its own documented
        # false-negative-over-false-positive policy for that consumer.
        assert directly_referenced_stdlib_types(snap) == frozenset({"std::exception"})
        # But the confirmation function must not trust it at all.
        assert directly_referenced_stdlib_type_spellings(snap) == frozenset()

    def test_stdlib_type_in_an_unambiguously_reached_records_field_is_confirmed(
        self,
    ) -> None:
        # Sanity check alongside the guard above: with no colliding enum,
        # `Alias` unambiguously resolves to `Wrapper`, and `std::exception`
        # inside its field is genuinely, trustworthily reached.
        wrapper = RecordType(
            name="Wrapper",
            kind="struct",
            fields=[TypeField(name="e", type="std::exception")],
        )
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("api", params=[Param(name="a", type="Alias")])],
            types=[wrapper, RecordType(name="std::exception", kind="class")],
            typedefs={"other::Alias": "Wrapper"},
        )
        assert directly_referenced_stdlib_type_spellings(snap) == frozenset(
            {"std::exception", "exception"}
        )

    def test_a_field_named_directly_in_declaration_text_is_still_trusted(
        self,
    ) -> None:
        # A record reached directly (never via any typedef) still trusts
        # its own fields unconditionally -- the fix only changes behavior
        # for records reached via an ambiguous alias.
        wrapper = RecordType(
            name="Wrapper",
            kind="struct",
            fields=[TypeField(name="e", type="std::exception")],
        )
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_fn("api", params=[Param(name="w", type="Wrapper")])],
            types=[wrapper, RecordType(name="std::exception", kind="class")],
        )
        assert directly_referenced_stdlib_type_spellings(snap) == frozenset(
            {"std::exception", "exception"}
        )


class TestReachableStdlibCacheHandlesADiamondAliasGraph:
    """`_reachable_stdlib`'s memoized cache (the fix for the quadratic
    rewalk finding) must still produce the same result as a full walk when
    two different aliases share a common downstream target -- a diamond
    in the typedef graph, not just a linear chain. A third alias sharing
    the same target additionally exercises the cache's own hit path (the
    second-or-later query for an already-cached alias, as opposed to the
    first query that actually computes and caches it)."""

    def test_three_aliases_sharing_a_common_stdlib_target_all_confirm_it(
        self,
    ) -> None:
        typedefs = {
            "Left": "Shared",
            "Right": "Shared",
            "Middle": "Shared",
            "Shared": "std::string",
        }
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[
                _fn("use_left", params=[Param(name="a", type="Left")]),
                _fn("use_right", params=[Param(name="b", type="Right")]),
                _fn("use_middle", params=[Param(name="c", type="Middle")]),
            ],
            types=[RecordType(name="std::string", kind="class")],
            typedefs=typedefs,
        )
        assert directly_referenced_stdlib_type_spellings(snap) == frozenset(
            {"std::string", "string"}
        )
