from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.compare.template_instance_absence import member_owner
from abicheck.diff_templates import (
    _looks_like_template_instantiation,
    _strip_param_signature,
    _strip_template_args,
    detect_missing_instantiations,
)
from abicheck.model import AbiSnapshot, Function

# Itanium symbols for the detector fixtures, written out from the ABI grammar.
# _Z 5scale 3vec I f 3abi I Li8E 2t8 E E f  -> lib::scale(vec<float, abi<8,t8>>, float)
NON_TEMPLATE_WITH_TEMPLATE_PARAM = "_Z5scale3vecIf3abiILi8E2t8EEf"
# _ZN 2ns 3Box I d E 3set E d  -> ns::Box<double>::set(double)
MEMBER_OF_CLASS_TEMPLATE = "_ZN2ns3BoxIdE3setEd"


def _fn(name: str, mangled: str) -> Function:
    return Function(name=name, mangled=mangled, return_type="void", params=[])


def _snap(functions: list[Function]) -> AbiSnapshot:
    return AbiSnapshot(library="lib", version="1.0", functions=functions)


# ---------------------------------------------------------------------------
# The predicate: template args on the function's own name only. The detector
# reads the declared name with its parameter list removed.
# ---------------------------------------------------------------------------


def _is_family(demangled: str) -> bool:
    return _looks_like_template_instantiation(_strip_param_signature(demangled))


@pytest.mark.parametrize(
    # Each demangled spelling is written by hand from its Itanium symbol:
    #   _Z5scale3vecIf3abiILi8E2t8EEf         scale(vec<float, abi<8,t8>>, float)
    #   _ZN2ns5scaleE3vecIf3abiILi8E2t8EEf    ns::scale(...)
    #   _ZN2ns1C5scaleE3vec...                ns::C::scale(...)  (non-template member)
    #   _ZN3BoxC1E3vecIiE                     Box::Box(vec<int>)  (constructor)
    #   _Z7computev                           compute()
    "demangled",
    [
        "scale(vec<float, abi<8, t8> >, float)",
        "ns::scale(vec<float, abi<8, t8> >, float)",
        "ns::C::scale(vec<float, abi<8, t8> >, float)",
        "Box::Box(vec<int>)",
        "compute()",
    ],
)
def test_non_template_function_is_not_a_family(demangled: str) -> None:
    assert _is_family(demangled) is False


@pytest.mark.parametrize(
    "demangled",
    [
        "void f<int>(int)",  # _Z1fIiEvT_
        "ns::Box<double>::set(double)",  # _ZN2ns3BoxIdE3setEd, member of a class template
    ],
)
def test_template_named_function_is_a_family(demangled: str) -> None:
    assert _is_family(demangled) is True


@pytest.mark.parametrize(
    ("demangled", "stem"),
    [
        ("get<int>(int)", "get"),
        ("get<double>(double)", "get"),
        ("ns::descriptor<float>::train", "ns::descriptor::train"),
        ("scale(vec<float, abi<8, t8> >, float)", "scale"),
    ],
)
def test_family_stem_drops_template_args_and_parameter_list(
    demangled: str, stem: str
) -> None:
    assert _strip_template_args(_strip_param_signature(demangled)) == stem


def test_member_owner_ignores_templated_parameter() -> None:
    # The owner of a non-template member is no class template, whatever its
    # parameter types carry.
    assert member_owner("_ZN2ns1C5scaleE3vecIf3abiILi8E2t8EEf") is None
    assert member_owner("_ZN2ns3BoxIdE3setEd") == "ns::Box<double>"


# ---------------------------------------------------------------------------
# The detector: the reported case is no longer an instantiation finding.
# ---------------------------------------------------------------------------


def _replaced_scale_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    old = _snap(
        [
            _fn("scale", NON_TEMPLATE_WITH_TEMPLATE_PARAM),
            _fn("get<int>", "_Z3getIiET_S0_"),
        ]
    )
    new = _snap(
        [
            _fn("get<int>", "_Z3getIiET_S0_"),
            _fn("scale", "_Z5scale3vecIf3abiILi16E3t16EEf"),
        ]
    )
    return old, new


def test_non_template_parameter_change_is_not_missing_instantiation() -> None:
    old, new = _replaced_scale_pair()
    assert detect_missing_instantiations(old, new) == []


def test_real_function_template_instantiation_still_missing() -> None:
    # get<double> was exported and is gone; get<int> survives in the same
    # family, so the header still advertises a missing instantiation.
    old = _snap(
        [
            _fn("get<int>", "_Z3getIiET_S0_"),
            _fn("get<double>", "_Z3getIdET_S0_"),
        ]
    )
    new = _snap([_fn("get<int>", "_Z3getIiET_S0_")])
    (finding,) = detect_missing_instantiations(old, new)
    assert finding.kind == ChangeKind.INSTANTIATION_MISSING_FROM_BINARY
    assert "get<double>" in finding.description


def test_member_of_class_template_still_missing() -> None:
    old = _snap(
        [
            _fn("ns::Box<double>::set", MEMBER_OF_CLASS_TEMPLATE),
            _fn("ns::Box<float>::set", "_ZN2ns3BoxIfE3setEf"),
        ]
    )
    new = _snap([_fn("ns::Box<float>::set", "_ZN2ns3BoxIfE3setEf")])
    (finding,) = detect_missing_instantiations(old, new)
    assert finding.kind == ChangeKind.INSTANTIATION_MISSING_FROM_BINARY
