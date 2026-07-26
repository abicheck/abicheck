# Copyright 2026 Nikolay Petrov
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
"""ADR-050 D4 (G32 Phase C) — abicheck.tu_merge's real compatible-merge
lattice.

Two classes of test:

- Fast, pure-Python tests over fake :class:`~abicheck.tu_fragment.TuFragment`
  entities (module-level determinism/order-independence, the
  TuMergeError-is-not-a-ChangeKind structural guard). No compiler needed.
- Real end-to-end tests loading G32 Phase 0's own committed
  ``odr_safe``/``odr_conflict`` fixture headers (``tests/fixtures/g32/``)
  through the real clang header-AST backend, proving this module actually
  resolves the fixtures the plan names it against -- not just a
  semantically-equivalent fake. Self-skips when clang is unavailable, the
  same convention ``test_dumper_manifest.py``'s own real-backend tests use.
"""

from __future__ import annotations

import itertools
import shutil
from pathlib import Path

import pytest

from abicheck.change_registry import REGISTRY
from abicheck.checker_policy import ChangeKind
from abicheck.errors import TuMergeError
from abicheck.model import (
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
)
from abicheck.tu_fragment import MergedTuFragments, TuFragment
from abicheck.tu_merge import (
    HETEROGENEOUS_ABI_CONTEXT,
    INCONSISTENT_DECLARATION,
    merge_fragments,
)

_G32_DIR = Path(__file__).parent / "fixtures" / "g32"


def _fn(name: str, mangled: str | None = None, **overrides: object) -> Function:
    overrides.setdefault("return_type", "void")
    return Function(name=name, mangled=mangled or name, **overrides)  # type: ignore[arg-type]


def _var(name: str, mangled: str | None = None, **overrides: object) -> Variable:
    return Variable(name=name, mangled=mangled or name, type="int", **overrides)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Structural guard: TuMergeError is not a ChangeKind (ADR-050 D4)
# ---------------------------------------------------------------------------


def test_tu_merge_error_codes_are_not_registered_as_change_kinds():
    change_kind_values = {kind.value for kind in ChangeKind} | {
        kind.name for kind in ChangeKind
    }
    assert INCONSISTENT_DECLARATION not in change_kind_values
    assert HETEROGENEOUS_ABI_CONTEXT not in change_kind_values
    assert INCONSISTENT_DECLARATION not in REGISTRY
    assert HETEROGENEOUS_ABI_CONTEXT not in REGISTRY


def test_tu_merge_error_is_a_snapshot_error():
    # Extraction-time failure, not a comparison finding -- existing
    # `except SnapshotError` handling around dump()/compare() must keep
    # catching it unchanged (ADR-050 D4, matching HeaderToolchainError /
    # IncompatibleSnapshotSchemaError precedent).
    from abicheck.errors import SnapshotError

    assert issubclass(TuMergeError, SnapshotError)


# ---------------------------------------------------------------------------
# Mixed AST producers across TUs (HETEROGENEOUS_ABI_CONTEXT)
# ---------------------------------------------------------------------------


def test_merge_fragments_raises_on_mixed_ast_producers():
    # --ast-frontend auto's per-TU fallback can land one TU on castxml and
    # another on clang within the same manifest even though the manifest's
    # own declared compiler/target is uniform -- dumper.py trusts a single
    # representative ast_producer for the whole merged snapshot (gating
    # DWARF layout backfill), so a genuine mix must be rejected rather than
    # silently mislabeled.
    a = TuFragment(tu_name="a", ast_producer="castxml")
    b = TuFragment(tu_name="b", ast_producer="clang")
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == HETEROGENEOUS_ABI_CONTEXT
    assert excinfo.value.tu_names == ("a", "b")


def test_merge_fragments_allows_uniform_ast_producer():
    a = TuFragment(tu_name="a", ast_producer="clang")
    b = TuFragment(tu_name="b", ast_producer="clang")
    merged = merge_fragments([a, b])
    assert merged.ast_producer == "clang"


# ---------------------------------------------------------------------------
# Determinism: merge is independent of fragment processing order
# ---------------------------------------------------------------------------


def test_merge_fragments_is_order_independent():
    fragments = [
        TuFragment(tu_name="alpha", functions=(_fn("shared", "_Z6sharedv"),)),
        TuFragment(
            tu_name="beta",
            functions=(_fn("shared", "_Z6sharedv"),),
            types=(RecordType(name="Point", kind="struct", is_opaque=True),),
        ),
        TuFragment(
            tu_name="gamma",
            types=(
                RecordType(
                    name="Point",
                    kind="struct",
                    fields=[TypeField(name="x", type="int")],
                ),
            ),
            typedefs={"size_type": "unsigned long"},
        ),
        TuFragment(tu_name="delta", constants={"MAX": "100"}),
    ]
    baseline = merge_fragments(fragments)
    for shuffled in itertools.permutations(fragments):
        assert merge_fragments(list(shuffled)) == baseline


def test_merge_fragments_output_order_is_content_derived_not_input_derived():
    # Swapping which fragment is passed first must not change which entity
    # "wins" a union merge, nor the final tuple order.
    a = TuFragment(tu_name="a", functions=(_fn("shared", "_Z6sharedv"),))
    b = TuFragment(tu_name="b", functions=(_fn("shared", "_Z6sharedv"),))
    assert merge_fragments([a, b]) == merge_fragments([b, a])


# ---------------------------------------------------------------------------
# Same-TU duplicates are not a merge concern (Phase B tolerance, preserved)
# ---------------------------------------------------------------------------


def test_merge_fragments_leaves_intra_fragment_duplicates_untouched():
    # A single TU's own parser output repeating a key (e.g. two destructors
    # both falling back to a synthesized no-mangled-name marker) is not a
    # cross-TU merge concern -- both entries pass through unmerged, even
    # though they'd conflict if compared as a genuine cross-TU pair.
    conflicting_but_same_tu = TuFragment(
        tu_name="a",
        functions=(
            _fn("dtor", "", return_type="void"),
            _fn("dtor2", "", return_type="int"),
        ),
    )
    merged = merge_fragments([conflicting_but_same_tu])
    assert len(merged.functions) == 2


# ---------------------------------------------------------------------------
# Empty / single-fragment base cases, directly against tu_merge (not just
# the dumper_manifest.merge_tu_fragments alias already covered elsewhere)
# ---------------------------------------------------------------------------


def test_merge_fragments_empty_returns_empty_result():
    merged = merge_fragments([])
    assert isinstance(merged, MergedTuFragments)
    assert merged == MergedTuFragments(
        functions=(),
        variables=(),
        types=(),
        enums=(),
        typedefs={},
        constants={},
        ast_producer="castxml",
        ast_toolchain={},
        ast_fallback_reason=None,
        ast_toolchain_supported=None,
        ast_toolchain_unsupported_reasons=(),
    )


def test_merge_fragments_variable_union_prefers_non_none_value():
    a = TuFragment(tu_name="a", variables=(_var("g_x", "g_x", value=None),))
    b = TuFragment(tu_name="b", variables=(_var("g_x", "g_x", value="42"),))
    merged = merge_fragments([a, b])
    assert merged.variables[0].value == "42"


def test_merge_fragments_variable_raises_on_conflicting_value():
    # Two different non-None values for the same variable is a genuine
    # conflict, not a "declaration without an initializer" union case.
    a = TuFragment(tu_name="a", variables=(_var("g_x", "g_x", value="1"),))
    b = TuFragment(tu_name="b", variables=(_var("g_x", "g_x", value="2"),))
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("variable", "g_x")


def test_merge_fragments_function_raises_on_conflicting_default_argument():
    # f(int=1) vs f(int=2): two different non-None defaults for the same
    # parameter is a genuine conflict, not "an added default argument".
    param_one = Param(name="n", type="int", default="1")
    param_two = Param(name="n", type="int", default="2")
    a = TuFragment(tu_name="a", functions=(_fn("f", "_Z1fi", params=[param_one]),))
    b = TuFragment(tu_name="b", functions=(_fn("f", "_Z1fi", params=[param_two]),))
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("function", "_Z1fi")


def test_merge_fragments_function_unions_default_argument_across_tus():
    param_no_default = Param(name="n", type="int")
    param_with_default = Param(name="n", type="int", default="0")
    a = TuFragment(
        tu_name="a", functions=(_fn("f", "_Z1fi", params=[param_no_default]),)
    )
    b = TuFragment(
        tu_name="b", functions=(_fn("f", "_Z1fi", params=[param_with_default]),)
    )
    merged = merge_fragments([a, b])
    assert merged.functions[0].params[0].default == "0"


def test_merge_fragments_function_reconciles_differing_parameter_names():
    # void f(int value); vs void f(int n); -- parameter names are not part
    # of a C/C++ function's type, so this is a routine compatible
    # redeclaration, not a conflict.
    a = TuFragment(
        tu_name="a",
        functions=(_fn("f", "_Z1fi", params=[Param(name="value", type="int")]),),
    )
    b = TuFragment(
        tu_name="b",
        functions=(_fn("f", "_Z1fi", params=[Param(name="n", type="int")]),),
    )
    merged = merge_fragments([a, b])
    assert len(merged.functions) == 1


def test_merge_fragments_prefers_public_header_provenance():
    # TU "a" (sorted first) reaches the declaration only through a private,
    # differently-named header; TU "b" reaches the identical declaration
    # through the declared public one. Deliberately different basenames
    # ("detail.h" vs "api.h") so this doesn't accidentally pass via
    # provenance.classify_origin's own basename-fallback matching (D3) --
    # it must be the merge's public-origin preference doing the work, not a
    # same-basename coincidence. The merged entity must carry the public
    # location, not arbitrarily whichever TU happened to sort first --
    # otherwise a genuinely public declaration would misclassify as private
    # once abicheck.provenance.apply_provenance runs on the merged snapshot.
    a = TuFragment(
        tu_name="a",
        functions=(_fn("f", "_Z1fv", source_location="internal/detail.h:1"),),
    )
    b = TuFragment(
        tu_name="b",
        functions=(_fn("f", "_Z1fv", source_location="include/api.h:1"),),
    )
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert merged.functions[0].source_location == "include/api.h:1"


def test_merge_fragments_function_keeps_parameter_names_from_the_winning_side():
    # A private `void f(int internal_name)` in TU "a" and the identical
    # public `void f(int public_name)` in TU "b" must merge to a function
    # attributed to the public header *and* spelled with the public
    # declaration's own parameter name -- not "internal_name" merely
    # because "a" sorted first and fed the parameter list unconditionally.
    a = TuFragment(
        tu_name="a",
        functions=(
            _fn(
                "f",
                "_Z1fi",
                params=[Param(name="internal_name", type="int")],
                source_location="internal/detail.h:1",
            ),
        ),
    )
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn(
                "f",
                "_Z1fi",
                params=[Param(name="public_name", type="int")],
                source_location="include/api.h:1",
            ),
        ),
    )
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.functions) == 1
    winner = merged.functions[0]
    assert winner.source_location == "include/api.h:1"
    assert winner.params[0].name == "public_name"


def test_merge_fragments_function_unions_default_from_the_losing_side_onto_winning_names():
    # The public side ("b") wins both provenance and parameter names, but a
    # default argument only the private side ("a") declared must still be
    # unioned in -- the "keep the winning side's identity" fix must not
    # regress the separate "union whichever side has a default" behavior.
    a = TuFragment(
        tu_name="a",
        functions=(
            _fn(
                "f",
                "_Z1fi",
                params=[Param(name="internal_name", type="int", default="0")],
                source_location="internal/detail.h:1",
            ),
        ),
    )
    b = TuFragment(
        tu_name="b",
        functions=(
            _fn(
                "f",
                "_Z1fi",
                params=[Param(name="public_name", type="int")],
                source_location="include/api.h:1",
            ),
        ),
    )
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.functions) == 1
    winner = merged.functions[0]
    assert winner.params[0].default == "0"
    assert winner.source_location == "include/api.h:1"
    assert winner.params[0].name == "public_name"


def test_merge_fragments_without_public_set_keeps_deterministic_default():
    # No public_header_paths/public_header_dirs supplied -- origin
    # classification stays opt-in (matches abicheck.provenance's own
    # default), so the tu_name-sorted-first side's location wins, unchanged
    # from before this fix.
    a = TuFragment(tu_name="a", functions=(_fn("f", "_Z1fv", source_location="a.h:1"),))
    b = TuFragment(tu_name="b", functions=(_fn("f", "_Z1fv", source_location="b.h:1"),))
    merged = merge_fragments([a, b])
    assert merged.functions[0].source_location == "a.h:1"


def test_merge_fragments_types_with_same_bare_name_different_namespace_do_not_collide():
    # RecordType.name is deliberately bare; the namespace lives in
    # qualified_name. Two genuinely unrelated types sharing a leaf name
    # ("one::X" and "two::X") must not spuriously conflict just because
    # both key on the bare name "X".
    one_x = RecordType(
        name="X",
        kind="struct",
        qualified_name="one::X",
        fields=[TypeField(name="a", type="int")],
    )
    two_x = RecordType(
        name="X",
        kind="struct",
        qualified_name="two::X",
        fields=[TypeField(name="b", type="double")],
    )
    a = TuFragment(tu_name="a", types=(one_x,))
    b = TuFragment(tu_name="b", types=(two_x,))
    merged = merge_fragments([a, b])
    assert {t.qualified_name for t in merged.types} == {"one::X", "two::X"}


def test_merge_fragments_enums_with_same_bare_name_different_namespace_do_not_collide():

    one_e = EnumType(
        name="E", qualified_name="one::E", members=[EnumMember(name="A", value=0)]
    )
    two_e = EnumType(
        name="E", qualified_name="two::E", members=[EnumMember(name="B", value=0)]
    )
    a = TuFragment(tu_name="a", enums=(one_e,))
    b = TuFragment(tu_name="b", enums=(two_e,))
    merged = merge_fragments([a, b])
    assert {e.qualified_name for e in merged.enums} == {"one::E", "two::E"}


def test_merge_fragments_type_forward_decl_provenance_wins_over_private_definition():
    # A public header forward-declares Point; the full definition lives
    # only in a private implementation header. The merged type must keep
    # the definition's fields but the *public* forward declaration's
    # source_location -- otherwise apply_provenance would read a genuinely
    # public type as private.
    public_forward = RecordType(
        name="Point", kind="struct", is_opaque=True, source_location="include/api.h:1"
    )
    private_definition = RecordType(
        name="Point",
        kind="struct",
        fields=[TypeField(name="x", type="int")],
        source_location="internal/detail.h:9",
    )
    a = TuFragment(tu_name="a", types=(public_forward,))
    b = TuFragment(tu_name="b", types=(private_definition,))
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.types) == 1
    winner = merged.types[0]
    assert winner.fields == private_definition.fields
    assert winner.source_location == "include/api.h:1"


def test_merge_fragments_enum_forward_decl_provenance_wins_over_private_definition():

    public_forward = EnumType(
        name="Color", members=[], source_location="include/api.h:1"
    )
    private_definition = EnumType(
        name="Color",
        members=[EnumMember(name="RED", value=0)],
        source_location="internal/detail.h:9",
    )
    a = TuFragment(tu_name="a", enums=(public_forward,))
    b = TuFragment(tu_name="b", enums=(private_definition,))
    merged = merge_fragments(
        [a, b], public_header_paths=["include/api.h"], public_header_dirs=[]
    )
    assert len(merged.enums) == 1
    winner = merged.enums[0]
    assert winner.members == private_definition.members
    assert winner.source_location == "include/api.h:1"


def test_merge_fragments_type_raises_on_conflicting_kind_for_opaque_pair():
    # A `union X;` forward declaration is not compatible with a
    # `struct X { ... };` definition, even though both key on the bare
    # name "X" -- the opaque/definition merge must check `kind` too.
    forward_union = RecordType(name="X", kind="union", is_opaque=True)
    struct_definition = RecordType(
        name="X", kind="struct", fields=[TypeField(name="v", type="int")]
    )
    a = TuFragment(tu_name="a", types=(forward_union,))
    b = TuFragment(tu_name="b", types=(struct_definition,))
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("type", "X")


def test_merge_fragments_type_reconciles_class_and_struct_forward_definition_pair():
    # `class X;` forward-declared, then `struct X { ... };` defined -- valid,
    # ordinary C++ (both GCC and Clang accept it): struct/class are the same
    # underlying entity, differing only in default member access, unlike
    # `union` which is a genuinely different type category.
    forward_class = RecordType(name="X", kind="class", is_opaque=True)
    struct_definition = RecordType(
        name="X", kind="struct", fields=[TypeField(name="v", type="int")]
    )
    a = TuFragment(tu_name="a", types=(forward_class,))
    b = TuFragment(tu_name="b", types=(struct_definition,))
    merged = merge_fragments([a, b])
    assert merged.types == (struct_definition,)


def test_merge_fragments_enum_raises_on_conflicting_underlying_type_for_forward_pair():
    # `enum E : int;` is not compatible with `enum E : unsigned { X };`,
    # even though the forward declaration has no members to compare against
    # the definition's -- the empty-members/definition merge must check
    # underlying_type/is_scoped too.

    forward = EnumType(name="E", members=[], underlying_type="int")
    definition = EnumType(
        name="E", members=[EnumMember(name="X", value=0)], underlying_type="unsigned"
    )
    a = TuFragment(tu_name="a", enums=(forward,))
    b = TuFragment(tu_name="b", enums=(definition,))
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("enum", "E")


def test_merge_fragments_variable_raises_on_conflicting_type():
    a = TuFragment(
        tu_name="a", variables=(Variable(name="g_x", mangled="g_x", type="int"),)
    )
    b = TuFragment(
        tu_name="b", variables=(Variable(name="g_x", mangled="g_x", type="double"),)
    )
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("variable", "g_x")


def test_merge_fragments_enum_reconciles_forward_declaration_and_definition():
    forward = EnumType(name="Color", members=[])

    definition = EnumType(name="Color", members=[EnumMember(name="RED", value=0)])
    a = TuFragment(tu_name="a", enums=(forward,))
    b = TuFragment(tu_name="b", enums=(definition,))
    merged = merge_fragments([a, b])
    assert merged.enums == (definition,)


def test_merge_fragments_enum_raises_on_conflicting_members():

    a = TuFragment(
        tu_name="a",
        enums=(EnumType(name="Color", members=[EnumMember(name="RED", value=0)]),),
    )
    b = TuFragment(
        tu_name="b",
        enums=(EnumType(name="Color", members=[EnumMember(name="RED", value=1)]),),
    )
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("enum", "Color")


def test_merge_fragments_function_raises_on_differing_param_count():
    a = TuFragment(
        tu_name="a",
        functions=(_fn("f", "_Z1fi", params=[Param(name="x", type="int")]),),
    )
    b = TuFragment(tu_name="b", functions=(_fn("f", "_Z1fi", params=[]),))
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("function", "_Z1fi")


def test_merge_fragments_type_reconciles_definition_then_opaque_across_tus():
    # Same shape as the forward-decl/definition test above, but with the
    # roles swapped (a = definition, b = opaque) -- both directions of the
    # is_opaque branch must merge, not just a-opaque-first.
    definition = RecordType(
        name="Point", kind="struct", fields=[TypeField(name="x", type="int")]
    )
    forward = RecordType(name="Point", kind="struct", is_opaque=True)
    a = TuFragment(tu_name="a", types=(definition,))
    b = TuFragment(tu_name="b", types=(forward,))
    merged = merge_fragments([a, b])
    assert merged.types == (definition,)


def test_merge_fragments_type_raises_on_conflicting_kind_for_reversed_opaque_pair():
    struct_definition = RecordType(
        name="X", kind="struct", fields=[TypeField(name="v", type="int")]
    )
    forward_union = RecordType(name="X", kind="union", is_opaque=True)
    a = TuFragment(tu_name="a", types=(struct_definition,))
    b = TuFragment(tu_name="b", types=(forward_union,))
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("type", "X")


def test_merge_fragments_enum_reconciles_definition_then_forward_across_tus():
    # Roles swapped from the enum forward-decl/definition test above (a =
    # definition, b = forward/empty) -- both directions must merge.

    definition = EnumType(name="Color", members=[EnumMember(name="RED", value=0)])
    forward = EnumType(name="Color", members=[])
    a = TuFragment(tu_name="a", enums=(definition,))
    b = TuFragment(tu_name="b", enums=(forward,))
    merged = merge_fragments([a, b])
    assert merged.enums == (definition,)


def test_merge_fragments_enum_raises_on_conflicting_underlying_type_for_reversed_forward_pair():

    definition = EnumType(
        name="E", members=[EnumMember(name="X", value=0)], underlying_type="unsigned"
    )
    forward = EnumType(name="E", members=[], underlying_type="int")
    a = TuFragment(tu_name="a", enums=(definition,))
    b = TuFragment(tu_name="b", enums=(forward,))
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("enum", "E")


def test_merge_fragments_enum_reconciles_identical_definitions_across_tus():
    # Both sides fully defined (non-empty members), identical modulo
    # provenance -- exercises the "_more_public_of" tie-break path for
    # enums, distinct from the forward-decl/definition path above.

    a_def = EnumType(
        name="Color",
        members=[EnumMember(name="RED", value=0)],
        source_location="a.h:1",
    )
    b_def = EnumType(
        name="Color",
        members=[EnumMember(name="RED", value=0)],
        source_location="b.h:1",
    )
    a = TuFragment(tu_name="a", enums=(a_def,))
    b = TuFragment(tu_name="b", enums=(b_def,))
    merged = merge_fragments([a, b])
    assert len(merged.enums) == 1
    assert merged.enums[0].members == [EnumMember(name="RED", value=0)]


def test_merge_fragments_raises_on_conflicting_constant_value():
    a = TuFragment(tu_name="a", constants={"MAX": "100"})
    b = TuFragment(tu_name="b", constants={"MAX": "200"})
    with pytest.raises(TuMergeError) as excinfo:
        merge_fragments([a, b])
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key == ("constant", "MAX")


# ---------------------------------------------------------------------------
# Real end-to-end: G32 Phase 0's own committed odr_safe/odr_conflict fixtures
# ---------------------------------------------------------------------------


def test_odr_safe_fixture_merges_cleanly_through_real_clang_backend():
    if shutil.which("clang") is None:
        pytest.skip("clang is required for the real-backend tu_merge test")
    from abicheck.dump_manifest import TranslationUnit
    from abicheck.dumper import _header_ast_parser
    from abicheck.dumper_manifest import run_tu_loop

    tu_a = TranslationUnit(
        name="tu_a", forced_includes=(_G32_DIR / "odr_safe" / "tu_a.h",)
    )
    tu_b = TranslationUnit(
        name="tu_b", forced_includes=(_G32_DIR / "odr_safe" / "tu_b.h",)
    )
    merged = run_tu_loop(
        (tu_a, tu_b),
        header_ast_parser=_header_ast_parser,
        roots=[_G32_DIR / "odr_safe" / "tu_a.h", _G32_DIR / "odr_safe" / "tu_b.h"],
        backend="clang",
        compiler="c++",
        lang="c++",
        exported_dynamic={"touches_point"},
        exported_static=set(),
    )
    assert [t.name for t in merged.types] == ["Point"]
    # tu_b.h's full definition is what won the merge, not tu_a.h's forward
    # declaration -- the type must carry real fields, not an opaque marker.
    assert merged.types[0].is_opaque is False
    assert {f.name for f in merged.types[0].fields} == {"x", "y"}
    assert [fn.name for fn in merged.functions] == ["touches_point"]


def test_odr_conflict_fixture_raises_through_real_clang_backend():
    if shutil.which("clang") is None:
        pytest.skip("clang is required for the real-backend tu_merge test")
    from abicheck.dump_manifest import TranslationUnit
    from abicheck.dumper import _header_ast_parser
    from abicheck.dumper_manifest import run_tu_loop

    tu_a = TranslationUnit(
        name="tu_a", forced_includes=(_G32_DIR / "odr_conflict" / "tu_a.h",)
    )
    tu_b = TranslationUnit(
        name="tu_b", forced_includes=(_G32_DIR / "odr_conflict" / "tu_b.h",)
    )
    # No explicit lang="c++" here (unlike the odr_safe test above, which
    # genuinely needs it for the bare `Point` forward-declaration syntax) --
    # these headers are valid under either C or C++ linkage, and forcing
    # lang="c++" was observed to route Windows' clang toward MSVC-mode name
    # mangling, where int/double-returning `compute(int)` overloads did not
    # collide under the same entity_key the way they reliably do under
    # Itanium mangling (CI: windows-latest, PR #635) -- matching
    # test_dumper_manifest.py's own real-backend conflict test, which
    # likewise leaves lang unset and passes on every platform.
    with pytest.raises(TuMergeError) as excinfo:
        run_tu_loop(
            (tu_a, tu_b),
            header_ast_parser=_header_ast_parser,
            roots=[
                _G32_DIR / "odr_conflict" / "tu_a.h",
                _G32_DIR / "odr_conflict" / "tu_b.h",
            ],
            backend="clang",
            compiler="c++",
            exported_dynamic={"compute"},
            exported_static=set(),
        )
    assert excinfo.value.code == INCONSISTENT_DECLARATION
    assert excinfo.value.entity_key[0] == "function"
