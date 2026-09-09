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
        # installed on the host. Patching demangle() to always fail (as it
        # already does on a host with neither cxxfilt nor c++filt) proves
        # the in-process structural parser, not demangle(), is what
        # resolves this shape.
        import abicheck.surface as surface_mod

        original = surface_mod.demangle
        surface_mod.demangle = lambda *a, **k: None
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
            surface_mod.demangle = original

    def test_symbol_level_finding_never_computes_type_candidates(self):
        """CodeRabbit review: the demangle-aware `candidates` computation
        used to run unconditionally before the symbol-level early return,
        so an ordinary FUNC_REMOVED that `_classify_symbol_level` resolves
        on its own forked a `c++filt` (via `demangle()`) for a value it
        never used. Patch `demangle` to raise if called at all, proving a
        symbol-level finding that `_classify_symbol_level` can resolve
        never reaches the type-candidate computation."""
        import abicheck.surface as surface_mod

        def _boom(*a, **k):
            raise AssertionError("demangle() must not be called")

        original = surface_mod.demangle
        surface_mod.demangle = _boom
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
            surface_mod.demangle = original

    def test_mangled_vtable_symbol_of_a_template_specialization_demotes_correctly(
        self,
    ):
        """Codex review, fresh evidence: the structural parser deliberately
        keeps a template owner's *raw encoded* argument list (e.g.
        ``Box<int>`` -> ``"BoxIiE"``, see
        ``itanium_scope_components``'s own docstring), which can never
        match the model's own canonical spelling (``"Box<int>"`` /
        the bare ``"Box"`` token ``_type_identifiers`` extracts from it).
        Left unhandled, a templated vtable/RTTI owner was silently *worse*
        off than before this parser existed: not merely "unmatched,
        conservatively kept" but never even attempted, since the raw
        structural spelling opaquely mismatches every real type name. The
        fix falls back to ``demangle()`` specifically when the owner's own
        component carries a template-argument list.

        Real GCC manglings (verified against a real ``c++filt``):
        ``_ZTV3BoxIiE`` demangles to ``"vtable for Box<int>"``, from which
        ``_type_identifiers`` extracts the bare ``"Box"`` token.
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
        # only because the template-argument list forced the demangler
        # fallback instead of the unmatchable raw structural spelling.
        assert classify_change_surface(c, s, s) == (False, REASON_NON_PUBLIC_TYPE)

        c_pub = Change(
            kind=ChangeKind.VTABLE_SLOT_COUNT_CHANGED,
            symbol="_ZTV6ResultIiE",
            description="",
        )
        assert classify_change_surface(c_pub, s, s) == (True, None)
