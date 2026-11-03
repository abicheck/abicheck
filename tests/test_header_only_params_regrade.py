"""A parameter-list change of a declaration that neither side exports is an
API break, not an ABI break (case122); an exported or unknown-export
declaration keeps ``func_params_changed``."""

from __future__ import annotations

import itertools

import pytest

from abicheck.buildsource.source_abi import SourceEntity
from abicheck.buildsource.template_signature import (
    PARAMS_KEY,
    template_change,
    template_parameter_types,
)
from abicheck.checker_policy import API_BREAK_KINDS, BREAKING_KINDS, RISK_KINDS
from abicheck.compare.export_transition import regrade_unexported_params
from abicheck.model import Function
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.fact import Fact

EXPORT_STATES = {
    "exported": Fact.present(True),
    "absent": Fact.present(False),
    "unknown": Fact.not_collected(),
}


def _fn(state: str) -> Function:
    return Function(
        name="clamp",
        mangled="clamp",
        return_type="T",
        binary_exported_fact=EXPORT_STATES[state],
    )


def _params_change() -> Change:
    return Change(kind=ChangeKind.FUNC_PARAMS_CHANGED, symbol="clamp", description="d")


@pytest.mark.parametrize(
    ("old", "new"), list(itertools.product(EXPORT_STATES, repeat=2))
)
def test_regrade_only_when_both_sides_confirm_no_export(old, new):
    other = Change(kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="clamp", description="r")
    out = regrade_unexported_params([_params_change(), other], _fn(old), _fn(new))
    expected = (
        ChangeKind.HEADER_ONLY_FUNCTION_PARAMS_CHANGED
        if (old, new) == ("absent", "absent")
        else ChangeKind.FUNC_PARAMS_CHANGED
    )
    assert [c.kind for c in out] == [expected, ChangeKind.FUNC_RETURN_CHANGED]


def test_kind_grades():
    assert ChangeKind.HEADER_ONLY_FUNCTION_PARAMS_CHANGED in API_BREAK_KINDS
    assert ChangeKind.HEADER_ONLY_FUNCTION_PARAMS_CHANGED not in BREAKING_KINDS
    assert ChangeKind.TEMPLATE_BODY_CHANGED in RISK_KINDS


def _ftd(tparam: str, *ptypes: str) -> dict:
    return {
        "kind": "FunctionTemplateDecl",
        "inner": [
            {"kind": "TemplateTypeParmDecl", "name": tparam},
            {
                "kind": "FunctionDecl",
                "inner": [
                    {"kind": "ParmVarDecl", "type": {"qualType": t}} for t in ptypes
                ],
            },
        ],
    }


def test_template_parameter_types_normalize_template_param_names():
    assert template_parameter_types(_ftd("T", "T", "int", "int")) == "$0|int|int"
    assert template_parameter_types(_ftd("U", "U", "int", "int")) == "$0|int|int"
    assert template_parameter_types(_ftd("T", "const T &", "Tx")) == "const $0 &|Tx"
    assert template_parameter_types({"kind": "ClassTemplateDecl", "inner": []}) is None


def _tmpl(params: str | None, body: str) -> SourceEntity:
    rel = {PARAMS_KEY: params} if params is not None else {}
    return SourceEntity(
        id="t", kind="template", qualified_name="clamp", body_hash=body, relations=rel
    )


@pytest.mark.parametrize(
    ("old", "new", "kind"),
    [
        (
            ("$0|int|int", "b1"),
            ("$0|long|long", "b2"),
            ChangeKind.HEADER_ONLY_FUNCTION_PARAMS_CHANGED,
        ),
        (("$0|int|int", "b1"), ("$0|int|int", "b2"), ChangeKind.TEMPLATE_BODY_CHANGED),
        ((None, "b1"), ("$0|long|long", "b2"), ChangeKind.TEMPLATE_BODY_CHANGED),
        (("$0|int|int", "b1"), (None, "b2"), ChangeKind.TEMPLATE_BODY_CHANGED),
    ],
)
def test_template_change_grades_only_a_recorded_param_change(old, new, kind):
    assert template_change(_tmpl(*old), _tmpl(*new), None, "loc").kind is kind
