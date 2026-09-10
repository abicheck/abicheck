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
from abicheck.surface import (
    REASON_NON_PUBLIC_TYPE,
    classify_change_surface,
    compute_public_surface,
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
    present and an absent demangler -- proven here by monkeypatching the
    shared ``abicheck.demangle.demangle()`` entry point both ways and
    asserting the classification never changes.
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
        import abicheck.demangle as demangle_mod

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

            original = demangle_mod.demangle
            try:
                demangle_mod.demangle = lambda *a, **k: None  # simulate absent
                absent_result = classify_change_surface(change, s, s)

                def _boom(*a, **k):
                    raise AssertionError(
                        f"demangle() must not be called for {mangled!r}"
                    )

                demangle_mod.demangle = _boom  # simulate a call that would crash
                present_result = classify_change_surface(change, s, s)
            finally:
                demangle_mod.demangle = original

            assert (
                absent_result
                == present_result
                == (
                    False,
                    REASON_NON_PUBLIC_TYPE,
                )
            ), (mangled, absent_result, present_result)
