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

"""The export-to-owner join behind ``func_removed_elf_only``'s ctor/dtor
exemption, and the export-loss classes it must never swallow.

Every Itanium spelling asserted here was produced by a real ``g++ 13``
compile and read back with ``nm -D`` -- never hand-abbreviated -- and the two
runtime claims (a client built once against OLD and run, eager-binding,
against NEW) were measured with a real loader, not inferred from this tool's
own verdict. The fixtures those measurements came from are reproduced in
``NATIVE_CONTROL_SOURCES`` below so the recorded manglings can be
regenerated.
"""

from __future__ import annotations

import pytest

from abicheck.compare.export_owner_resolution import (
    OwnerJoin,
    declared_special_members,
    special_member_export_coverage,
)
from abicheck.compare.undeclared_exports import _diff_undeclared_exports
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, RecordType, ScopeOrigin
from abicheck.model.mangled_name import itanium_scope_components
from abicheck.model.owner_recovery import itanium_special_member_owner

#: The C++ the recorded manglings below were compiled from (g++ 13.3,
#: ``-std=c++17 -fPIC -shared``). Kept as text so a reader can regenerate
#: them rather than trust a transcription.
NATIVE_CONTROL_SOURCES = """
namespace api {
struct Base { Base(int); virtual ~Base(); };
struct Derived : Base { using Base::Base; };          // -> CI1/CI2
template <typename T> struct Box { Box(){} ~Box(){} T get(){return T();} };
template struct Box<int>;                             // explicit instantiation
namespace inner { template <typename T> struct Box { Box(){} }; }
template struct inner::Box<double>;
namespace detail { struct Hidden { Hidden(); }; }
}
namespace other { struct Base { Base(); }; }          // bare-name collision
"""


def _snapshot(
    *,
    exports: list[str],
    declarations: list[Function],
    types: list[RecordType] | None = None,
) -> AbiSnapshot:
    snap = AbiSnapshot(
        library="libapi.so",
        version="1.0",
        from_headers=True,
        elf=ElfMetadata(machine="x86-64", symbols=[ElfSymbol(name=n) for n in exports]),
    )
    snap.functions = list(declarations)
    snap.types = list(types or [])
    return snap


def _ctor(owner: str, *, params: str = "", inline: bool) -> Function:
    return Function(
        name=owner.rpartition("::")[2],
        mangled=f"__abicheck_ctor__{owner}({params})",
        return_type="",
        origin=ScopeOrigin.PUBLIC_HEADER,
        is_inline=inline,
    )


def _dtor(owner: str, *, inline: bool) -> Function:
    return Function(
        name=f"~{owner.rpartition('::')[2]}",
        mangled=f"~{owner}",
        return_type="",
        origin=ScopeOrigin.PUBLIC_HEADER,
        is_inline=inline,
    )


def _reported(exports_old: list[str], old_decls, new_decls=None) -> set[str]:
    old = _snapshot(exports=exports_old, declarations=old_decls)
    new = _snapshot(
        exports=[], declarations=old_decls if new_decls is None else new_decls
    )
    return {c.symbol for c in _diff_undeclared_exports(old, new)}


# ---------------------------------------------------------------------------
# 1. The name side: real compiler manglings parse to the right owner
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mangled, member, owner, templated, inherited",
    [
        # Ordinary member ctor/dtor of a namespaced class.
        ("_ZN3api4BaseC1Ei", "{ctor}", "api::Base", False, False),
        ("_ZN3api4BaseC2Ei", "{ctor}", "api::Base", False, False),
        ("_ZN3api4BaseD0Ev", "{dtor}", "api::Base", False, False),
        ("_ZN3api4BaseD1Ev", "{dtor}", "api::Base", False, False),
        ("_ZN3api4BaseD2Ev", "{dtor}", "api::Base", False, False),
        # Inheriting constructors. The owner is the DERIVED class; the
        # `NS_4BaseE` that follows the CI code is the base-type production
        # and must not be mistaken for the owner.
        ("_ZN3api7DerivedCI1NS_4BaseEEi", "{ctor}", "api::Derived", False, True),
        ("_ZN3api7DerivedCI2NS_4BaseEEi", "{ctor}", "api::Derived", False, True),
        # Class-template specializations: the recovered path names the
        # PRIMARY template and says so via the templated flag.
        ("_ZN3api3BoxIiEC1Ev", "{ctor}", "api::Box", True, False),
        ("_ZN3api3BoxIiED1Ev", "{dtor}", "api::Box", True, False),
        ("_ZN3api5inner3BoxIdEC1Ev", "{ctor}", "api::inner::Box", True, False),
        # Nested namespaces, and a class whose own name embeds "C1".
        ("_ZN3api6detail6HiddenC1Ev", "{ctor}", "api::detail::Hidden", False, False),
        ("_ZN6C1EvilIiEC1Ev", "{ctor}", "C1Evil", True, False),
        # The `St` scope abbreviation.
        ("_ZNSt6vectorIiEC1Ev", "{ctor}", "std::vector", True, False),
    ],
)
def test_real_manglings_resolve_to_their_owner(
    mangled: str, member: str, owner: str, templated: bool, inherited: bool
) -> None:
    resolved = itanium_special_member_owner(mangled)
    assert resolved is not None, mangled
    assert (
        resolved.member,
        resolved.qualified_owner,
        resolved.owner_carries_template_arguments,
        resolved.inherited,
    ) == (member, owner, templated, inherited)


@pytest.mark.parametrize(
    "mangled, why",
    [
        ("_ZTVN3api4BaseE", "a vtable is a special name, not a ctor/dtor"),
        ("_ZTIN3api4BaseE", "typeinfo, likewise"),
        ("_ZTSN3api4BaseE", "typeinfo name, likewise"),
        ("_ZN3api4Base1vEv", "an ordinary member function"),
        ("_Z4drawi", "a free function has no owner"),
        ("_ZN3api4BaseaSERKS0_", "operator=, not a ctor/dtor"),
        ("malloc", "not mangled at all"),
        ("_ZN3api", "truncated"),
    ],
)
def test_non_special_members_reach_no_verdict(mangled: str, why: str) -> None:
    """``None`` is "no verdict", and the detector treats it as keep-reporting.
    Pinning the vtable/RTTI rows is what keeps the exemption disjoint from the
    export losses the detector exists to catch."""
    assert itanium_special_member_owner(mangled) is None, why
    assert (
        special_member_export_coverage(
            mangled,
            _snapshot(exports=[], declarations=[]),
            _snapshot(exports=[], declarations=[]),
        ).join
        is OwnerJoin.UNSUPPORTED
    )


def test_inherited_ctor_owner_is_not_the_base_class() -> None:
    """The single mutation this whole parsing change exists to foreclose:
    reading `NS_4BaseE` as the owner (what a demangler prints first) would
    attribute a derived class's export loss to its base, and every downstream
    ownership consumer -- surface scoping, internal-namespace classification --
    would follow it to the wrong scope."""
    resolved = itanium_special_member_owner("_ZN3api7DerivedCI1NS_4BaseEEi")
    assert resolved is not None
    assert resolved.owner_components == ("api", "Derived")
    assert "Base" not in resolved.qualified_owner
    assert itanium_scope_components("_ZN3api7DerivedCI1NS_4BaseEEi") == [
        "api",
        "Derived",
        "{ctor}",
    ]


def test_inherited_ctor_marker_span_still_declines_the_form() -> None:
    """A sibling-mangling rewriter locates the 2-character C1/D1 marker and
    substitutes it. `CI1` is three characters with a base-type encoding
    attached, so admitting it there would hand that caller a corrupt symbol.
    Scope recovery gains the form; span rewriting deliberately does not."""
    from abicheck.diff_cxx_rules import itanium_ctor_dtor_marker_span

    assert itanium_ctor_dtor_marker_span("_ZN3api7DerivedCI1NS_4BaseEEi") is None
    assert itanium_ctor_dtor_marker_span("_ZN3api4BaseC1Ei") == (12, 14)


# ---------------------------------------------------------------------------
# 2. The declaration side: qualified-first, ambiguity-safe
# ---------------------------------------------------------------------------


def test_declaration_index_is_keyed_by_the_qualified_owner() -> None:
    snap = _snapshot(
        exports=[],
        declarations=[
            _ctor("api::Box<int>", inline=True),
            _ctor("api::Box<int>", params="const Box<int>&", inline=True),
            _dtor("api::Box<int>", inline=True),
            _ctor("other::Base", inline=False),
        ],
    )
    index = declared_special_members(snap)
    assert set(index) == {
        ("{ctor}", "api::Box"),
        ("{dtor}", "api::Box"),
        ("{ctor}", "other::Base"),
    }
    assert len(index[("{ctor}", "api::Box")]) == 2


def test_a_bare_name_collision_is_never_a_resolution() -> None:
    """The unsound join this module replaced. ``type_by_name`` is keyed by the
    *unqualified* record name, so an unrelated ``api::Base`` stood in for the
    real, undeclared ``other::Base`` and suppressed its export loss. Resolving
    through the qualified declaration index makes that a visible AMBIGUOUS
    state instead of a silent match."""
    decls = [_ctor("api::Base", params="int", inline=True)]
    old = _snapshot(
        exports=["_ZN5other4BaseC1Ev"],
        declarations=decls,
        types=[RecordType(name="Base", kind="struct")],
    )
    new = _snapshot(exports=[], declarations=decls, types=list(old.types))
    coverage = special_member_export_coverage("_ZN5other4BaseC1Ev", old, new)
    assert coverage.join is OwnerJoin.AMBIGUOUS
    assert coverage.owner == "other::Base"
    assert not coverage.covered
    assert _reported(["_ZN5other4BaseC1Ev"], decls) == {"_ZN5other4BaseC1Ev"}


def test_the_same_bare_owner_in_two_namespaces_resolves_independently() -> None:
    """Public/private collision: ``api::Base`` is inline-declared and exempt,
    ``api::detail::Base`` is not declared at all and stays reported. One
    snapshot, one run, two different answers -- which a bare-name join cannot
    produce at all."""
    decls = [_ctor("api::Base", inline=True), _dtor("api::Base", inline=True)]
    assert _reported(["_ZN3api4BaseC1Ev", "_ZN3api6detail4BaseC1Ev"], decls) == {
        "_ZN3api6detail4BaseC1Ev"
    }


# ---------------------------------------------------------------------------
# 3. Coverage: the four facts, each one separately load-bearing
# ---------------------------------------------------------------------------


def test_inline_declaration_unchanged_on_both_sides_is_covered() -> None:
    """Emission churn with no obligation: the header offers the definition,
    so every consumer TU already emits its own copy. Runtime-verified -- a
    client built against a header carrying in-class definitions runs against
    both the -O0 and the -O2 build (exit 0 both ways); see `CPP_HDR` in
    `tests/test_cross_compiler_fp.py`."""
    decls = [_ctor("api::Inline", inline=True), _dtor("api::Inline", inline=True)]
    old = _snapshot(exports=["_ZN3api6InlineC1Ev"], declarations=decls)
    new = _snapshot(exports=[], declarations=decls)
    coverage = special_member_export_coverage("_ZN3api6InlineC1Ev", old, new)
    assert coverage.covered
    assert coverage.reason == "inline_declaration_unchanged_on_both_sides"
    assert _reported(["_ZN3api6InlineC1Ev", "_ZN3api6InlineD1Ev"], decls) == set()


def test_an_out_of_line_constructor_loss_stays_reported() -> None:
    """Native control 1, runtime-verified: headers, source and SONAME
    unchanged, a version script localizing only ``_ZN3api6WidgetC1Ev``, and a
    client built once against OLD dies against NEW with
    ``undefined symbol: _ZN3api6WidgetC1Ev``. "The owning class is still
    declared" is therefore not a licence to drop this finding -- which is
    exactly what the replaced predicate did."""
    decls = [_ctor("api::Widget", inline=False), _dtor("api::Widget", inline=False)]
    coverage = special_member_export_coverage(
        "_ZN3api6WidgetC1Ev",
        _snapshot(exports=["_ZN3api6WidgetC1Ev"], declarations=decls),
        _snapshot(exports=[], declarations=decls),
    )
    assert not coverage.covered
    assert coverage.reason == "out_of_line_definition"
    assert _reported(["_ZN3api6WidgetC1Ev"], decls) == {"_ZN3api6WidgetC1Ev"}


def test_a_weak_template_instantiation_loss_stays_reported() -> None:
    """Native control 2, runtime-verified: ``_ZN3api3BoxIiEC1Ev`` is a
    ``W``/COMDAT symbol from an explicit instantiation, the consumer declares
    ``extern template`` and so emits nothing itself, and localizing it kills
    the same already-linked client. Neither "it is weak", nor "it is a
    template", nor "the primary template is declared inline" licenses
    dropping it: the primary template's declaration is evidence about the
    template, not about this specialization's binary obligation."""
    decls = [_ctor("api::Box<int>", inline=True), _dtor("api::Box<int>", inline=True)]
    coverage = special_member_export_coverage(
        "_ZN3api3BoxIiEC1Ev",
        _snapshot(exports=["_ZN3api3BoxIiEC1Ev"], declarations=decls),
        _snapshot(exports=[], declarations=decls),
    )
    assert not coverage.covered
    assert coverage.reason == "template_specialization"
    assert _reported(["_ZN3api3BoxIiEC1Ev"], decls) == {"_ZN3api3BoxIiEC1Ev"}


def test_an_inherited_constructor_loss_of_an_undeclared_owner_stays_reported() -> None:
    """A derived class nobody declares has no declaration-aware diff to hand
    the symbol to. Before the ``CI`` forms parsed at all, this reached the
    same outcome by accident (the parser failed outright); now it reaches it
    because the owner genuinely does not resolve, and the sibling test below
    shows the distinction is real."""
    decls = [_ctor("api::Base", params="int", inline=True)]
    coverage = special_member_export_coverage(
        "_ZN3api7DerivedCI1NS_4BaseEEi",
        _snapshot(exports=["_ZN3api7DerivedCI1NS_4BaseEEi"], declarations=decls),
        _snapshot(exports=[], declarations=decls),
    )
    assert coverage.join is OwnerJoin.UNRESOLVED
    assert coverage.owner == "api::Derived"
    assert not coverage.covered


def test_an_inherited_constructor_of_an_inline_declared_owner_is_covered() -> None:
    """The positive half: with the derived class's own special members
    declared inline on both sides, the inheriting constructor resolves to it
    and is covered. This is what the ``CI`` parse buys -- before it, the name
    reached no verdict at all and the exemption could never apply however the
    headers were written."""
    decls = [
        _ctor("api::Derived", params="int", inline=True),
        _dtor("api::Derived", inline=True),
    ]
    coverage = special_member_export_coverage(
        "_ZN3api7DerivedCI1NS_4BaseEEi",
        _snapshot(exports=["_ZN3api7DerivedCI1NS_4BaseEEi"], declarations=decls),
        _snapshot(exports=[], declarations=decls),
    )
    assert coverage.covered
    assert coverage.inherited


def test_a_declaration_present_on_one_side_only_stays_reported() -> None:
    """A declaration that changed is a declaration change, not emission
    churn. The exemption's premise is that the headers said the same thing on
    both sides; when they did not, this detector must not absorb the
    difference."""
    old_decls = [_ctor("api::Gone", inline=True), _dtor("api::Gone", inline=True)]
    coverage = special_member_export_coverage(
        "_ZN3api4GoneC1Ev",
        _snapshot(exports=["_ZN3api4GoneC1Ev"], declarations=old_decls),
        _snapshot(exports=[], declarations=[]),
    )
    assert coverage.reason == "owner_declared_on_one_side_only"
    assert not coverage.covered
    assert _reported(["_ZN3api4GoneC1Ev"], old_decls, new_decls=[]) == {
        "_ZN3api4GoneC1Ev"
    }


def test_one_out_of_line_overload_keeps_the_whole_owner_reported() -> None:
    """A ctor mangling does not say which overload it belongs to, so the
    exemption asks the weaker fact of *every* candidate. A class with one
    inline and one out-of-line constructor keeps its losses reported."""
    decls = [
        _ctor("api::Mixed", inline=True),
        _ctor("api::Mixed", params="int", inline=False),
        _dtor("api::Mixed", inline=True),
    ]
    assert _reported(["_ZN3api5MixedC1Ev"], decls) == {"_ZN3api5MixedC1Ev"}


# ---------------------------------------------------------------------------
# 4. The export losses the exemption must never reach (PR #1308's own fix)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lost",
    ["_ZTVN3api4BaseE", "_ZTIN3api4BaseE", "_ZTSN3api4BaseE", "_ZTTN3api4BaseE"],
)
def test_vtable_and_rtti_losses_survive_however_the_owner_is_declared(
    lost: str,
) -> None:
    """The false negative PR #1308 fixed, re-asserted against the strongest
    form of the exemption: the owning class is declared, inline, unchanged on
    both sides -- every condition that covers a constructor -- and the
    vtable/RTTI loss is still reported, because a consumer cannot emit one
    for itself."""
    decls = [_ctor("api::Base", inline=True), _dtor("api::Base", inline=True)]
    assert _reported([lost], decls) == {lost}


def test_a_lost_data_export_of_a_declared_class_survives() -> None:
    """The variable half of the same claim."""
    decls = [_ctor("api::Base", inline=True), _dtor("api::Base", inline=True)]
    assert _reported(["_ZN3api4Base7counterE"], decls) == {"_ZN3api4Base7counterE"}


def test_unchanged_input_reports_nothing() -> None:
    decls = [_ctor("api::Base", inline=True), _dtor("api::Base", inline=True)]
    exports = ["_ZN3api4BaseC1Ev", "_ZTVN3api4BaseE", "_ZN3api4Base1vEv"]
    old = _snapshot(exports=exports, declarations=decls)
    new = _snapshot(exports=list(exports), declarations=decls)
    assert _diff_undeclared_exports(old, new) == []


# ---------------------------------------------------------------------------
# 5. Mutation controls: each states which mutation it alone catches
# ---------------------------------------------------------------------------


def test_an_always_covered_guard_would_fail_something_here() -> None:
    """Vacuity guard for the whole file. If ``covered`` were hardwired True,
    every case in this list would go unreported; if it were hardwired False,
    the inline case would report. Both are asserted in one place so neither
    mutation can pass by satisfying only half the suite."""
    inline = [_ctor("api::Inline", inline=True), _dtor("api::Inline", inline=True)]
    out_of_line = [_ctor("api::Ool", inline=False), _dtor("api::Ool", inline=False)]
    assert _reported(["_ZN3api6InlineC1Ev"], inline) == set()
    assert _reported(["_ZN3api3OolC1Ev"], out_of_line) == {"_ZN3api3OolC1Ev"}


# ---------------------------------------------------------------------------
# 6. The "no verdict" paths, one test per way the parse or the join gives up
# ---------------------------------------------------------------------------
#
# The whole design rests on `None`/`unsupported` meaning "this parser reached
# no verdict", which the detector treats as keep-reporting. A defensive
# branch that no test reaches is one nobody has checked actually *gives up*
# rather than silently answering something -- and answering here drops a
# finding. So each way of giving up gets its own case.


@pytest.mark.parametrize(
    "mangled, why",
    [
        # `CI` followed by something that is not a ctor index. Not a real
        # compiler output; the point is that the CI production must not fire
        # on a coincidental `CI` prefix in the component stream.
        ("_ZN3api7DerivedCIxNS_4BaseEEi", "CI not followed by a ctor index"),
        # A GNU ABI tag whose length prefix does not describe a valid name.
        ("_ZN1CB9xIiEC1Ev", "malformed B<tag> length prefix"),
        # A vendor/unmodelled operator code in the leaf position.
        ("_ZN1CzzEv", "unmodelled operator code"),
        # The nested name closes with no ctor/dtor component at all.
        ("_ZN3apiEv", "nested name closes before any special member"),
        # `St` with nothing after it.
        ("_ZNStE", "no components after the std abbreviation"),
        # The component stream simply runs out, with no terminator and no
        # special member -- a truncated symbol, not a ctor.
        ("_ZN3api", "components exhausted before any special member"),
        # A conversion operator: the parser stops at it by design (its
        # target type is never decoded), and stopping is not an owner.
        ("_ZN1CcviEv", "conversion operator leaf is not a ctor/dtor"),
    ],
)
def test_every_way_the_parse_gives_up_reaches_no_verdict(
    mangled: str, why: str
) -> None:
    assert itanium_special_member_owner(mangled) is None, why


def test_a_truncated_inherited_ctor_still_resolves_its_owner() -> None:
    """The counterpart to the row above: an inheriting constructor's
    base-type encoding is deliberately never parsed, so a name truncated
    *after* the `CI<n>` code still yields the owner. Pinned because it is the
    visible consequence of stopping early, and a future "validate what
    follows" change would break it silently."""
    resolved = itanium_special_member_owner("_ZN3api7DerivedCI1")
    assert resolved is not None
    assert resolved.qualified_owner == "api::Derived"
    assert resolved.inherited


@pytest.mark.parametrize(
    "mangled",
    ["_ZTVQQQ", "_ZTV", "_ZTX3Foo"],
    ids=["unparseable-remainder", "empty-remainder", "unknown-special-code"],
)
def test_special_name_owner_recovery_gives_up_the_same_way(mangled: str) -> None:
    """`surface.py` demotes a finding when every resolvable owner candidate is
    confidently private, so a candidate set invented from an unparseable name
    would demote a real break. Both entry points must answer None."""
    from abicheck.model.owner_recovery import (
        itanium_special_name_owner_identifiers,
        itanium_special_name_owner_scope_components,
    )

    assert itanium_special_name_owner_identifiers(mangled) is None
    assert itanium_special_name_owner_scope_components(mangled) is None


def test_the_declaration_index_ignores_ordinary_manglings() -> None:
    """`function_map` holds mostly ordinary Itanium manglings; only the
    synthetic ctor/dtor placeholders are owner evidence. An ordinary mangling
    read as a placeholder would invent an owner from a method name."""
    snap = _snapshot(
        exports=[],
        declarations=[
            Function(
                name="v",
                mangled="_ZN3api4Base1vEv",
                return_type="void",
                origin=ScopeOrigin.PUBLIC_HEADER,
            ),
            _ctor("api::Base", inline=True),
        ],
    )
    assert set(declared_special_members(snap)) == {("{ctor}", "api::Base")}


def test_a_placeholder_with_no_owner_is_not_indexed() -> None:
    """A bare `~` (an owner-less destructor placeholder) must not index an
    empty-string owner, which every unresolvable name would then match."""
    snap = _snapshot(
        exports=[],
        declarations=[
            Function(
                name="~",
                mangled="~",
                return_type="",
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
        ],
    )
    assert declared_special_members(snap) == {}


def test_a_ctor_placeholder_without_a_parameter_list_still_indexes() -> None:
    """The parameter-list scan is angle-depth aware and may find no `(` at
    depth 0 at all; the key is then the whole remainder, not a truncation."""
    snap = _snapshot(
        exports=[],
        declarations=[
            Function(
                name="Base",
                mangled="__abicheck_ctor__api::Base",
                return_type="",
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
        ],
    )
    assert set(declared_special_members(snap)) == {("{ctor}", "api::Base")}
