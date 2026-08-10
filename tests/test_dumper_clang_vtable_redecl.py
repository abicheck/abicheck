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

"""Unit tests for G31 Phase C's direct-clang vtable reconstruction --
class-template REDECLARATION identity (kinds/defaults/names merging across
a ``previousDecl`` chain, and the ambiguity guard for two genuinely
unrelated declarations that coincidentally share a bare qualname).

Split out of ``test_dumper_clang_vtable.py`` once the combined file crossed
the AI-readiness 2000-line hard cap (``tests/CLAUDE.md``'s file-size
convention) -- shares that file's hand-built ``-ast-dump=json``-shaped node
builders via ``tests._dumper_clang_vtable_helpers`` rather than a second
copy. See ``dumper_clang_vtable.py``'s own ``_register_template_param_
metadata`` docstring for the full empirical history (third through ninth
review rounds) this module's fixtures reproduce.
"""

from __future__ import annotations

from tests._dumper_clang_vtable_helpers import (
    _base,
    _record,
    _specialization,
    _tu,
    _types,
)


def test_conflicting_nested_template_defaults_across_sibling_specializations_stay_ambiguous() -> (
    None
):
    """Codex review, fresh evidence (P1, third round): when TWO different
    explicit outer specializations each define their OWN same-named nested
    member template with DIFFERING defaults (``Outer<int>``'s ``template
    <class U=int> struct A`` vs. ``Outer<double>``'s ``template<class
    U=double> struct A``), both register under the identical BARE qualname
    ``"A"`` -- neither ``_index_template_param_kinds`` nor its two
    siblings ever extend scope through a ``ClassTemplateSpecializationDecl``.
    First-registration-wins previously trusted whichever was visited
    first, silently borrowing the WRONG default for the other -- confirmed
    end-to-end this left the base unresolvable and a real virtual-method
    addition on the derived class completely undetected (`NO_CHANGE`).
    Fixed by treating a conflicting second registration as genuinely
    ambiguous and dropping it entirely, rather than trusting either
    candidate.
    """
    outer_int_spec = {
        "kind": "ClassTemplateSpecializationDecl",
        "name": "Outer",
        "completeDefinition": True,
        "inner": [
            {"kind": "TemplateArgument", "type": {"qualType": "int"}},
            {
                "kind": "ClassTemplateDecl",
                "name": "A",
                "inner": [
                    {
                        "kind": "TemplateTypeParmDecl",
                        "name": "U",
                        "defaultArg": {
                            "kind": "TemplateArgument",
                            "type": {"qualType": "int"},
                        },
                    },
                    _record("A"),
                ],
            },
        ],
    }
    outer_double_spec = {
        "kind": "ClassTemplateSpecializationDecl",
        "name": "Outer",
        "completeDefinition": True,
        "inner": [
            {"kind": "TemplateArgument", "type": {"qualType": "double"}},
            {
                "kind": "ClassTemplateDecl",
                "name": "A",
                "inner": [
                    {
                        "kind": "TemplateTypeParmDecl",
                        "name": "U",
                        "defaultArg": {
                            "kind": "TemplateArgument",
                            "type": {"qualType": "double"},
                        },
                    },
                    _record("A"),
                    _specialization(
                        "A",
                        {
                            "kind": "CXXMethodDecl",
                            "name": "f",
                            "mangledName": "_ZN5OuterIdE1AIdE1fEv",
                            "type": {"qualType": "void ()"},
                            "virtual": True,
                        },
                        type_args=["double"],
                    ),
                ],
            },
        ],
    }
    root = _tu(
        {
            "kind": "ClassTemplateDecl",
            "name": "Outer",
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "T"},
                _record("Outer"),
            ],
        },
        outer_int_spec,
        outer_double_spec,
        _record("D", bases=[_base("Outer<double>::A<>")]),
    )
    from abicheck.dumper_clang_vtable import _index_template_param_defaults

    # Both nested "A"s are type-only parameters (the KINDS list -- type vs.
    # non-type -- is identical, [None], for both), so only the DEFAULTS
    # index (where the two genuinely differ, "int" vs. "double") is the one
    # that must detect the conflict and drop the ambiguous entry.
    assert "A" not in _index_template_param_defaults(root)
    # Safe degradation, not a crash or a wrong resolution: the base stays
    # unresolvable (same accepted false negative every other unresolvable-
    # base shape in this module degrades to), never a fabricated slot.
    types = _types(root)
    assert types["D"].vtable == []


def test_legal_redeclaration_with_renamed_params_still_resolves_dependent_default() -> (
    None
):
    """Codex review, fresh evidence (fourth round): the third round's
    conflicting-registration-is-ambiguous fix was too broad -- an ordinary,
    LEGAL C++ redeclaration of the SAME template with renamed parameters
    (`template<class T, class U=T> struct A;` followed by `template<class
    X, class Y> struct A {...};`) must not be treated as a genuine
    conflict just because the literal parameter NAMES differ across the
    two declarations. Clang always spells a dependent default's inherited
    text using the ORIGINAL declaration's parameter name (`"T"`, never
    renamed to `"X"`/`"Y"` on the later declaration, confirmed
    empirically), so dropping the NAMES metadata here broke dependent-
    default substitution for this ordinary shape -- distinguished from a
    genuine conflict via clang's own `previousDecl` link (present on every
    legal redeclaration, absent between two genuinely unrelated
    declarations that coincidentally share a bare qualname).
    """
    root = _tu(
        {
            "kind": "ClassTemplateDecl",
            "name": "A",
            "id": "0x1",
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "T"},
                {
                    "kind": "TemplateTypeParmDecl",
                    "name": "U",
                    "defaultArg": {
                        "kind": "TemplateArgument",
                        "type": {"qualType": "T"},
                    },
                },
            ],
        },
        {
            "kind": "ClassTemplateDecl",
            "name": "A",
            "id": "0x2",
            "previousDecl": "0x1",
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "X"},
                {"kind": "TemplateTypeParmDecl", "name": "Y"},
                _record("A"),
                _specialization(
                    "A",
                    {
                        "kind": "CXXMethodDecl",
                        "name": "f",
                        "mangledName": "_ZN1AIddE1fEv",
                        "type": {"qualType": "void ()"},
                        "virtual": True,
                    },
                    type_args=["double", "double"],
                ),
            ],
        },
        _record("D", bases=[_base("A<double>")]),
    )
    types = _types(root)
    assert types["D"].vtable == ["_ZN1AIddE1fEv"]
    assert types["D"].vptr_offset_bits == 0


def test_unrelated_coincidentally_equal_registration_does_not_corrupt_redecl_identity() -> (
    None
):
    """Codex review, fresh evidence (fifth round): the fourth round's
    ``previousDecl``-based redeclaration check was itself incomplete.
    ``_register_template_param_metadata`` also treated a bare *value*
    match (``existing == value``) as license to advance the tracked
    ``node_ids[qualname]`` -- but two genuinely UNRELATED nested member
    templates (``Outer<int>``'s and ``Outer<double>``'s own, neither ever
    redeclared) can trivially have byte-identical kinds/defaults/names by
    coincidence before either is redeclared: both are plain ``template
    <class T, class U=T> struct A;``. Advancing ``node_ids["A"]`` to the
    SECOND (unrelated) node's id there corrupted the chain for the FIRST
    entity's own later, legal out-of-class redeclaration with renamed
    parameters: its real ``previousDecl`` (pointing at the first node)
    then mismatched the corrupted tracked id and was wrongly dropped as
    ambiguous, losing the DEFAULTS metadata for "A" entirely and leaving
    ``Outer<int>::A<double>``'s dependent default unsubstituted -- the
    specialization was indexed as ``Outer<int>::A<double, double>``
    instead of the base's actual ``Outer<int>::A<double>``, leaving the
    inherited vtable unresolvable and a real added virtual method
    undetected. Fixed by only ever advancing ``node_ids`` on a CONFIRMED
    same-entity link (the first registration, or a ``previousDecl``
    match) -- a coincidental value match no longer reassigns whose id is
    being tracked.
    """
    outer_int_spec = {
        "kind": "ClassTemplateSpecializationDecl",
        "name": "Outer",
        "completeDefinition": True,
        "inner": [
            {"kind": "TemplateArgument", "type": {"qualType": "int"}},
            {
                "kind": "ClassTemplateDecl",
                "name": "A",
                "id": "0x1",
                "inner": [
                    {"kind": "TemplateTypeParmDecl", "name": "T"},
                    {
                        "kind": "TemplateTypeParmDecl",
                        "name": "U",
                        "defaultArg": {
                            "kind": "TemplateArgument",
                            "type": {"qualType": "T"},
                        },
                    },
                ],
            },
        ],
    }
    outer_double_spec = {
        "kind": "ClassTemplateSpecializationDecl",
        "name": "Outer",
        "completeDefinition": True,
        "inner": [
            {"kind": "TemplateArgument", "type": {"qualType": "double"}},
            {
                "kind": "ClassTemplateDecl",
                "name": "A",
                "id": "0x2",
                "inner": [
                    {"kind": "TemplateTypeParmDecl", "name": "T"},
                    {
                        "kind": "TemplateTypeParmDecl",
                        "name": "U",
                        "defaultArg": {
                            "kind": "TemplateArgument",
                            "type": {"qualType": "T"},
                        },
                    },
                ],
            },
        ],
    }
    # Out-of-class definition of Outer<int>::A, using RENAMED parameters --
    # a legal redeclaration of the FIRST entity (id "0x1"), never the
    # second (unrelated) one.
    outer_int_spec_redef = {
        "kind": "ClassTemplateSpecializationDecl",
        "name": "Outer",
        "completeDefinition": True,
        "inner": [
            {"kind": "TemplateArgument", "type": {"qualType": "int"}},
            {
                "kind": "ClassTemplateDecl",
                "name": "A",
                "id": "0x3",
                "previousDecl": "0x1",
                "inner": [
                    {"kind": "TemplateTypeParmDecl", "name": "X"},
                    {
                        "kind": "TemplateTypeParmDecl",
                        "name": "Y",
                        "defaultArg": {
                            "kind": "TemplateArgument",
                            "type": {"qualType": "X"},
                        },
                    },
                    _record("A"),
                    _specialization(
                        "A",
                        {
                            "kind": "CXXMethodDecl",
                            "name": "f",
                            "mangledName": "_ZN5OuterIiE1AIddE1fEv",
                            "type": {"qualType": "void ()"},
                            "virtual": True,
                        },
                        type_args=["double", "double"],
                    ),
                ],
            },
        ],
    }
    root = _tu(
        {
            "kind": "ClassTemplateDecl",
            "name": "Outer",
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "T"},
                _record("Outer"),
            ],
        },
        outer_int_spec,
        outer_double_spec,
        outer_int_spec_redef,
        _record("D", bases=[_base("Outer<int>::A<double>")]),
    )
    from abicheck.dumper_clang_vtable import _index_template_param_defaults

    # The redeclaration's own previousDecl link must still resolve
    # correctly despite the unrelated, coincidentally-equal middle
    # registration -- "A" stays in the index (not dropped as ambiguous),
    # keeping the FIRST declaration's defaults (dependent default spelled
    # with the ORIGINAL parameter name "T", not the redeclaration's "X").
    assert _index_template_param_defaults(root).get("A") == [None, "T"]
    types = _types(root)
    assert types["D"].vtable == ["_ZN5OuterIiE1AIddE1fEv"]
    assert types["D"].vptr_offset_bits == 0


def test_default_added_by_legal_redeclaration_still_resolves_dependent_default() -> None:
    """Codex review, fresh evidence (sixth round): a CONFIRMED redeclaration
    (linked via ``previousDecl``) previously kept the tracked metadata frozen
    at the FIRST declaration's value unconditionally -- correct for a
    renamed-parameter redeclaration (the fourth round's own case, where the
    original names/defaults must win), but wrong when the later declaration
    legally ADDS a default the first one never had:
    ``template<class T, class U> struct A;`` followed by ``template<class T,
    class U=T> struct A {...};`` is one C++ entity whose effective default
    for ``U`` only becomes visible on the SECOND declaration. Dropping it
    left ``_index_template_param_defaults`` with ``[None, None]`` for "A",
    so dependent-default substitution couldn't trim the trailing argument --
    mis-indexing ``A<double>`` as ``A<double, double>``, unable to match the
    base's actual trimmed spelling, leaving the inherited vtable invisible.
    Fixed by positionally MERGING the confirmed redeclaration's value into
    the tracked one (a position the tracked value has no data for adopts the
    new declaration's value there) rather than keeping it frozen.
    """
    root = _tu(
        {
            "kind": "ClassTemplateDecl",
            "name": "A",
            "id": "0x1",
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "T"},
                {"kind": "TemplateTypeParmDecl", "name": "U"},
            ],
        },
        {
            "kind": "ClassTemplateDecl",
            "name": "A",
            "id": "0x2",
            "previousDecl": "0x1",
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "T"},
                {
                    "kind": "TemplateTypeParmDecl",
                    "name": "U",
                    "defaultArg": {
                        "kind": "TemplateArgument",
                        "type": {"qualType": "T"},
                    },
                },
                _record("A"),
                _specialization(
                    "A",
                    {
                        "kind": "CXXMethodDecl",
                        "name": "f",
                        "mangledName": "_ZN1AIddE1fEv",
                        "type": {"qualType": "void ()"},
                        "virtual": True,
                    },
                    type_args=["double", "double"],
                ),
            ],
        },
        _record("D", bases=[_base("A<double>")]),
    )
    from abicheck.dumper_clang_vtable import _index_template_param_defaults

    # "A" now carries the default the SECOND declaration added, letting
    # dependent-default substitution trim "A<double, double>" down to the
    # base's actual spelling "A<double>".
    assert _index_template_param_defaults(root).get("A") == [None, "T"]
    types = _types(root)
    assert types["D"].vtable == ["_ZN1AIddE1fEv"]
    assert types["D"].vptr_offset_bits == 0


def test_added_default_with_renamed_params_translates_through_positions() -> None:
    """Codex review, fresh evidence (eighth round): the sixth round's merge
    logic adopted a newly-added default's raw text as-is, but when the
    redeclaration that adds the default ALSO renames its parameters (
    ``template<class T, class U> struct A;`` followed by ``template<class
    X, class Y=X> struct A {...};``), that raw text ("X") names one of the
    REDECLARATION's own (renamed) parameters, not the tracked/original
    names ("T"/"U") :func:`_specialization_spelling` looks the dependent
    reference up against -- so the substitution silently failed to find
    "X" in the tracked names index, leaving the trailing argument
    untrimmed. Fixed by translating a newly adopted default's dependent
    reference through the CONTRIBUTING declaration's own positional name
    list into the FIRST declaration's name at that same position before
    merging it in.
    """
    root = _tu(
        {
            "kind": "ClassTemplateDecl",
            "name": "A",
            "id": "0x1",
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "T"},
                {"kind": "TemplateTypeParmDecl", "name": "U"},
            ],
        },
        {
            "kind": "ClassTemplateDecl",
            "name": "A",
            "id": "0x2",
            "previousDecl": "0x1",
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "X"},
                {
                    "kind": "TemplateTypeParmDecl",
                    "name": "Y",
                    "defaultArg": {
                        "kind": "TemplateArgument",
                        "type": {"qualType": "X"},
                    },
                },
                _record("A"),
                _specialization(
                    "A",
                    {
                        "kind": "CXXMethodDecl",
                        "name": "f",
                        "mangledName": "_ZN1AIddE1fEv",
                        "type": {"qualType": "void ()"},
                        "virtual": True,
                    },
                    type_args=["double", "double"],
                ),
            ],
        },
        _record("D", bases=[_base("A<double>")]),
    )
    from abicheck.dumper_clang_vtable import _index_template_param_defaults

    # The added default is translated to the FIRST declaration's own
    # parameter name ("T"), matching the tracked names index used by
    # _specialization_spelling's dependent-default substitution.
    assert _index_template_param_defaults(root).get("A") == [None, "T"]
    types = _types(root)
    assert types["D"].vtable == ["_ZN1AIddE1fEv"]
    assert types["D"].vptr_offset_bits == 0


def test_names_filled_in_by_intermediate_redeclaration_translate_correctly() -> None:
    """Codex review, fresh evidence (ninth round): an earlier version of the
    eighth round's translation fix kept its own local "first name seen for
    this qualname" shadow, updated only at a qualname's very first
    sighting -- which silently went stale whenever an INTERMEDIATE
    redeclaration was the one that actually named a previously-unnamed
    parameter: ``template<class, class> struct A;`` (both unnamed) then
    ``template<class X, class Y> struct A;`` (a redeclaration that names
    them) then ``template<class T, class U=T> struct A {...};`` (a further
    redeclaration that adds a default). The real, fully-merged names index
    correctly resolves to ``["X", "Y"]``, but the stale local shadow still
    held ``[None, None]`` from the very first sighting, so the added
    default's translation target was falsy and the raw, untranslated text
    ("T") was kept -- unresolvable against the real names index. Fixed by
    reusing :func:`_index_template_param_names`'s own already-correct,
    fully-merged result as the translation target instead of re-deriving a
    second, independently-tracked copy.
    """
    root = _tu(
        {
            "kind": "ClassTemplateDecl",
            "name": "A",
            "id": "0x1",
            "inner": [
                {"kind": "TemplateTypeParmDecl"},
                {"kind": "TemplateTypeParmDecl"},
            ],
        },
        {
            "kind": "ClassTemplateDecl",
            "name": "A",
            "id": "0x2",
            "previousDecl": "0x1",
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "X"},
                {"kind": "TemplateTypeParmDecl", "name": "Y"},
            ],
        },
        {
            "kind": "ClassTemplateDecl",
            "name": "A",
            "id": "0x3",
            "previousDecl": "0x2",
            "inner": [
                {"kind": "TemplateTypeParmDecl", "name": "T"},
                {
                    "kind": "TemplateTypeParmDecl",
                    "name": "U",
                    "defaultArg": {
                        "kind": "TemplateArgument",
                        "type": {"qualType": "T"},
                    },
                },
                _record("A"),
                _specialization(
                    "A",
                    {
                        "kind": "CXXMethodDecl",
                        "name": "f",
                        "mangledName": "_ZN1AIddE1fEv",
                        "type": {"qualType": "void ()"},
                        "virtual": True,
                    },
                    type_args=["double", "double"],
                ),
            ],
        },
        _record("D", bases=[_base("A<double>")]),
    )
    from abicheck.dumper_clang_vtable import _index_template_param_defaults

    # The added default is translated to the names that actually won the
    # merge ("X"/"Y", filled in by the SECOND declaration), not a stale
    # first-sighting shadow that never saw them.
    assert _index_template_param_defaults(root).get("A") == [None, "X"]
    types = _types(root)
    assert types["D"].vtable == ["_ZN1AIddE1fEv"]
    assert types["D"].vptr_offset_bits == 0
