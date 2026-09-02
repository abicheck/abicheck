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


def test_normalize_header_ast_skips_synthetic_ctor_key_functions() -> None:
    """A castxml constructor with no recoverable real mangled name gets a
    synthetic snapshot key (``model.synthetic_key.SYNTHETIC_CTOR_KEY_PREFIX``)
    -- not a stable cross-backend identity, and one `dumper_hybrid.
    _merge_functions` can later rewrite to a real clang-matched mangled
    name/entity_id, a rewrite this per-backend normalizer cannot see. See
    this module's own skip for the full rationale (Codex review, second
    round, real castxml/clang parity failure)."""
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
    assert ir.occurrences == {}


def test_normalize_header_ast_skips_synthetic_dtor_key_functions() -> None:
    """The identical skip applies to a synthetic destructor key (``"~Class"``
    -- ``model.synthetic_key.is_synthetic_dtor_key``)."""
    fn = _function("~Widget", "void", mangled="~Widget", is_compiler_generated=True)
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        functions=[fn],
    )
    assert ir.occurrences == {}


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
    assert entity.cv_qualification.value == ("const",)
    assert entity.producer == "clang"


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
