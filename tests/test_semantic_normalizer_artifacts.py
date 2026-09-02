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

"""``extract.semantic_normalizer``'s artifact-recognition primitives (ADR-063
Phase 6, fourth slice) -- exercised through ``normalize_header_ast`` itself.

Split out of ``test_semantic_normalizer.py`` once that file's accumulated
regression tests for these primitives pushed it past the AI-readiness
gate's 1200-line cap for a new test file, mirroring the production-code
split of ``extract/semantic_normalizer_artifacts.py`` out of
``extract/semantic_normalizer.py`` for the identical reason. Scope: the
unresolved-type sentinel, castxml's opaque ``FunctionType`` tag, clang's
compound-initializer expression fingerprint, and clang's Python-bool-derived
literal spelling -- the producer-specific-artifact recognition rules that
``semantic_normalizer_artifacts.py`` implements and this file pins.
"""

from __future__ import annotations

import pytest

from abicheck.extract.semantic_normalizer import normalize_header_ast
from abicheck.model.declarations import Function, Param, Variable
from abicheck.model.identity import (
    entity_id_for_constant,
    entity_id_for_function,
    entity_id_for_typedef,
    entity_id_for_variable,
)


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


def test_normalize_header_ast_real_type_named_functiontype_wrapper_is_present() -> None:
    """A real, legitimately-named type like ``"MyFunctionTypeWrapper*"``
    (castxml's ``Struct``/``Class`` branch resolves such a name correctly
    and verbatim, no opacity involved at all) must NOT be rejected -- a
    naive ``"FunctionType" in raw_type`` substring test also matches this
    (Codex review, eleventh round, fresh evidence: an earlier revision did
    exactly that). The opaque fallback's own contribution is always
    exactly the bare tag text with nothing else glued onto it, so this
    must require the WHOLE (cv/sigil-stripped) string to match, not a
    substring anywhere."""
    fn = _function("f", "void", ("MyFunctionTypeWrapper*",))
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


def test_normalize_header_ast_functiontype_tag_on_clang_is_present() -> None:
    """Clang never emits the literal ``"FunctionType"`` tag text at all --
    a clang-produced spelling matching this shape can only be a real,
    legitimately-named type, so the check is gated on ``producer ==
    "castxml"`` (Codex review, eleventh round, fresh evidence: an earlier
    revision fired for clang too)."""
    fn = _function("f", "void", ("FunctionType*",))
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


def test_normalize_header_ast_castxml_suffix_qualified_opaque_function_type() -> None:
    """castxml renders a cv-qualified POINTER VALUE (not a cv-qualified
    pointee) as a SUFFIX -- ``f"{base} {qual_str}"`` -- so a const
    function-pointer's opaque fallback resolves to ``"FunctionType*
    const"``, not ``"const FunctionType*"`` (Codex review, thirteenth
    round, fresh evidence: an earlier revision only recognized a LEADING
    cv-keyword, so this suffix-qualified shape was wrongly published as
    present, conflicting with clang's real spelling in a hybrid dump of an
    unchanged const callback)."""
    fn = _function("f", "void", ("FunctionType* const",))
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


def test_normalize_header_ast_castxml_sized_array_opaque_function_type() -> None:
    """castxml's ``ArrayType`` renderer spells a fixed-size array of
    function pointers (``void (*callbacks[3])(int)``) as
    ``"FunctionType*[3]"`` -- a SIZED array suffix, not only the unsized
    ``"[]"`` (Codex review, sixteenth round, fresh evidence): an earlier
    revision matched only ``"[]"``, so this sized-array shape was wrongly
    published as present, conflicting with clang's real, complete
    declarator in a hybrid dump of an unchanged callback array."""
    var = Variable(
        name="callbacks",
        mangled="callbacks",
        type="FunctionType*[3]",
        entity_id=entity_id_for_variable((), "callbacks", mangled_name="callbacks"),
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


def test_normalize_header_ast_castxml_atomic_wrapped_opaque_function_type() -> None:
    """castxml's resolver composes ``_Atomic(void (*)(int)) callback`` into
    ``"_Atomic(FunctionType*)"`` -- an ``_Atomic(...)`` wrapper enclosing
    the whole opaque spelling, not only a pointer/cv/array wrapper (Codex
    review, seventeenth round, fresh evidence): an earlier revision had no
    ``_Atomic(...)`` branch, so this shape fell through as a real, present
    spelling, conflicting with clang's complete ``_Atomic(void
    (*)(int))`` spelling in a hybrid dump of an unchanged declaration."""
    var = Variable(
        name="callback",
        mangled="callback",
        type="_Atomic(FunctionType*)",
        entity_id=entity_id_for_variable((), "callback", mangled_name="callback"),
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


@pytest.mark.parametrize(
    "raw_type",
    [
        "const _Atomic(FunctionType*)",
        "_Atomic(FunctionType*)*",
        "_Atomic(FunctionType*)[3]",
    ],
)
def test_normalize_header_ast_castxml_wrapped_atomic_opaque_function_type(
    raw_type: str,
) -> None:
    """The `_Atomic(...)`-wrapped form can itself be wrapped again, the
    same way the bare tag can (Codex review, eighteenth round, fresh
    evidence): `const _Atomic(void (*)(int)) callback`,
    `_Atomic(void (*)(int)) *callback`, and an array of atomic callbacks
    render as `"const _Atomic(FunctionType*)"`,
    `"_Atomic(FunctionType*)*"`, and `"_Atomic(FunctionType*)[3]"`
    respectively -- a cv-prefix/sigil/array wrapper OUTSIDE the
    `_Atomic(...)` parens, on top of the wrapper already recognized
    inside them. An earlier revision treated `_Atomic(...)` as only ever
    the whole string, so none of these further-wrapped shapes matched,
    and the normalizer published castxml's opaque fallback as present --
    a false conflict against clang's complete declarator in a hybrid
    dump."""
    var = Variable(
        name="callback",
        mangled="callback",
        type=raw_type,
        entity_id=entity_id_for_variable((), "callback", mangled_name="callback"),
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


def test_normalize_header_ast_comparison_operator_in_template_argument_function() -> (
    None
):
    """The identical fix, proactively applied to ``has_unresolved_
    component`` too (Codex review, twelfth round found this shape in the
    sibling variable-cv-qualification scan; this is the same underlying
    primitive bug and would misbehave identically here without the
    matching fix): a real comparison ``<`` inside a parenthesized non-type
    template argument must not push a spurious bracket level that a later
    real ``)`` would then incorrectly pop instead of the paren it actually
    closes, corrupting the running depth for anything after it."""
    fn = _function("f", "void", ("S<(N < 0)>",))
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


def test_normalize_header_ast_expr_prefixed_qualified_name_is_not_a_fingerprint() -> (
    None
):
    """A plain ``"expr:"`` PREFIX test would also match castxml's raw,
    verbatim source-text initializer whenever it happens to spell a
    qualified name whose next component is literally ``expr`` (e.g. an
    expression-template library's ``expr::`` namespace) -- no fingerprint
    involved at all (Codex review, tenth round, fresh evidence: mirrors
    `diff_default_value_reliability._is_expr_fingerprint`'s identical
    prefix-vs-full-shape fix, PR #720). The real fingerprint shape is
    ``"expr:"`` plus exactly 16 lowercase hex digits; this value has a
    ``::`` after the prefix and letters/digits that don't fit that shape,
    so it must be published as a real, present spelling."""
    eid = entity_id_for_constant((), "kValue")
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        constants={"kValue": "expr::NAMESPACE_VALUE"},
        constant_entity_ids={"kValue": eid},
    )
    (entity,) = ir.occurrences.values()
    assert entity.canonical_spelling.value == "expr::NAMESPACE_VALUE"


def test_normalize_header_ast_clang_python_bool_literal_constant_is_unsupported() -> (
    None
):
    """``dumper_clang_expr._initializer_value`` normalizes even a LONE
    boolean literal, not only a compound expression: clang's AST JSON
    ``value`` for a boolean literal deserializes to a Python ``bool``, and
    ``str(True)``/``str(False)`` capitalizes it -- never the lowercase
    ``"true"``/``"false"`` C++ keyword spelling castxml's verbatim ``init``
    text carries (Codex review, fourteenth round, fresh evidence). This
    capitalization is a safe, structural signal (no real C++ source spells
    a bool literal this way), so it is marked ``Fact.unsupported()`` rather
    than published as a false disagreement against castxml's real
    ``"true"``."""
    eid = entity_id_for_constant((), "kFlag")
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="clang",
        constants={"kFlag": "True"},
        constant_entity_ids={"kFlag": eid},
    )
    (entity,) = ir.occurrences.values()
    assert not entity.canonical_spelling.is_present
    assert entity.canonical_spelling.status.value == "unsupported"


def test_normalize_header_ast_castxml_true_named_identifier_stays_present() -> None:
    """The boolean-literal exception is gated on ``producer == "clang"``
    (Codex review, fifteenth round, fresh evidence): ``"True"``/``"False"``
    are legal, if unusual, case-sensitive C++ identifier spellings --
    ``constexpr bool True = true; constexpr bool k = True;`` is real,
    compilable C++ -- so castxml's verbatim ``init`` text for ``k`` (which
    genuinely reads ``"True"``) must NOT be discarded as if it were
    clang's own Python-``str(bool)`` artifact; only clang's own
    stringification is producer-specific here."""
    eid = entity_id_for_constant((), "k")
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="castxml",
        constants={"k": "True"},
        constant_entity_ids={"k": eid},
    )
    (entity,) = ir.occurrences.values()
    assert entity.canonical_spelling.value == "True"


def test_normalize_header_ast_decimal_integer_constant_stays_present() -> None:
    """The known, accepted residual: a plain decimal-digit value (like
    clang's normalized form of a hex/char/float literal) has no safe
    structural signal distinguishing it from a genuine decimal literal
    castxml would ALSO spell identically -- so it is published as present,
    same as before. Closing the underlying gap fully needs a real
    castxml-side literal-grammar parser or a threaded literal-kind fact, a
    model-shape decision for a future slice (see this module's own
    docstring, the constants loop's comment)."""
    eid = entity_id_for_constant((), "kMax")
    ir = normalize_header_ast(
        types=[],
        enums=[],
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="clang",
        constants={"kMax": "65"},
        constant_entity_ids={"kMax": eid},
    )
    (entity,) = ir.occurrences.values()
    assert entity.canonical_spelling.value == "65"
