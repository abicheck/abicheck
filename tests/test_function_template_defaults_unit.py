"""ADR-085 function-template defaults: fast unit coverage of the detector's and
the index's branches. The integration cases in
``test_function_template_param_defaults.py`` drive the same code through clang;
these build the snapshots and clang-AST nodes directly, so every branch runs
without a compiler.
"""

from __future__ import annotations

from abicheck.compare.template_param_defaults import (
    detect_function_template_param_default_changed,
)
from abicheck.extract.headers.clang.function_template_defaults import (
    FUNCTION_NODE_KINDS,
    function_template_default_index,
    function_template_defaults_fact,
)
from abicheck.model import AbiSnapshot, Fact, Function
from abicheck.model.availability import FactStatus
from abicheck.model.identity import EntityId


def _entity(leaf: str = "f", extra: tuple[str, ...] = ("type",)) -> EntityId:
    return EntityId(scope=(), kind="function", leaf_name=leaf, extra=extra)


def _pattern(
    defaults: tuple[str | None, ...] | None,
    *,
    entity: EntityId | None = None,
    status: FactStatus = FactStatus.PRESENT,
) -> Function:
    if defaults is None:
        fact = Fact.not_collected()
    elif status is FactStatus.PRESENT:
        fact = Fact.present(defaults)
    else:
        fact = Fact(status=status, value=defaults)
    return Function(
        name="f",
        mangled="",
        return_type="void",
        template_param_defaults_fact=fact,
        entity_id=_entity() if entity is None else entity,
    )


def _snap(*functions: Function) -> AbiSnapshot:
    snap = AbiSnapshot(library="libx.so", version="1")
    snap.declarations.functions.extend(functions)
    return snap


def test_changed_default_is_reported_with_both_spellings() -> None:
    findings, suppressed = detect_function_template_param_default_changed(
        _snap(_pattern(("int",))), _snap(_pattern(("char",)))
    )
    assert suppressed == []
    (change,) = findings
    assert change.symbol == "f()"
    assert change.old_value == "('int',)"
    assert change.new_value == "('char',)"


def test_unchanged_default_reports_nothing() -> None:
    findings, suppressed = detect_function_template_param_default_changed(
        _snap(_pattern(("int",))), _snap(_pattern(("int",)))
    )
    assert findings == []
    assert suppressed == []


def test_parameter_count_change_is_not_a_default_change() -> None:
    findings, _ = detect_function_template_param_default_changed(
        _snap(_pattern(("int",))), _snap(_pattern(("int", None)))
    )
    assert findings == []


def test_shape_a_covered_change_is_suppressed_not_dropped() -> None:
    # An exported instantiation of the same template already reports the change
    # as DEFAULT_TEMPLATE_ARG_CHANGED. The header-level change is kept in the
    # second list, so the caller can record it as deduplicated.
    findings, suppressed = detect_function_template_param_default_changed(
        _snap(_pattern(("int",))),
        _snap(_pattern(("char",))),
        instantiation_stems=("f",),
    )
    assert findings == []
    (change,) = suppressed
    assert change.new_value == "('char',)"


def test_one_side_unrecorded_is_declined_not_compared() -> None:
    # An UNKNOWN side never pairs with a recorded side.
    findings, suppressed = detect_function_template_param_default_changed(
        _snap(_pattern(("int",), status=FactStatus.PARTIAL)),
        _snap(_pattern(("char",))),
    )
    assert findings == []
    assert suppressed == []


def test_ordinary_function_with_not_collected_fact_is_not_a_pattern() -> None:
    # After a v64 reload an ordinary function carries NOT_COLLECTED, not None.
    # It must not be grouped as a template pattern.
    findings, _ = detect_function_template_param_default_changed(
        _snap(_pattern(None)), _snap(_pattern(("char",)))
    )
    assert findings == []


def test_redeclaration_defaults_merge_per_parameter() -> None:
    # The declaration states the default and the definition does not: the
    # merged row is the default from whichever declaration spells it.
    old = _snap(_pattern(("int", None)), _pattern((None, None)))
    new = _snap(_pattern(("char", None)), _pattern((None, None)))
    (change,) = detect_function_template_param_default_changed(old, new)[0]
    assert change.old_value == "('int', None)"
    assert change.new_value == "('char', None)"


def test_conflicting_redeclaration_defaults_are_not_trusted() -> None:
    # Two declarations state different defaults for one parameter. That is
    # ill-formed C++, so the identity is dropped rather than guessed at.
    old = _snap(_pattern(("int",)), _pattern(("long",)))
    findings, suppressed = detect_function_template_param_default_changed(
        old, _snap(_pattern(("char",)))
    )
    assert findings == []
    assert suppressed == []


def test_redeclarations_with_different_arity_are_not_trusted() -> None:
    old = _snap(_pattern(("int",)), _pattern(("int", None)))
    findings, _ = detect_function_template_param_default_changed(
        old, _snap(_pattern(("char",)))
    )
    assert findings == []


def test_redeclaration_with_a_non_present_fact_is_not_trusted() -> None:
    old = _snap(_pattern(("int",)), _pattern(("int",), status=FactStatus.PARTIAL))
    findings, _ = detect_function_template_param_default_changed(
        old, _snap(_pattern(("char",)))
    )
    assert findings == []


def test_distinct_identities_with_one_name_pair_separately() -> None:
    # Two overloads named f with different template-parameter kinds are two
    # identities. A change to one never reports the other.
    type_overload = _entity(extra=("type",))
    value_overload = _entity(extra=("nontype:int",))
    old = _snap(
        _pattern(("int",), entity=type_overload),
        _pattern((None,), entity=value_overload),
    )
    new = _snap(
        _pattern(("char",), entity=type_overload),
        _pattern((None,), entity=value_overload),
    )
    (change,) = detect_function_template_param_default_changed(old, new)[0]
    assert change.old_value == "('int',)"


def test_index_maps_the_pattern_declaration_to_its_template_defaults() -> None:
    pattern = {"kind": "FunctionDecl", "name": "f"}
    root = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "FunctionTemplateDecl",
                "name": "f",
                "inner": [
                    {
                        "kind": "TemplateTypeParmDecl",
                        "name": "T",
                        "defaultArg": {"type": {"qualType": "int"}},
                    },
                    pattern,
                ],
            }
        ],
    }
    index = function_template_default_index(root)
    assert index == {id(pattern): ("int",)}


def test_index_leaves_a_template_with_an_unspelled_default_unrecorded() -> None:
    # A non-type default with no spelled argument is not a value the walk can
    # read, so the whole pattern is left UNKNOWN instead of recording a guess.
    pattern = {"kind": "FunctionDecl", "name": "f"}
    root = {
        "kind": "FunctionTemplateDecl",
        "inner": [
            {"kind": "NonTypeTemplateParmDecl", "name": "N", "defaultArg": {}},
            pattern,
        ],
    }
    assert function_template_default_index(root) == {}


def test_index_ignores_non_function_children_of_a_template() -> None:
    other = {"kind": "ClassTemplateDecl", "name": "C"}
    root = {
        "kind": "FunctionTemplateDecl",
        "inner": [
            {"kind": "TemplateTypeParmDecl", "name": "T"},
            "stray",
            other,
        ],
    }
    assert function_template_default_index(root) == {}
    assert "ClassTemplateDecl" not in FUNCTION_NODE_KINDS


def test_fact_is_none_without_an_index_or_for_an_unindexed_node() -> None:
    node = {"kind": "FunctionDecl", "name": "g"}
    assert function_template_defaults_fact(node, None) is None
    assert function_template_defaults_fact(node, {}) is None
    assert function_template_defaults_fact(node, {id(node) + 1: ("int",)}) is None


def test_fact_is_present_for_an_indexed_pattern() -> None:
    node = {"kind": "FunctionDecl", "name": "g"}
    fact = function_template_defaults_fact(node, {id(node): ("int", None)})
    assert fact is not None
    assert fact.status is FactStatus.PRESENT
    assert fact.value == ("int", None)
