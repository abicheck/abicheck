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

"""``extract.semantic_normalizer.normalize_header_ast`` (ADR-063 Phase 6,
second and third slices).

Unit-level: exercises the normalizer directly against hand-built
``RecordType``/``EnumType``/typedef/``Function``/``Variable`` inputs,
independent of any real castxml/clang parse (that end-to-end wiring is
covered by ``test_semantic_ir_end_to_end.py``, which needs the real
toolchains).
"""

from __future__ import annotations

from abicheck.extract.semantic_normalizer import normalize_header_ast
from abicheck.model.declarations import Function, Param, Variable
from abicheck.model.entities import EnumType, RecordType
from abicheck.model.identity import (
    Namespace,
    entity_id_for_constant,
    entity_id_for_enum,
    entity_id_for_function,
    entity_id_for_type,
    entity_id_for_typedef,
    entity_id_for_variable,
)
from abicheck.model.occurrence import OccurrenceId


def _record(name: str, qualified_name: str | None, scope, leaf: str) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        qualified_name=qualified_name,
        entity_id=entity_id_for_type(scope, leaf),
    )


def test_normalize_header_ast_projects_types_enums_typedefs() -> None:
    scope = (Namespace("outer"),)
    rt = _record("Point", "outer::Point", scope, "Point")
    et = EnumType(
        name="Color",
        qualified_name="outer::Color",
        entity_id=entity_id_for_enum(scope, "Color"),
    )
    typedef_eid = entity_id_for_typedef(scope, "PointAlias")

    ir = normalize_header_ast(
        types=[rt],
        enums=[et],
        typedefs_qualified={"outer::PointAlias": "outer::Point"},
        typedef_entity_ids={"outer::PointAlias": typedef_eid},
        producer="castxml",
    )

    assert set(ir.occurrences) == {
        OccurrenceId(rt.entity_id),
        OccurrenceId(et.entity_id),
        OccurrenceId(typedef_eid),
    }
    assert ir.occurrences[OccurrenceId(rt.entity_id)].canonical_spelling.value == (
        "outer::Point"
    )
    assert ir.occurrences[OccurrenceId(et.entity_id)].canonical_spelling.value == (
        "outer::Color"
    )
    assert ir.occurrences[OccurrenceId(typedef_eid)].canonical_spelling.value == (
        "outer::Point"
    )
    assert all(e.producer == "castxml" for e in ir.occurrences.values())


def test_normalize_header_ast_falls_back_to_bare_name_at_global_scope() -> None:
    """A global-scope record/enum has ``qualified_name is None`` (see
    ``RecordType.qualified_name``'s own docstring) -- the normalizer falls
    back to the bare ``name``, not a ``None``/empty canonical_spelling."""
    rt = _record("Widget", None, (), "Widget")
    ir = normalize_header_ast(
        types=[rt],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
    )
    (entity,) = ir.occurrences.values()
    assert entity.canonical_spelling.value == "Widget"


def test_normalize_header_ast_skips_entities_without_entity_id() -> None:
    """Older-snapshot-shaped input (no ``entity_id`` populated) contributes
    no occurrence -- this normalizer reads identity, it never re-resolves
    it."""
    rt = RecordType(name="Widget", kind="struct")
    ir = normalize_header_ast(
        types=[rt],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
    )
    assert ir.occurrences == {}


def test_normalize_header_ast_skips_typedef_with_no_sidecar_match() -> None:
    """A qualified name present in ``typedef_entity_ids`` but missing from
    ``typedefs_qualified`` (or vice versa) is tolerated, not raised -- the
    two maps are expected to share a key set, but this function's contract
    with its caller is read-only/best-effort (see its own docstring)."""
    eid = entity_id_for_typedef((), "OnlyInSidecar")
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={"OnlyInSidecar": eid},
        producer="castxml",
    )
    assert ir.occurrences == {}


def test_normalize_header_ast_marks_unresolved_typedef_as_failed_not_present() -> None:
    """Both header-AST backends spell an unresolved underlying type as the
    literal ``"?"`` placeholder -- treating that as a confirmed ``PRESENT``
    spelling would permanently block a hybrid merge's backfill the moment
    the *other* backend actually resolves it (Codex review, PR #1001:
    ``extract/semantic_ir_merge.py`` only ever backfills a non-present base
    fact)."""
    eid = entity_id_for_typedef((), "Unresolved")
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={"Unresolved": "?"},
        typedef_entity_ids={"Unresolved": eid},
        producer="castxml",
    )
    (entity,) = ir.occurrences.values()
    assert not entity.canonical_spelling.is_present
    assert entity.canonical_spelling.value is None


def test_normalize_header_ast_a_resolved_underlying_type_stays_present() -> None:
    """A real underlying type spelled literally ``"?"`` is not a realistic
    case (never a valid C/C++ type name), so this doesn't need to special-
    case it beyond the exact-sentinel match above."""
    eid = entity_id_for_typedef((), "Resolved")
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={"Resolved": "int"},
        typedef_entity_ids={"Resolved": eid},
        producer="castxml",
    )
    (entity,) = ir.occurrences.values()
    assert entity.canonical_spelling.is_present
    assert entity.canonical_spelling.value == "int"


def test_normalize_header_ast_first_occurrence_wins_on_entity_id_collision() -> None:
    """Two declarations sharing one ``EntityId`` within a single backend's
    own output (e.g. a forward declaration alongside its definition) -- the
    normalizer keeps the first rather than an arbitrary/overwritten pick,
    per its own documented limitation (no per-occurrence disambiguator is
    available from either header-AST backend today)."""
    scope = (Namespace("outer"),)
    eid = entity_id_for_type(scope, "Point")
    rt_first = RecordType(name="Point", kind="struct", entity_id=eid)
    rt_second = RecordType(
        name="Point", kind="struct", qualified_name="outer::Point", entity_id=eid
    )

    ir = normalize_header_ast(
        types=[rt_first, rt_second],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
    )
    (entity,) = ir.occurrences.values()
    # rt_first's spelling ("Point", no qualified_name) survives -- rt_second
    # ("outer::Point") is discarded, not merged.
    assert entity.canonical_spelling.value == "Point"


def test_normalize_header_ast_is_producer_agnostic() -> None:
    """Both header-AST backends expose the identical
    ``parse_types()``/``parse_enums()``/``parse_typedefs_qualified()``/
    ``parse_typedef_entity_ids()`` shape -- this function is deliberately
    not specialized to either, only stamping whatever *producer* its caller
    passes onto every produced entity."""
    et = EnumType(
        name="Mode",
        entity_id=entity_id_for_enum((), "Mode"),
    )
    ir = normalize_header_ast(
        types=[],
        enums=[et],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="clang",
    )
    (entity,) = ir.occurrences.values()
    assert entity.producer == "clang"


# --- Third slice: functions and variables -----------------------------------


def _function(
    name: str,
    return_type: str,
    param_types: tuple[str, ...] = (),
    *,
    is_const: bool = False,
    is_volatile: bool = False,
    is_compiler_generated: bool | None = False,
    mangled: str | None = None,
) -> Function:
    return Function(
        name=name,
        mangled=mangled or f"_Z{len(name)}{name}v",
        return_type=return_type,
        params=[Param(name="", type=t) for t in param_types],
        is_const=is_const,
        is_volatile=is_volatile,
        is_compiler_generated=is_compiler_generated,
        entity_id=entity_id_for_function(
            (), name, mangled_name=mangled, param_types=param_types
        ),
    )


def test_normalize_header_ast_canonicalizes_function_signature_spelling() -> None:
    """A function's ``canonical_spelling`` is built from the same
    canonicalizers ``entity_id_for_function`` itself uses -- cross-backend
    spelling differences (elaborated-type-specifiers, pointer/reference
    sigil spacing) collapse to one canonical string, and a top-level
    by-value parameter cv-qualifier is dropped (it is not a real overload
    discriminator)."""
    fn = _function("f", "struct Widget *", ("const int", "char const*"))
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        functions=[fn],
    )
    (entity,) = ir.occurrences.values()
    assert entity.canonical_spelling.value == "Widget *(int, char const *)"
    assert entity.producer == "castxml"


def test_normalize_header_ast_unresolved_function_return_type_is_failed() -> None:
    """castxml's own unresolved-type sentinel (``"?"``) on a function's
    RETURN type is a failure, not a confirmed spelling (Codex review) --
    the identical treatment the typedef branch already gives it."""
    fn = _function("f", "?", ("int",))
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        functions=[fn],
    )
    (entity,) = ir.occurrences.values()
    assert not entity.canonical_spelling.is_present
    assert entity.canonical_spelling.value is None


def test_normalize_header_ast_unresolved_function_param_type_is_failed() -> None:
    """The identical sentinel on a PARAMETER type is also a failure, not
    silently rendered as a literal ``"?"`` inside an otherwise-confirmed
    signature string."""
    fn = _function("f", "void", ("?",))
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        functions=[fn],
    )
    (entity,) = ir.occurrences.values()
    assert not entity.canonical_spelling.is_present


def test_normalize_header_ast_unresolved_pointer_param_type_is_failed() -> None:
    """castxml's own type resolver composes an unresolved POINTEE into the
    enclosing spelling (``"?*"``) rather than only ever returning the bare
    ``"?"`` -- an exact-equality sentinel check misses this composite shape
    entirely (Codex review, second round, fresh evidence: this reproduces
    against a version of ``_has_unresolved_component`` that checked
    ``t == _UNRESOLVED_TYPE_SENTINEL`` instead of substring containment)."""
    fn = _function("f", "void", ("?*",))
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        functions=[fn],
    )
    (entity,) = ir.occurrences.values()
    assert not entity.canonical_spelling.is_present


def test_normalize_header_ast_castxml_opaque_function_type_param_is_unsupported() -> (
    None
):
    """castxml's own resolver has no dedicated rendering for an anonymous
    ``FunctionType`` (unlike ``Struct``/``Class``/``Typedef``/... which all
    have one), so a direct function-pointer parameter resolves to the
    literal opaque tag ``"FunctionType*"`` rather than a real declarator
    spelling -- this is NOT an unresolved type (the resolver ran and
    produced a real, final answer), so it must be ``Fact.unsupported()``,
    a different status than the genuinely-unresolved ``"?"`` sentinel case
    (Codex review, ninth round, fresh evidence: publishing the opaque tag
    as `Fact.present` made a hybrid merge report a spurious conflict
    against clang's real `"void (*)(int)"` spelling for an unchanged
    callback parameter -- the identical shape `idioms._is_callback_type`
    already has to work around elsewhere in this codebase)."""
    fn = _function("f", "void", ("FunctionType*",))
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        functions=[fn],
    )
    (entity,) = ir.occurrences.values()
    assert not entity.canonical_spelling.is_present
    assert entity.canonical_spelling.status.value == "unsupported"


def test_normalize_header_ast_castxml_opaque_function_type_variable_is_unsupported() -> (
    None
):
    """The identical castxml opaque-tag limitation on a direct
    function-pointer VARIABLE's own type, mirroring the parameter case
    above."""
    var = Variable(
        name="g_cb",
        mangled="g_cb",
        type="FunctionType*",
        entity_id=entity_id_for_variable((), "g_cb", mangled_name="g_cb"),
    )
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        variables=[var],
    )
    (entity,) = ir.occurrences.values()
    assert not entity.canonical_spelling.is_present
    assert entity.canonical_spelling.status.value == "unsupported"


def test_normalize_header_ast_ternary_in_decltype_is_not_unresolved() -> None:
    """A real, fully-resolved type spelling can legally contain a literal
    ``"?"`` -- clang emits one verbatim for a dependent ternary expression
    inside ``decltype(...)`` (Codex review, third round, fresh evidence:
    this reproduces against a version of ``_has_unresolved_component`` that
    used a plain substring test instead of depth-tracking). The sentinel's
    own ``"?"`` never sits inside a ``(...)``/``<...>`` grouping; this
    one does, so it must NOT be treated as unresolved."""
    fn = _function("f", "void", ("S<decltype(flag ? A{} : B{})>",))
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="clang",
        functions=[fn],
    )
    (entity,) = ir.occurrences.values()
    assert entity.canonical_spelling.is_present


def test_normalize_header_ast_shift_operator_in_template_argument_is_not_unresolved() -> (
    None
):
    """A right-shift operator inside a parenthesized non-type template
    argument is not two nested template closers -- a flat depth counter
    decrements once per ``>`` character, so ``">>"`` in ``"S<(N >> 1 ? 1 :
    2)>"`` wrongly drops the running depth to zero WHILE STILL inside the
    ``(...)`` grouping, misreading the ternary's own ``"?"`` as the
    sentinel (Codex review, seventh round, fresh evidence: confirmed
    against a real ``clang++ -Xclang -ast-dump=json`` repro for exactly
    this ``qualType`` shape on a dependent function return/variable type).
    A ``">"`` only legitimately closes a template level when the innermost
    still-open bracket is itself a ``"<"``; here it is a ``"("``, so both
    ``">"``s in ``">>"`` are real, resolved shift-operator characters."""
    fn = _function("f", "void", ("S<(N >> 1 ? 1 : 2)>",))
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="clang",
        functions=[fn],
    )
    (entity,) = ir.occurrences.values()
    assert entity.canonical_spelling.is_present


def test_normalize_header_ast_nested_template_closers_still_pop_correctly() -> None:
    """A genuine ``">>"`` closing TWO nested template levels
    (``"vector<vector<int>>"``) still pops both, unlike the shift-operator
    case above -- the innermost still-open bracket is a ``"<"`` both times
    this ``>`` is processed, which is exactly the discriminator that tells
    the two shapes apart."""
    fn = _function("f", "void", ("vector<vector<int>>?",))
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        functions=[fn],
    )
    (entity,) = ir.occurrences.values()
    # The trailing "?" sits OUTSIDE both template closes (both "<"s are
    # correctly popped by the ">>" ahead of it), so it IS the sentinel at
    # real depth zero -- unresolved, not a false negative from an
    # over-eager stack that never popped.
    assert not entity.canonical_spelling.is_present


def test_normalize_header_ast_unresolved_atomic_wrapper_is_failed() -> None:
    """castxml's own ``AtomicType`` branch renders an unresolved wrapped
    type as the literal ``"_Atomic(?)"`` -- a REAL paren pair as part of
    the resolver's own grammar, not an expression context (Codex review,
    fourth round, fresh evidence: this reproduces against a version of
    ``_has_unresolved_component`` that treated every ``(`` as
    depth-increasing, hiding this sentinel at depth 1)."""
    fn = _function("f", "void", ("_Atomic(?)",))
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        functions=[fn],
    )
    (entity,) = ir.occurrences.values()
    assert not entity.canonical_spelling.is_present


def test_normalize_header_ast_resolved_atomic_wrapper_stays_present() -> None:
    """A real, resolved ``_Atomic(...)`` type (valid C11 syntax) must not
    be disturbed by the sentinel-detection special-casing."""
    fn = _function("f", "void", ("_Atomic(int)",))
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        functions=[fn],
    )
    (entity,) = ir.occurrences.values()
    assert entity.canonical_spelling.is_present


def test_normalize_header_ast_function_cv_qualification() -> None:
    """A member function's ``is_const``/``is_volatile`` populate
    ``cv_qualification`` in canonical order, separately from the spelling
    text."""
    fn = _function("m", "void", is_const=True, is_volatile=True)
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        functions=[fn],
    )
    (entity,) = ir.occurrences.values()
    assert entity.cv_qualification.value == ("const", "volatile")


def test_normalize_header_ast_non_cv_function_has_empty_cv_qualification() -> None:
    fn = _function("f", "void")
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        functions=[fn],
    )
    (entity,) = ir.occurrences.values()
    assert entity.cv_qualification.value == ()
    assert entity.cv_qualification.is_present


def test_normalize_header_ast_includes_synthetic_ctor_key_functions() -> None:
    """A castxml constructor with no recoverable real mangled name gets a
    synthetic snapshot key (``model.synthetic_key.SYNTHETIC_CTOR_KEY_PREFIX``)
    -- not a stable cross-backend identity, and one `dumper_hybrid.
    _merge_functions` can later rewrite it to a real clang-matched mangled
    name/entity_id during a hybrid merge. That rewrite is now propagated
    into `semantic_ir` too (`dumper_hybrid._rewrite_semantic_ir_entity_ids`),
    so this per-backend normalizer no longer needs to guess and exclude --
    a single-backend (non-hybrid) dump has no rewrite step at all, and this
    occurrence is exactly as real as any other function's (Codex review,
    third round: an earlier revision of this slice excluded every
    synthetic-keyed function here, unconditionally losing this evidence
    even for a plain castxml-only dump)."""
    fn = _function(
        "Widget",
        "void",
        mangled="__abicheck_ctor__Widget()",
        is_compiler_generated=True,
    )
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        functions=[fn],
    )
    (entity,) = ir.occurrences.values()
    assert entity.canonical_spelling.value == "void()"


def test_normalize_header_ast_includes_synthetic_dtor_key_functions() -> None:
    """The identical treatment applies to a synthetic destructor key
    (``"~Class"`` -- ``model.synthetic_key.is_synthetic_dtor_key``)."""
    fn = _function("~Widget", "void", mangled="~Widget", is_compiler_generated=True)
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        functions=[fn],
    )
    (entity,) = ir.occurrences.values()
    assert entity.canonical_spelling.value == "void()"


def test_normalize_header_ast_includes_compiler_generated_with_real_mangled_name() -> (
    None
):
    """A compiler-generated function that DOES get a real mangled name (e.g.
    a synthesized ``operator=`` -- ``Function.is_compiler_generated``'s own
    docstring) has none of the synthetic-key hazard and must be normalized
    like any other function -- ``AbiSnapshot.functions`` already includes
    it, so excluding it from ``semantic_ir`` too would itself be a
    representation disagreement (Codex review, second round: an earlier
    revision of this slice skipped every ``is_compiler_generated`` function
    regardless of whether its mangled name was synthetic)."""
    fn = _function(
        "operator=",
        "Widget &",
        ("const Widget &",),
        mangled="_ZN6WidgetaSERKS_",
        is_compiler_generated=True,
    )
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        functions=[fn],
    )
    (entity,) = ir.occurrences.values()
    assert entity.canonical_spelling.value == "Widget &(Widget const &)"


def test_normalize_header_ast_skips_function_without_entity_id() -> None:
    fn = Function(name="f", mangled="_Z1fv", return_type="void")
    assert fn.entity_id is None
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        functions=[fn],
    )
    assert ir.occurrences == {}


def test_normalize_header_ast_canonicalizes_variable_type_spelling() -> None:
    """``"struct Widget const*"`` is a MUTABLE pointer to const data -- the
    pointee is const, the pointer/variable itself is not, so
    ``cv_qualification`` must be empty (Codex review, fourth round, fresh
    evidence: this previously asserted ``("const",)``, reproducing the
    exact pointee-vs-value conflation the finding named)."""
    var = Variable(
        name="g_widget",
        mangled="g_widget",
        type="struct Widget const*",
        is_const=True,
        entity_id=entity_id_for_variable((), "g_widget", mangled_name="g_widget"),
    )
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="clang",
        variables=[var],
    )
    (entity,) = ir.occurrences.values()
    assert entity.canonical_spelling.value == "Widget const *"
    assert entity.cv_qualification.value == ()
    assert entity.producer == "clang"


def test_normalize_header_ast_const_pointer_variable_is_top_level_const() -> None:
    """A CONST pointer to mutable data (``"int * const"``) -- the pointer
    itself is const -- IS the declaration's own top-level qualification,
    unlike the mutable-pointer-to-const-data case above."""
    var = Variable(
        name="g_ptr",
        mangled="g_ptr",
        type="int * const",
        entity_id=entity_id_for_variable((), "g_ptr", mangled_name="g_ptr"),
    )
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="clang",
        variables=[var],
    )
    (entity,) = ir.occurrences.values()
    assert entity.cv_qualification.value == ("const",)


def test_normalize_header_ast_by_value_const_variable_is_top_level_const() -> None:
    """A plain by-value const variable (no pointer at all) -- the whole
    string IS the top-level qualification, matching the pre-existing
    by-value behavior."""
    var = Variable(
        name="g_n",
        mangled="g_n",
        type="const int",
        entity_id=entity_id_for_variable((), "g_n", mangled_name="g_n"),
    )
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="clang",
        variables=[var],
    )
    (entity,) = ir.occurrences.values()
    assert entity.cv_qualification.value == ("const",)


def test_normalize_header_ast_const_template_argument_is_not_top_level() -> None:
    """A ``const`` inside a template argument list belongs to that
    argument's own type, not this pointer's qualification (mirrors
    ``model.declarator_qualifiers._extract_top_level_cv``'s identical
    discipline)."""
    var = Variable(
        name="g_vec",
        mangled="g_vec",
        type="vector<const int> *",
        entity_id=entity_id_for_variable((), "g_vec", mangled_name="g_vec"),
    )
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="clang",
        variables=[var],
    )
    (entity,) = ir.occurrences.values()
    assert entity.cv_qualification.value == ()


def test_normalize_header_ast_const_function_pointer_variable_is_top_level_const() -> (
    None
):
    """A const function-pointer variable wraps its own sigil in a real
    declarator-grouping paren (clang's own spelling for ``int (* const
    fp)(int)`` is ``"int (*const)(int)"``) -- that paren must be transparent
    to the top-level-sigil search, not counted as an ordinary opaque
    nesting level the way a parameter list or ``decltype(...)`` is (Codex
    review, fifth round, fresh evidence: an earlier revision found no
    top-level sigil at all here, since the sigil sits INSIDE the
    declarator-grouping paren, and silently reported ``()`` instead of
    ``("const",)``)."""
    var = Variable(
        name="g_fp",
        mangled="g_fp",
        type="int (*const)(int)",
        entity_id=entity_id_for_variable((), "g_fp", mangled_name="g_fp"),
    )
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="clang",
        variables=[var],
    )
    (entity,) = ir.occurrences.values()
    assert entity.cv_qualification.value == ("const",)


def test_normalize_header_ast_restrict_variable_is_deliberately_not_recognized() -> (
    None
):
    """``restrict`` is deliberately NOT recognized for a variable, even
    though clang's own qualType spells it verbatim (``"int *restrict"`` for
    ``int * restrict gp``) and ``CanonicalEntity.cv_qualification``'s
    vocabulary names it alongside ``const``/``volatile`` (Codex review,
    sixth round, fresh evidence -- reverting a fifth-round addition):
    castxml's ``type_name_uncached`` never emits the word at all (a
    deliberate choice on castxml's own side, unlike a function *parameter*'s
    ``Param.is_restrict``, which both backends populate structurally), so a
    plain text scan reports a clang-only, backend-asymmetric answer -- a
    castxml-produced entity would claim a CONFIRMED absence of a qualifier
    its own backend structurally cannot see, which `merge_semantic_ir`'s
    backfill then treats as a genuine two-sided disagreement against
    clang's real ``("restrict",)`` instead of backfilling it. See this
    module's own `_CV_KEYWORD_RE` comment for the full reasoning and what a
    real fix needs (a structural, reliability-tracked ``Variable.
    is_restrict`` fact, not a normalizer-only change)."""
    var = Variable(
        name="g_ptr",
        mangled="g_ptr",
        type="int *restrict",
        entity_id=entity_id_for_variable((), "g_ptr", mangled_name="g_ptr"),
    )
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="clang",
        variables=[var],
    )
    (entity,) = ir.occurrences.values()
    assert entity.cv_qualification.value == ()


def test_normalize_header_ast_member_function_pointer_variable_excludes_pointee_qualifier() -> (
    None
):
    """A mutable pointer to a cv-qualified member function (``void
    (C::*pmf)(int) const``) reports NO top-level qualification of its own --
    the ``const`` after the parameter list qualifies the POINTED-TO member
    function, not the ``pmf`` pointer variable itself (Codex review, sixth
    round, fresh evidence: an earlier revision scanned the whole text after
    the sigil and wrongly attributed the member function's own ``const`` to
    the pointer variable, so a mutable and a genuinely const member-function
    pointer both reported ``("const",)``)."""
    var = Variable(
        name="g_pmf",
        mangled="g_pmf",
        type="void (C::*)(int) const",
        entity_id=entity_id_for_variable((), "g_pmf", mangled_name="g_pmf"),
    )
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="clang",
        variables=[var],
    )
    (entity,) = ir.occurrences.values()
    assert entity.cv_qualification.value == ()


def test_normalize_header_ast_member_function_pointer_variable_keeps_own_qualifier() -> (
    None
):
    """A genuinely CONST member-function-pointer variable (``void (C::*
    const)(int)``) -- the qualifier sits BEFORE the trailing parameter list,
    directly after the declarator's own sigil, so it is correctly attributed
    to the pointer variable itself, unlike the pointed-to function's own
    trailing qualifier in the sibling test above."""
    var = Variable(
        name="g_pmf",
        mangled="g_pmf",
        type="void (C::* const)(int)",
        entity_id=entity_id_for_variable((), "g_pmf", mangled_name="g_pmf"),
    )
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="clang",
        variables=[var],
    )
    (entity,) = ir.occurrences.values()
    assert entity.cv_qualification.value == ("const",)


def test_normalize_header_ast_typedef_hidden_qualifier_is_a_known_limitation() -> None:
    """A top-level qualifier hidden behind a typedef alias is NOT detected
    -- a documented, accepted limitation, not a silent wrong answer (Codex
    review, eighth round, fresh evidence): for ``typedef int * const
    ConstPtr; extern ConstPtr p;``, both backends pass this normalizer the
    ALIAS spelling (``"ConstPtr"``), which carries no sigil/keyword for the
    text scan to find. Pins the current, honest ``()`` so a future fix
    threading real desugared/structural evidence through has a test that
    fails once it lands, rather than this gap silently persisting
    unnoticed. See `_variable_top_level_cv_qualification`'s own docstring
    ("Known, accepted limitation...") for what a real fix needs."""
    var = Variable(
        name="p",
        mangled="p",
        type="ConstPtr",
        entity_id=entity_id_for_variable((), "p", mangled_name="p"),
    )
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="clang",
        variables=[var],
    )
    (entity,) = ir.occurrences.values()
    assert entity.cv_qualification.value == ()


def test_normalize_header_ast_unresolved_variable_type_is_failed() -> None:
    """The identical unresolved-type sentinel on a variable's own type is a
    failure, not a confirmed ``"?"`` spelling (Codex review)."""
    var = Variable(
        name="g_widget",
        mangled="g_widget",
        type="?",
        entity_id=entity_id_for_variable((), "g_widget", mangled_name="g_widget"),
    )
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        variables=[var],
    )
    (entity,) = ir.occurrences.values()
    assert not entity.canonical_spelling.is_present


def test_normalize_header_ast_unresolved_composite_variable_type_is_failed() -> None:
    """The identical composite-shape gap (``"?"`` embedded rather than the
    whole spelling) applies to a variable's own type too (Codex review,
    second round)."""
    var = Variable(
        name="g_widget2",
        mangled="g_widget2",
        type="const ?",
        entity_id=entity_id_for_variable((), "g_widget2", mangled_name="g_widget2"),
    )
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        variables=[var],
    )
    (entity,) = ir.occurrences.values()
    assert not entity.canonical_spelling.is_present


def test_normalize_header_ast_unresolved_composite_typedef_is_failed() -> None:
    """The typedef branch (pre-existing, second slice) had the identical
    exact-equality gap -- fixed in the same pass since both call the shared
    ``_has_unresolved_component`` helper (Codex review, second round)."""
    eid = entity_id_for_typedef((), "Alias")
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={"Alias": "?*"},
        typedef_entity_ids={"Alias": eid},
        producer="castxml",
    )
    (entity,) = ir.occurrences.values()
    assert not entity.canonical_spelling.is_present


def test_normalize_header_ast_non_const_variable_has_empty_cv_qualification() -> None:
    var = Variable(
        name="g_widget",
        mangled="g_widget",
        type="int",
        entity_id=entity_id_for_variable((), "g_widget", mangled_name="g_widget"),
    )
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="clang",
        variables=[var],
    )
    (entity,) = ir.occurrences.values()
    assert entity.cv_qualification.value == ()


def test_normalize_header_ast_functions_and_variables_default_to_empty() -> None:
    """*functions*/*variables* default to ``()`` -- a caller that has not
    migrated to this slice's scope yet needs no change."""
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
    )
    assert ir.occurrences == {}


def test_normalize_header_ast_projects_constant_value_verbatim() -> None:
    """A constant's ``canonical_spelling`` is its raw ``parse_constants()``
    value text, unchanged -- there is no established cross-backend
    canonicalization for a constant's value expression to apply (this
    module's own docstring, "Scope of the fourth slice"), so this mirrors
    ``diff_symbols._diff_constants``'s own long-standing raw-string
    comparison rather than inventing one."""
    eid = entity_id_for_constant((), "kMaxWidgets")
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        constants={"kMaxWidgets": "42"},
        constant_entity_ids={"kMaxWidgets": eid},
    )
    (entity,) = ir.occurrences.values()
    assert set(ir.occurrences) == {OccurrenceId(eid)}
    assert entity.canonical_spelling.value == "42"
    assert entity.producer == "castxml"
    # No captured type, so no cv_qualification/template_arguments fact --
    # both stay at their `Fact.not_collected()` default.
    assert not entity.cv_qualification.is_present
    assert not entity.template_arguments.is_present


def test_normalize_header_ast_constant_with_no_matching_value_is_skipped() -> None:
    """A ``constant_entity_ids`` entry with no matching ``constants`` value
    is tolerated defensively, mirroring the typedef branch's identical
    treatment of a missing sidecar entry."""
    eid = entity_id_for_constant((), "kOrphan")
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        constants={},
        constant_entity_ids={"kOrphan": eid},
    )
    assert ir.occurrences == {}


def test_normalize_header_ast_constants_default_to_empty() -> None:
    """*constants*/*constant_entity_ids* default to ``{}`` -- a caller that
    has not migrated to this slice's scope yet needs no change."""
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
    )
    assert ir.occurrences == {}


def test_normalize_header_ast_clang_expr_fingerprint_constant_is_unsupported() -> None:
    """A clang-produced compound-initializer constant (``dumper_clang_expr.
    _expr_fingerprint``'s own ``"expr:" + sha256(...)[:16]`` encoding) is
    NOT published as a confirmed spelling -- it is a build-stable
    STRUCTURAL fingerprint, not a spelling of the source text, and that
    module's own docstring is explicit that cross-backend constant values
    are not expected to match for this case (Codex review, sixth round,
    fresh evidence: an earlier revision published it as ``Fact.present``,
    which made `merge_semantic_ir` report a spurious conflict against
    castxml's real initializer text for an unchanged compound constant)."""
    eid = entity_id_for_constant((), "kSum")
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="clang",
        constants={"kSum": "expr:0123456789abcdef"},
        constant_entity_ids={"kSum": eid},
    )
    (entity,) = ir.occurrences.values()
    assert not entity.canonical_spelling.is_present
    assert entity.canonical_spelling.status.value == "unsupported"
