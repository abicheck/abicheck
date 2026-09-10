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

"""``surface.py``'s demangle-aware type-candidate fallback (Codex review,
item 3), split out of ``test_surface.py`` to keep that file under the
AI-readiness file-size hard cap.

``VTABLE_SLOT_COUNT_CHANGED``/``RTTI_INHERITANCE_CHANGED``/
``VTT_SLOT_COUNT_CHANGED`` (``diff_elf_layout.py``) carry a raw mangled
``_ZTV``/``_ZTI``/``_ZTT`` + class symbol in ``Change.symbol``, not a plain
type spelling -- none of those kinds are in ``_TYPE_LEVEL_KIND_NAMES``, so
``_classify_symbol_level`` runs first, finds no matching function/variable
(a vtable/RTTI symbol is neither), and falls through to the type-level path
with ``sym`` still mangled. Before demangling that fallback,
``_type_identifiers`` could never match the mangled blob against any real
type name, so this always defaulted to "unknown -> keep" regardless of
whether the class was actually public -- scoping could never demote
genuinely internal-only vtable/RTTI churn the way it demotes every other
type-level finding kind about the same class.
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.model import (
    AbiSnapshot,
    Function as _Function,
    Param,
    RecordType,
    ScopeOrigin,
    Visibility,
)
from abicheck.model.mangled_name import (
    itanium_special_name_owner_identifiers,
    itanium_special_name_owner_scope_components,
)
from abicheck.surface import (
    REASON_NON_PUBLIC_TYPE,
    classify_change_surface,
    compute_public_surface,
)


def _no_demangler() -> bool:
    """True when no working C++ demangler (cxxfilt / c++filt) is available --
    some CI runners (e.g. macOS) have neither. Mirrors ``test_source_abi.py``'s
    identically-named helper/marker pair for the same reason: a test that
    asserts the demangler-*present* behavior must be skipped, not failed,
    on a host with none installed."""
    from abicheck.demangle import demangle

    return demangle("_ZN6WidgetC1Ev") is None


#: Skip marker for a test asserting the demangler-*present* half of
#: TestDemanglerFallbackForUnparseableShapes -- the demangler-*absent* half
#: is simulated via monkeypatching and needs no real tool.
needs_demangler = pytest.mark.skipif(
    _no_demangler(), reason="no C++ demangler (cxxfilt/c++filt) available"
)


def _fn(name, ret="void", params=(), vis=Visibility.PUBLIC, mangled=None):
    return _Function(
        name=name,
        mangled=mangled if mangled is not None else f"_Z{len(name)}{name}",
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=vis,
        origin=ScopeOrigin.UNKNOWN,
    )


def _rec(name):
    return RecordType(name=name, kind="struct", size_bits=64)


class TestMangledVtableSymbolSurfaceClassification:
    def _surf(self, snap):
        return compute_public_surface(snap)

    def test_mangled_vtable_symbol_demotes_a_non_public_type(self):
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="Result *")],
            types=[_rec("Result"), _rec("InternalCache")],
        )
        s = self._surf(snap)
        c = Change(
            kind=ChangeKind.VTABLE_SLOT_COUNT_CHANGED,
            symbol="_ZTV13InternalCache",
            description="",
        )
        assert classify_change_surface(c, s, s) == (False, REASON_NON_PUBLIC_TYPE)

    def test_mangled_vtable_symbol_of_a_public_type_stays_in_surface(self):
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="Result *")],
            types=[_rec("Result"), _rec("InternalCache")],
        )
        s = self._surf(snap)
        c_pub = Change(
            kind=ChangeKind.VTABLE_SLOT_COUNT_CHANGED,
            symbol="_ZTV6Result",
            description="",
        )
        assert classify_change_surface(c_pub, s, s) == (True, None)

    def test_mangled_rtti_symbol_demotes_a_non_public_type(self):
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="Result *")],
            types=[_rec("Result"), _rec("InternalCache")],
        )
        s = self._surf(snap)
        c = Change(
            kind=ChangeKind.RTTI_INHERITANCE_CHANGED,
            symbol="_ZTI13InternalCache",
            description="",
        )
        assert classify_change_surface(c, s, s) == (False, REASON_NON_PUBLIC_TYPE)

    def test_unmangled_symbol_is_unaffected(self):
        # demangle() is a no-op (returns None) for an already-plain type/
        # member spelling, so every pre-existing (non-mangled-symbol) case
        # is unchanged by this fix.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="Result *")],
            types=[_rec("Result"), _rec("InternalCache")],
        )
        s = self._surf(snap)
        c = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="InternalCache", description=""
        )
        assert classify_change_surface(c, s, s) == (False, REASON_NON_PUBLIC_TYPE)

    def test_mangled_vtable_symbol_resolves_without_an_external_demangler(self):
        # Codex review, fresh evidence: the vtable/RTTI/VTT owner-scope
        # resolution must not depend on the optional cxxfilt/c++filt tool
        # -- a policy-affecting classification (public-surface scoping)
        # would otherwise silently vary by whether that tool happens to be
        # installed on the host. `surface.py` no longer imports `demangle`
        # at all for this shape (not even as a fallback -- see the
        # template-specialization test below), so patching the shared
        # `abicheck.demangle.demangle()` entry point to always fail (as it
        # already does on a host with neither cxxfilt nor c++filt) proves
        # the in-process structural parser is what resolves it, on every
        # host, unconditionally.
        import abicheck.demangle as demangle_mod

        original = demangle_mod.demangle
        demangle_mod.demangle = lambda *a, **k: None
        try:
            snap = AbiSnapshot(
                library="l",
                version="1",
                functions=[_fn("api", ret="Result *")],
                types=[_rec("Result"), _rec("InternalCache")],
            )
            s = self._surf(snap)
            c = Change(
                kind=ChangeKind.VTABLE_SLOT_COUNT_CHANGED,
                symbol="_ZTV13InternalCache",
                description="",
            )
            assert classify_change_surface(c, s, s) == (False, REASON_NON_PUBLIC_TYPE)
            c_pub = Change(
                kind=ChangeKind.VTABLE_SLOT_COUNT_CHANGED,
                symbol="_ZTV6Result",
                description="",
            )
            assert classify_change_surface(c_pub, s, s) == (True, None)
        finally:
            demangle_mod.demangle = original

    def test_symbol_level_finding_never_computes_type_candidates(self):
        """CodeRabbit review: the demangle-aware `candidates` computation
        used to run unconditionally before the symbol-level early return,
        so an ordinary FUNC_REMOVED that `_classify_symbol_level` resolves
        on its own forked a `c++filt` (via `demangle()`) for a value it
        never used. Patch the shared `demangle()` entry point to raise if
        called at all, proving a symbol-level finding that
        `_classify_symbol_level` can resolve never reaches the
        type-candidate computation (which, since the fix for the
        reproducibility defect above, never calls `demangle()` anyway --
        this test still pins "never called", not just "never depended
        on")."""
        import abicheck.demangle as demangle_mod

        def _boom(*a, **k):
            raise AssertionError("demangle() must not be called")

        original = demangle_mod.demangle
        demangle_mod.demangle = _boom
        try:
            snap = AbiSnapshot(
                library="l",
                version="1",
                functions=[_fn("api", ret="Result *"), _fn("removed_fn")],
                types=[_rec("Result")],
            )
            s = self._surf(snap)
            c = Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol=_fn("removed_fn").mangled,
                description="",
            )
            # Not in public_symbols on either side (removed) -> a definite
            # symbol-level verdict, resolved without ever touching
            # candidates/demangle().
            assert classify_change_surface(c, s, s) is not None
        finally:
            demangle_mod.demangle = original

    def test_mangled_vtable_symbol_of_a_template_specialization_demotes_correctly(
        self,
    ):
        """The structural parser deliberately keeps a template owner's *raw
        encoded* argument list (e.g. ``Box<int>`` -> ``"BoxIiE"``, see
        ``itanium_scope_components``'s own docstring) for
        ``itanium_special_name_owner_scope_components`` (identity), which
        can never match the model's own canonical spelling. For
        *type-candidate* resolution specifically (this classification),
        ``itanium_special_name_owner_identifiers`` instead extracts every
        raw identifier token from the owner's scope path AND its
        template-argument list, structurally -- no external demangler
        involved at all, on any host (this used to fall back to
        ``demangle()`` for exactly this shape, which is the host-dependent
        reproducibility defect ``TestTemplatedOwnerHostIndependence`` below
        pins directly).

        Real GCC manglings (verified against a real ``c++filt``):
        ``_ZTV3BoxIiE`` demangles to ``"vtable for Box<int>"``, from which
        ``_type_identifiers`` extracts the bare ``"Box"`` token --
        ``itanium_special_name_owner_identifiers`` extracts the identical
        ``"Box"`` token structurally.
        """
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="Result *")],
            types=[_rec("Result"), _rec("Box")],
        )
        s = self._surf(snap)
        c = Change(
            kind=ChangeKind.VTABLE_SLOT_COUNT_CHANGED,
            symbol="_ZTV3BoxIiE",
            description="",
        )
        # "Box" is declared (in all_types) but never reachable from a public
        # function/variable -- a real non-public type, correctly demoted
        # via the structural template-argument identifier extraction.
        assert classify_change_surface(c, s, s) == (False, REASON_NON_PUBLIC_TYPE)

        c_pub = Change(
            kind=ChangeKind.VTABLE_SLOT_COUNT_CHANGED,
            symbol="_ZTV6ResultIiE",
            description="",
        )
        assert classify_change_surface(c_pub, s, s) == (True, None)


class TestTemplatedOwnerHostIndependence:
    """New defect 1 regression: a templated vtable/RTTI/VTT owner's
    public-surface classification must not vary by whether the optional
    ``cxxfilt``/``c++filt`` demangler happens to be installed on the host.

    Before the fix, ``surface.py``'s ``_resolve_type_candidates`` fell back
    to ``demangle()`` specifically for a templated owner (e.g. a libstdc++
    container instantiation, exactly oneDNN's real-world shape) --
    host-present, the class correctly demoted out of the public surface;
    host-absent, the same comparison silently kept it in-surface. This is a
    *bug-class* regression: it must hold for every representative templated
    special-name owner, not only the one reported symbol, and under both a
    present and an absent demangler -- proven here by monkeypatching
    ``surface.py``'s own bound ``demangle`` name (``abicheck.surface.
    demangle``) both ways and asserting the classification never changes.
    CodeRabbit review (round 9/10, fresh evidence): an earlier version of
    this test patched ``abicheck.demangle.demangle`` -- the *defining*
    module's own attribute -- instead. ``surface.py`` binds ``demangle`` via
    ``from .demangle import demangle``, so that patch left ``surface.py``'s
    own already-bound reference untouched; had this classification actually
    (incorrectly) still called through to the real demangler, the ``_boom``
    stand-in below would never have fired and the test would have passed
    anyway, proving nothing about whether demangling was actually avoided.

    **Scope of this invariant (round 8 correction):** it applies to every
    shape ``itanium_special_name_owner_scope_components``/
    ``itanium_special_name_owner_identifiers`` can structurally parse --
    plain and templated owners alike, which is every case in ``_CASES``
    below. It does NOT apply to a shape neither structural parser can
    parse at all (a standard `Ss`/`Sa`/... substitution alone, or a
    local-class owner) -- for those, ``demangle()`` is still the
    dependency-free-when-possible fallback, and legitimately classifies
    differently by host the same way every other kind already does (see
    ``TestDemanglerFallbackForUnparseableShapes`` below, which pins the
    opposite direction: that the fallback is NOT dropped for those
    shapes).
    """

    def _surf(self, snap):
        return compute_public_surface(snap)

    #: Representative libstdc++-shaped and user-shaped templated owners
    #: (mangled symbol, declared type name the owner is *composed of* that
    #: is present in `types=` but unreachable from any public function --
    #: i.e. the expected-non-public case).
    _CASES = [
        # dnnl::pool::vector<T>-shaped namespaced-internal template (avoids
        # a bare "impl"/"detail"/"internal" component -- that would trip
        # surface.py's unrelated anti-hiding rule for internal namespaces,
        # which always keeps the finding in-surface regardless of this fix).
        ("_ZTVN4dnnl4pool6vectorIiEE", "vector"),
        # A libstdc++-style container instantiation over a user type.
        ("_ZTVSt6vectorIN2ab3BarEE", "Bar"),
        # Nested template argument (Box<ab::Baz>).
        ("_ZTIN3BoxIN2ab3BazEEE", "Baz"),
        # Simple one-level template, no nested namespace.
        ("_ZTT3BoxIiE", "Box"),
    ]

    def test_classification_identical_with_and_without_a_demangler(self):
        import abicheck.surface as surface_mod

        for mangled, present_type in self._CASES:
            snap = AbiSnapshot(
                library="l",
                version="1",
                functions=[_fn("api", ret="Result *")],
                types=[_rec("Result"), _rec(present_type)],
            )
            s = self._surf(snap)
            change = Change(
                kind=ChangeKind.VTABLE_SLOT_COUNT_CHANGED,
                symbol=mangled,
                description="",
            )

            original = surface_mod.demangle
            try:
                surface_mod.demangle = lambda *a, **k: None  # simulate absent
                absent_result = classify_change_surface(change, s, s)

                def _boom(*a, **k):
                    raise AssertionError(
                        f"demangle() must not be called for {mangled!r}"
                    )

                surface_mod.demangle = _boom  # simulate a call that would crash
                present_result = classify_change_surface(change, s, s)
            finally:
                surface_mod.demangle = original

            assert (
                absent_result
                == present_result
                == (
                    False,
                    REASON_NON_PUBLIC_TYPE,
                )
            ), (mangled, absent_result, present_result)


class TestNamespaceComponentCannotMasqueradeAsOwner:
    """Findings-analysis-fixes review round 3, finding 4: a bare namespace
    *component* pulled out of a templated ``_ZTV``/``_ZTI``/``_ZTT`` owner's
    scope path must never stand in as its own independent implicated-type
    candidate.

    Before the fix, ``itanium_special_name_owner_identifiers`` flattened
    every identifier anywhere in the owner's scope path and template
    arguments into one undifferentiated set -- ``_ZTVN2ns7WrapperIiEE``
    (``ns::Wrapper<int>``) produced ``{"ns", "Wrapper"}``. If the snapshot
    happens to model an *unrelated* type literally named ``ns`` (reachable
    from nothing public) but has no ``ns::Wrapper<int>``/``Wrapper`` of its
    own, ``_classify_type_level`` would resolve ``known = {"ns"}`` -- not in
    ``public_types`` -- and confidently demote the finding as
    ``non-public-type``, even though the *real* owner (``ns::Wrapper<int>``)
    is completely unresolvable from this evidence and the conservative
    default for an unresolvable owner is "unknown, keep" (never hide a real
    break). This is the false-positive-demotion direction; the sibling test
    below (real internal-only owner, matching real ``real-break-preserved``
    coverage) proves the fix doesn't ALSO make every internal-only owner
    default to "keep" -- a genuinely resolvable, non-public owner must still
    demote as before.
    """

    def _surf(self, snap):
        return compute_public_surface(snap)

    def test_unrelated_type_sharing_a_bare_namespace_name_cannot_demote(self):
        # "ns" is modeled (so it participates in `all_types`) but is neither
        # public nor reachable from any public function -- exactly the
        # shape that would wrongly stand in for the real, unresolvable
        # `ns::Wrapper<int>` owner under the pre-fix flattened extraction.
        # No `ns::Wrapper`/`Wrapper` type is modeled at all: the real owner
        # is genuinely unresolvable from this snapshot's evidence.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="Result *")],
            types=[_rec("Result"), _rec("ns")],
        )
        s = self._surf(snap)
        change = Change(
            kind=ChangeKind.VTABLE_SLOT_COUNT_CHANGED,
            symbol="_ZTVN2ns7WrapperIiEE",
            description="",
        )
        # An unresolvable owner must default to "unknown, keep" -- never a
        # confident demotion borrowed from an unrelated same-named type.
        assert classify_change_surface(change, s, s) == (True, None)

    def test_real_internal_only_owner_still_demotes(self):
        # Sibling real-break-preserved case: when the owner genuinely *is*
        # resolvable and non-public (`ns::Wrapper` itself modeled, matching
        # the qualified-owner candidate the fix still produces), the
        # existing demotion behavior for a confidently non-public type must
        # be completely unaffected by this fix.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="Result *")],
            types=[_rec("Result"), _rec("ns::Wrapper")],
        )
        s = self._surf(snap)
        change = Change(
            kind=ChangeKind.VTABLE_SLOT_COUNT_CHANGED,
            symbol="_ZTVN2ns7WrapperIiEE",
            description="",
        )
        assert classify_change_surface(change, s, s) == (
            False,
            REASON_NON_PUBLIC_TYPE,
        )

    def test_unrelated_type_sharing_the_bare_owner_tail_cannot_demote(self):
        """Round 9/10 finding (Codex review, fresh evidence): the round-3
        fix above stops a bare *namespace-component* (``"ns"``) from
        masquerading as the owner. It left a second-order hazard: the
        retained bare *owner tail* itself can still do the same thing.
        ``_ZTVN2ns7WrapperIiEE`` yields ``{"ns::Wrapper", "Wrapper"}`` --
        if ``ns::Wrapper`` is absent from the snapshot (the real owner
        genuinely unresolvable) but an unrelated, unreachable record
        happens to be named bare ``Wrapper`` (no namespace at all -- not
        the round-3 case), that unrelated record must not stand in for the
        real, unresolvable owner either.

        The snapshot also models an unrelated *qualified* type
        (``other::Thing``) so the model demonstrably tracks qualification
        (the real-world shape: DWARF-derived snapshots always store a
        record's fully-qualified name, `dwarf_snapshot.py`'s
        ``RecordType(name=qualified, ...)``) -- distinguishing this from
        every other test in this file, which deliberately models every
        type bare-only and relies on bare-tail matching for that
        legitimately-unqualified-snapshot shape (see
        ``TestTemplatedOwnerHostIndependence``'s own cases, which this fix
        must not regress).
        """
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="Result *")],
            # A bare, unrelated `Wrapper` (not `ns::Wrapper`) -- unreachable
            # from anything public. No `ns::Wrapper`/`ns` type modeled at
            # all: the real owner is genuinely unresolvable. `other::Thing`
            # is present purely to prove the model tracks qualification.
            types=[_rec("Result"), _rec("Wrapper"), _rec("other::Thing")],
        )
        s = self._surf(snap)
        change = Change(
            kind=ChangeKind.VTABLE_SLOT_COUNT_CHANGED,
            symbol="_ZTVN2ns7WrapperIiEE",
            description="",
        )
        # Unresolvable owner -> "unknown, keep", never a confident demotion
        # borrowed from the unrelated bare-named record.
        assert classify_change_surface(change, s, s) == (True, None)

    def test_castxml_style_bare_record_confirms_via_qualified_key_index(self):
        """Codex review, round 11: a further refinement of the round-9/10
        bare-owner-tail fix above. CastXML/Clang records are indexed in
        ``PublicSurface.all_types`` only by their *bare* leaf name, while
        the real ``ns::Foo`` identity is retained separately in
        ``origin_by_qualified_key`` (``policy/public_surface.py``). The
        round-10 fix tested a candidate's qualified form only against
        ``all_types``, so a real, modeled-but-private owner indexed under
        ``origin_by_qualified_key`` was wrongly treated as *unresolvable*
        (over-conservative "unknown, keep") instead of being confidently
        demoted as non-public -- even though its real qualified origin was
        available all along. An unrelated DWARF-style qualified record
        (``other::Thing``) is included purely to trigger
        ``tracks_qualified_names``, matching the exact shape Codex reported.
        """
        foo = RecordType(
            name="Foo", kind="struct", size_bits=64, qualified_name="ns::Foo"
        )
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="int")],  # Foo itself is NOT reachable
            types=[foo, _rec("other::Thing")],
        )
        s = self._surf(snap)
        change = Change(
            kind=ChangeKind.VTABLE_SLOT_COUNT_CHANGED,
            symbol="_ZTVN2ns3FooE",
            description="",
        )
        # The real owner IS modeled (just under origin_by_qualified_key, not
        # all_types) and is genuinely non-public -> a confident demotion,
        # not the "unknown, keep" fallback a round-10-only fix would give.
        assert classify_change_surface(change, s, s) == (
            False,
            REASON_NON_PUBLIC_TYPE,
        )


class TestDemanglerFallbackForUnparseableShapes:
    """Round 8 finding: ``itanium_special_name_owner_scope_components``/
    ``itanium_special_name_owner_identifiers`` return ``None`` -- can't
    parse the shape at all -- for a valid Itanium special name outside
    their structural subset, e.g. a local-class vtable owner
    (``_ZTVZ3foovE1A``). The round-1 fix removed the ``demangle()``
    fallback entirely for this ``owner_scope is None`` branch, which
    over-scoped the host-independence invariant
    ``TestTemplatedOwnerHostIndependence`` establishes above (that
    invariant is about shapes the structural parser CAN handle, never
    about shapes it flatly can't) -- on a demangler-equipped host, an
    otherwise-known private owner was silently demoted to "unknown, keep"
    instead of correctly resolving and demoting. This restores the
    fallback for exactly the unparseable branch and pins both directions:
    a demangler-equipped host resolves and demotes; a demangler-less host
    conservatively keeps (never a false break), matching the same
    accepted pattern ``FUNC_REMOVED_ELF_ONLY``'s ``demangled_symbol``
    already uses everywhere else in this codebase.

    The bare ``Ss`` standard substitution for ``std::basic_string<...>``
    (``_ZTVSs``) used to be this class's other case, but round 9/10's
    macOS CI investigation found it was never actually a "structural
    parser can't handle this shape" case in the first place -- it is a
    fixed, ABI-mandated abbreviation (Itanium ABI Section 5.9), and
    ``mangled_name.py``'s ``_STANDARD_SUBSTITUTION_OWNER_SCOPE`` now
    resolves all six such codes structurally, the same way ``St`` was
    already resolved for the scope-*prefix* case. It moved to
    ``TestStandardSubstitutionOwnerResolvesStructurally`` below, which
    pins that host-independence directly rather than through
    ``demangle()`` (real macOS CI evidence: GNU's and LLVM's demanglers
    render ``Ss`` using different spellings -- the full template form vs.
    a shorthand alias -- so relying on ``demangle()`` output text for this
    shape was never actually host-independent despite a demangler being
    present on both hosts; see that class's own docstring).
    """

    def _surf(self, snap):
        return compute_public_surface(snap)

    #: (mangled symbol, declared type name the demangled spelling is
    #: composed of, present in `types=` but unreachable from any public
    #: function -- i.e. the expected-non-public case once resolved).
    _CASES = [
        ("_ZTVZ3foovE1A", "A"),  # a local-class vtable owner.
    ]

    def test_structural_parsers_reject_both_shapes(self):
        # Sanity precondition this whole test class rests on: the shape
        # is genuinely outside the structural parsers' subset, so the
        # `owner_scope is None` fallback branch is actually exercised
        # rather than accidentally testing the structural path instead.
        for mangled, _ in self._CASES:
            assert itanium_special_name_owner_scope_components(mangled) is None
            assert itanium_special_name_owner_identifiers(mangled) is None

    @needs_demangler
    def test_demangler_present_resolves_and_demotes(self):
        for mangled, present_type in self._CASES:
            snap = AbiSnapshot(
                library="l",
                version="1",
                functions=[_fn("api", ret="Result *")],
                types=[_rec("Result"), _rec(present_type)],
            )
            s = self._surf(snap)
            change = Change(
                kind=ChangeKind.VTABLE_SLOT_COUNT_CHANGED,
                symbol=mangled,
                description="",
            )
            # Real c++filt-backed demangle() (available in this test
            # environment) -- proves the fallback is actually wired, not
            # merely mocked to succeed. `surface.py` binds `demangle` via
            # `from .demangle import demangle`, so patching it must target
            # `abicheck.surface.demangle` (the imported name), not
            # `abicheck.demangle.demangle` (the defining module's own
            # attribute) -- the latter would leave `surface.py`'s already-
            # bound reference untouched.
            assert classify_change_surface(change, s, s) == (
                False,
                REASON_NON_PUBLIC_TYPE,
            ), mangled

    def test_demangler_absent_conservatively_keeps(self):
        import abicheck.surface as surface_mod

        for mangled, present_type in self._CASES:
            snap = AbiSnapshot(
                library="l",
                version="1",
                functions=[_fn("api", ret="Result *")],
                types=[_rec("Result"), _rec(present_type)],
            )
            s = self._surf(snap)
            change = Change(
                kind=ChangeKind.VTABLE_SLOT_COUNT_CHANGED,
                symbol=mangled,
                description="",
            )
            original = surface_mod.demangle
            try:
                surface_mod.demangle = lambda *a, **k: None  # simulate absent
                # Unresolvable without a demangler -- conservative
                # "unknown, keep" (never a false break), same as every
                # other kind's demangler-less default.
                assert classify_change_surface(change, s, s) == (True, None), mangled
            finally:
                surface_mod.demangle = original

    @needs_demangler
    def test_classification_may_legitimately_differ_by_host_for_these_shapes(self):
        # Explicit converse of TestTemplatedOwnerHostIndependence: for a
        # shape the structural parser can't handle at all, presence vs.
        # absence of a demangler is allowed to change the classification
        # (present -> correctly demoted; absent -> conservatively kept).
        # This is not a regression of the host-independence invariant,
        # which is scoped to shapes the structural parser CAN handle.
        import abicheck.surface as surface_mod

        mangled, present_type = self._CASES[0]
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="Result *")],
            types=[_rec("Result"), _rec(present_type)],
        )
        s = self._surf(snap)
        change = Change(
            kind=ChangeKind.VTABLE_SLOT_COUNT_CHANGED,
            symbol=mangled,
            description="",
        )
        original = surface_mod.demangle
        try:
            surface_mod.demangle = lambda *a, **k: None
            absent_result = classify_change_surface(change, s, s)
        finally:
            surface_mod.demangle = original
        present_result = classify_change_surface(change, s, s)
        assert absent_result != present_result


class TestStandardSubstitutionOwnerResolvesStructurally:
    """Round 9/10 finding (Codex review, fresh evidence, real macOS CI
    failure): the bare Itanium standard-substitution owner shapes (``Ss``,
    ``Sa``, ``Sb``, ``Si``, ``So``, ``Sd`` -- Itanium ABI Section 5.9) used
    to fall through to ``TestDemanglerFallbackForUnparseableShapes``'s
    ``demangle()`` fallback, on the theory that they were just another
    unparseable-by-the-structural-parser shape like a local-class vtable
    owner. They are not: each is a *fixed*, ABI-mandated abbreviation for
    one specific standard-library type, so they can (and, per this
    codebase's own "fix the cause, not the instance" principle, should) be
    resolved structurally, exactly like ``St``'s scope-prefix case already
    is.

    This matters beyond tidiness: relying on ``demangle()`` for this shape
    was never actually host-independent the way the class-level docstring
    up top assumed. A real macOS CI run demangled ``_ZTVSs`` via LLVM's
    demangler (the only backend reachable there once the ``cxxfilt`` PyPI
    package's libstdc++-only ``__cxa_demangle`` binding fails to load) and
    got a shorthand alias spelling, not GNU's fully-spelled
    ``std::basic_string<char, std::char_traits<char>, std::allocator<char>
    >`` -- so the exact same comparison, run on two hosts that each had a
    real, working demangler installed, produced two different type-
    candidate sets purely from that spelling difference. Resolving these
    six codes structurally in ``mangled_name.py`` (never through
    ``demangle()`` at all) removes that gap entirely: the "std::string"
    and "std::vector<int>" cases now classify identically on every host,
    demangler installed or not.
    """

    def _surf(self, snap):
        return compute_public_surface(snap)

    def test_bare_substitution_owner_resolves_without_a_demangler(self):
        import abicheck.surface as surface_mod

        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="Result *")],
            types=[_rec("Result"), _rec("basic_string")],
        )
        s = self._surf(snap)
        change = Change(
            kind=ChangeKind.VTABLE_SLOT_COUNT_CHANGED,
            symbol="_ZTVSs",
            description="",
        )
        # `demangle()` forced to fail -- proves the classification does not
        # depend on it at all for this shape, unlike the genuinely
        # unparseable shapes `TestDemanglerFallbackForUnparseableShapes`
        # covers.
        original = surface_mod.demangle
        try:
            surface_mod.demangle = lambda *a, **k: None
            assert classify_change_surface(change, s, s) == (
                False,
                REASON_NON_PUBLIC_TYPE,
            )
        finally:
            surface_mod.demangle = original

    def test_structural_parsers_resolve_all_six_standard_substitutions(self):
        # Each code's structural resolution matches its known ABI expansion
        # -- not just "returns something non-None".
        expected = {
            "_ZTVSa": ("std", "allocator"),
            "_ZTVSb": ("std", "basic_string"),
            "_ZTVSs": ("std", "basic_string"),
            "_ZTVSi": ("std", "basic_istream"),
            "_ZTVSo": ("std", "basic_ostream"),
            "_ZTVSd": ("std", "basic_iostream"),
        }
        for mangled, scope in expected.items():
            components, template_positions = (
                itanium_special_name_owner_scope_components(mangled)
            )
            assert components == list(scope), mangled
            assert template_positions == frozenset()
            identifiers = itanium_special_name_owner_identifiers(mangled)
            assert identifiers == frozenset({"::".join(scope), scope[-1]}), mangled


class TestStdSubstitutionOwnerDemotesLikeItsFullySpelledEquivalent:
    """Findings-analysis-fixes review round 4, finding 3: the canonical
    Itanium ``St`` substitution for ``std::`` used to drop the qualified
    ``std::X`` owner candidate entirely -- ``_ZTVSt6vectorIiE`` produced
    only ``{"vector"}``, never ``{"std::vector", "vector"}`` the way the
    ordinary, fully-spelled ``_ZTVN3std6vectorIiEE`` form already did. A
    snapshot modeling an internal ``std::vector`` record but no bare
    ``vector`` type read the substituted owner as unknown (keep) while
    correctly demoting the fully-spelled one -- the exact same real class,
    the exact same real ABI churn, classified two different ways purely by
    which of the two equivalent mangled spellings the compiler happened to
    emit for a given translation unit."""

    def _surf(self, snap):
        return compute_public_surface(snap)

    def test_substituted_and_spelled_out_forms_classify_identically(self):
        # Only the qualified `std::vector` is modeled (not a bare `vector`)
        # and it is neither public nor reachable -- the shape that only
        # demotes correctly once the qualified candidate is present.
        snap = AbiSnapshot(
            library="l",
            version="1",
            functions=[_fn("api", ret="Result *")],
            types=[_rec("Result"), _rec("std::vector")],
        )
        s = self._surf(snap)
        substituted = Change(
            kind=ChangeKind.VTABLE_SLOT_COUNT_CHANGED,
            symbol="_ZTVSt6vectorIiE",
            description="",
        )
        spelled_out = Change(
            kind=ChangeKind.VTABLE_SLOT_COUNT_CHANGED,
            symbol="_ZTVN3std6vectorIiEE",
            description="",
        )
        expected = (False, REASON_NON_PUBLIC_TYPE)
        assert classify_change_surface(substituted, s, s) == expected
        assert classify_change_surface(spelled_out, s, s) == expected
