# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Itanium/MSVC class-artifact classification and its report wording.

Bug class: a C++ constructor/destructor export was worded as a
"vtable/typeinfo/VTT/thunk" artifact, and a symbol whose *identifier* merely
contained ``C1E``/``D0E`` was taken for a structor. The oracle is the
hand-written table below (what the Itanium ABI says each name is), not the
classifier's own helpers.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.export_account_decision import ExportAccount
from abicheck.buildsource.export_accounting import ACCOUNT_CXX_ARTIFACT
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.cxx_artifact_symbols import (
    cxx_class_artifact_kind,
    is_cxx_class_artifact_symbol,
)
from abicheck.workflows.export_existence_wording import existence_description

CTOR = [
    "_ZN3FooC1Ev",
    "_ZN3FooC2Ev",
    "_ZN3FooC3Ev",
    "_ZN3FooC1ERKS_",
    "_ZN2ns3FooC2Eiid",
    "_ZN2ns5inner3BarC1EPKc",
    "_ZN3FooIiEC2Ev",  # class template
    "_ZN2ns3BoxISt6vectorIiSaIiEEEC1Ev",  # template arg with std:: abbrevs
    "_ZNSt6vectorIiSaIiEEC2Ev",  # _ZNSt...
    "_ZNSaIcEC1Ev",  # std::allocator<char>
    "_ZN3FooC1B5cxx11Ev",  # ABI tag after the structor
    "_ZN3FooCI14BaseEi",  # inheriting constructor
    "_ZN3FooCI2N2ns4BaseEEi",
    "_ZN3FooILi5EEC1Ev",  # non-type template literal with digits
    "_ZN3FooIT_EC1Ev",
    "_ZN2C13FooC1Ev",  # namespace literally named C1
    "??0Foo@@QEAA@XZ",  # MSVC
]
DTOR = [
    "_ZN3FooD0Ev",
    "_ZN3FooD1Ev",
    "_ZN3FooD2Ev",
    "_ZN2ns5inner3BarD1Ev",
    "_ZN3FooIiED2Ev",
    "_ZNSt6vectorIiSaIiEED1Ev",
    "_ZNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEED2Ev",
    "_ZN2D03FooD0Ev",
    "??1Foo@@QEAA@XZ",
]
SPECIAL = {
    "_ZTV3Foo": "vtable",
    "_ZTVN2ns3FooE": "vtable",
    "_ZTI3Foo": "typeinfo",
    "_ZTS3Foo": "typeinfo name",
    "_ZTT3Foo": "VTT",
    "_ZTC3Foo0_3Bar": "construction vtable",
    "_ZThn8_N3Foo1fEv": "thunk",
    "_ZTv0_n24_N3Foo1fEv": "thunk",
    "_ZTch0_h0_N3Foo1fEv": "thunk",
}
NOT_ARTIFACT = [
    "_ZN3foo3BC1Ev",  # function named "BC1"
    "_ZN3foo2C1Ev",  # function literally named "C1"
    "_ZN3foo2D0Ev",  # function literally named "D0"
    "_ZN3foo4AC1EEv",  # identifier contains "C1E"
    "_ZN3foo5xD0E1Ev",
    "_ZN3Foo3getEv",
    "_ZNK3Foo4sizeEv",
    "_ZN3FooaSERKS_",  # operator=
    "_ZN3C1D3getEv",
    "_ZN2ns3BoxIiE6C1D0E1Ev",
    "_Z3fooi",
    "_Z2C1v",
    "foo",
    "C1",
    # malformed / truncated manglings never raise and never classify
    "_ZN",
    "_ZN3Fo",
    "_ZN3FooC",
    "_ZN3FooIiEC",
    "_ZN" + "9" * 5000 + "Av",
]


@pytest.mark.parametrize("sym", CTOR)
def test_constructors(sym: str) -> None:
    assert cxx_class_artifact_kind(sym) == "constructor"
    assert is_cxx_class_artifact_symbol(sym)


@pytest.mark.parametrize("sym", DTOR)
def test_destructors(sym: str) -> None:
    assert cxx_class_artifact_kind(sym) == "destructor"


@pytest.mark.parametrize(("sym", "noun"), sorted(SPECIAL.items()))
def test_special_names(sym: str, noun: str) -> None:
    assert cxx_class_artifact_kind(sym) == noun


@pytest.mark.parametrize("sym", NOT_ARTIFACT)
def test_not_artifacts(sym: str) -> None:
    assert cxx_class_artifact_kind(sym) is None
    assert not is_cxx_class_artifact_symbol(sym)


def _describe(sym: str, kind: ChangeKind) -> str:
    change = Change(kind=kind, symbol=sym, description="")
    text = existence_description(change, ExportAccount(ACCOUNT_CXX_ARTIFACT))
    assert text is not None
    return text


@pytest.mark.parametrize(
    "kind", [ChangeKind.FUNC_ADDED_ELF_ONLY, ChangeKind.FUNC_REMOVED_ELF_ONLY]
)
@pytest.mark.parametrize("sym", CTOR + DTOR)
def test_structor_wording_never_says_vtable(sym: str, kind: ChangeKind) -> None:
    text = _describe(sym, kind)
    expected = "constructor" if sym in CTOR else "destructor"
    assert expected in text
    for word in ("vtable", "typeinfo", "VTT", "thunk"):
        assert word not in text.replace(sym, "")


@pytest.mark.parametrize(("sym", "noun"), sorted(SPECIAL.items()))
def test_special_name_wording(sym: str, noun: str) -> None:
    text = _describe(sym, ChangeKind.VAR_REMOVED_ELF_ONLY).replace(sym, "")
    assert f"({noun})" in text
    assert "constructor" not in text and "destructor" not in text
