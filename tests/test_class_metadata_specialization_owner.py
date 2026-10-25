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

"""A removed class-metadata export of a template specialization, or of a class
whose only virtual member is its destructor, is confirmed by its header.

Bug class (known gap 2026-10-22): ``_ZTVN3api3BoxIiEE`` and the vtable of a
dtor-only class in an inline namespace stayed ``UNKNOWN_UNRESOLVED`` under
``--contract public`` and did not gate, although the header declared the class.
The header names such a class in a spelling the mangling does not share
(``api::Box<int>``; ``api::D`` for ``api::v1::D``), and the old proof -- the
class's mangled virtual slots read as a non-template path -- skipped both.

The invariant, over prefixes, nesting depths, inline namespaces and template
arities: a class-metadata removal is in contract exactly when a public record's
own mangled member (a virtual slot, or its implicit ``operator=``) spells the
same mangled class scope. A primary template, a different specialization, a
user-declared ``operator=`` and a dtor-only class with no mangled member prove
nothing, so those removals stay unconfirmed (reported, not gating).

The oracle is the hand-written Itanium spelling built by ``_body`` below, never
the parser under test.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import Verdict
from abicheck.contract_relevance_types import ContractRelevance
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, Param, RecordType, Visibility
from abicheck.model.elf_facts import SymbolBinding, SymbolType
from abicheck.model.fact import Fact
from abicheck.model.vocabulary import ScopeOrigin

PREFIXES = ("_ZTV", "_ZTI", "_ZTS", "_ZTT")
_FUNC = "_ZN3api4makeEv"
#: Itanium builtin-type codes used as template arguments, with their spelling.
_BUILTIN = {"i": "int", "l": "long", "c": "char", "j": "unsigned int"}


@dataclass(frozen=True)
class Cls:
    """A class: mangled namespaces, its header spelling's namespaces (inline
    ones dropped, as castxml/clang write them), its leaf, its template args."""

    mangled_ns: tuple[str, ...]
    header_ns: tuple[str, ...]
    leaf: str
    args: tuple[str, ...] = ()

    @property
    def header_name(self) -> str:
        leaf = self.leaf
        if self.args:
            leaf += "<" + ", ".join(_BUILTIN[a] for a in self.args) + ">"
        return "::".join((*self.header_ns, leaf))

    @property
    def record_name(self) -> str:
        return self.header_name.rsplit("::", 1)[-1]


def _body(cls: Cls) -> str:
    """``<length><name>`` per component, the leaf's ``I...E`` args appended."""
    text = "".join(f"{len(n)}{n}" for n in (*cls.mangled_ns, cls.leaf))
    if cls.args:
        text += "I" + "".join(cls.args) + "E"
    return text


def _metadata(prefix: str, cls: Cls) -> str:
    body = _body(cls)
    return f"{prefix}N{body}E" if cls.mangled_ns else f"{prefix}{body}"


def _slot(cls: Cls) -> str:
    return f"_ZN{_body(cls)}3getEv"


def _copy_assign(cls: Cls) -> str:
    # ``RKS<n>_``: the parameter refers back to the class itself; the exact
    # substitution index is irrelevant to the scope, so a fixed one is used.
    return f"_ZN{_body(cls)}aSERKS1_"


def _snapshot(
    version: str,
    *,
    record: Cls | None,
    exports: tuple[str, ...],
    vtable: tuple[str, ...] = (),
    pattern: bool = False,
    assign: tuple[str, str, bool] | None = None,
) -> AbiSnapshot:
    """*assign* is ``(mangled, param identity, compiler-generated)``."""
    functions = [
        Function(
            name="make",
            mangled=_FUNC,
            return_type="int",
            visibility=Visibility.PUBLIC,
            source_header="api.h",
            origin=ScopeOrigin.PUBLIC_HEADER,
        )
    ]
    types = []
    if record is not None:
        types.append(
            RecordType(
                name=record.record_name,
                kind="class",
                size_bits=64,
                qualified_name=record.header_name,
                source_header="api.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
                vtable=list(vtable),
                is_template_pattern=pattern,
            )
        )
    if assign is not None:
        mangled, identity, generated = assign
        functions.append(
            Function(
                name="operator=",
                mangled=mangled,
                return_type="int",
                params=[
                    Param(
                        name="",
                        type="const X&",
                        type_identities_fact=Fact.present((identity,)),
                    )
                ],
                visibility=Visibility.PUBLIC,
                source_header="api.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
                is_compiler_generated=generated,
            )
        )
    symbols = [
        ElfSymbol(name=_FUNC, binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC)
    ]
    symbols += [
        ElfSymbol(name=n, binding=SymbolBinding.GLOBAL, sym_type=SymbolType.OBJECT)
        for n in exports
    ]
    return AbiSnapshot(
        library="libapi.so",
        version=version,
        functions=functions,
        types=types,
        from_headers=True,
        elf=ElfMetadata(machine="x86-64", symbols=symbols),
    )


def _removal(mangled: str, **kw):
    old = _snapshot("1.0", exports=(mangled,), **kw)
    new = _snapshot("2.0", exports=(), **kw)
    result = compare(old, new, contract_evaluation=True, contract_mode="public")
    matches = [c for c in result.changes if c.symbol == mangled]
    assert len(matches) == 1, [c.symbol for c in result.changes]
    return matches[0], result


def _assert_confirmed(mangled: str, **kw) -> None:
    change, result = _removal(mangled, **kw)
    assert change.contract_relevance is ContractRelevance.IN_CONTRACT
    assert result.verdict is Verdict.BREAKING


def _assert_unconfirmed(mangled: str, **kw) -> None:
    change, result = _removal(mangled, **kw)
    assert change.contract_relevance is not ContractRelevance.IN_CONTRACT
    assert result.verdict is not Verdict.BREAKING


SPECIALIZATIONS = (
    Cls(("api",), ("api",), "Box", ("i",)),
    Cls(("api", "v1"), ("api",), "Box", ("i", "l")),
    Cls(("a", "b", "c"), ("a", "b", "c"), "Pair", ("c", "j")),
    Cls(("svs", "runtime", "v0"), ("svs", "runtime"), "Idx", ("j",)),
    Cls((), (), "Box", ("i",)),
)
_IDS = [
    c.header_name + ("/inline" if c.mangled_ns != c.header_ns else "")
    for c in SPECIALIZATIONS
]

DTOR_ONLY = (
    Cls(("api", "v1"), ("api",), "D"),
    Cls(("svs", "runtime", "v0"), ("svs", "runtime"), "Handle"),
    Cls(("api", "v2"), ("api",), "Box", ("i", "l")),
    Cls(("api",), ("api",), "Plain"),
)


# ---------------------------------------------------------------------------
# 1. A specialization is proven by its own mangled virtual slot.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("cls", SPECIALIZATIONS, ids=_IDS)
def test_specialization_confirmed_by_its_slot(prefix: str, cls: Cls) -> None:
    _assert_confirmed(
        _metadata(prefix, cls), record=cls, vtable=(f"~{cls.leaf}", _slot(cls))
    )


@pytest.mark.parametrize("cls", SPECIALIZATIONS, ids=_IDS)
def test_other_specialization_is_not_confirmed(cls: Cls) -> None:
    """``Box<int>``'s slot proves ``Box<int>``, never ``Box<char,char>``."""
    other = Cls(cls.mangled_ns, cls.header_ns, cls.leaf, ("c", "c"))
    _assert_unconfirmed(
        _metadata("_ZTV", other), record=cls, vtable=(f"~{cls.leaf}", _slot(cls))
    )


@pytest.mark.parametrize("cls", SPECIALIZATIONS, ids=_IDS)
def test_primary_template_pattern_proves_nothing(cls: Cls) -> None:
    _assert_unconfirmed(
        _metadata("_ZTV", cls),
        record=cls,
        vtable=(f"~{cls.leaf}", _slot(cls)),
        pattern=True,
    )


@pytest.mark.parametrize("cls", SPECIALIZATIONS, ids=_IDS)
def test_specialization_without_mangled_member_stays_unconfirmed(cls: Cls) -> None:
    """Only its header spelling is known: unproven, so not gating."""
    if not cls.mangled_ns and not cls.args:
        pytest.skip("non-template global class matches by spelling")
    _assert_unconfirmed(_metadata("_ZTV", cls), record=cls, vtable=(f"~{cls.leaf}",))


# ---------------------------------------------------------------------------
# 2. A dtor-only class is proven by its implicit copy-assignment operator.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("cls", DTOR_ONLY, ids=lambda c: c.header_name)
def test_dtor_only_confirmed_by_implicit_assign(prefix: str, cls: Cls) -> None:
    _assert_confirmed(
        _metadata(prefix, cls),
        record=cls,
        vtable=(f"~{cls.leaf}",),
        assign=(_copy_assign(cls), cls.header_name, True),
    )


@pytest.mark.parametrize("cls", DTOR_ONLY[:3], ids=lambda c: c.header_name)
def test_dtor_only_without_assign_stays_unconfirmed(cls: Cls) -> None:
    _assert_unconfirmed(_metadata("_ZTV", cls), record=cls, vtable=(f"~{cls.leaf}",))


@pytest.mark.parametrize("cls", DTOR_ONLY[:3], ids=lambda c: c.header_name)
def test_user_declared_assign_proves_nothing(cls: Cls) -> None:
    """A user ``operator=`` may take any type; only an implicit one is tied to
    its own class."""
    _assert_unconfirmed(
        _metadata("_ZTV", cls),
        record=cls,
        vtable=(f"~{cls.leaf}",),
        assign=(_copy_assign(cls), cls.header_name, False),
    )


@pytest.mark.parametrize("cls", DTOR_ONLY[:3], ids=lambda c: c.header_name)
def test_assign_of_another_class_proves_nothing(cls: Cls) -> None:
    """An implicit ``operator=`` of a class that is not the public record."""
    other = Cls(cls.mangled_ns, cls.header_ns, "Other")
    _assert_unconfirmed(
        _metadata("_ZTV", other),
        record=cls,
        vtable=(f"~{cls.leaf}",),
        assign=(_copy_assign(other), other.header_name, True),
    )


@pytest.mark.parametrize("cls", DTOR_ONLY[:3], ids=lambda c: c.header_name)
def test_same_leaf_in_another_namespace_is_not_confirmed(cls: Cls) -> None:
    sibling = Cls((*cls.mangled_ns, "other"), cls.header_ns, cls.leaf, cls.args)
    _assert_unconfirmed(
        _metadata("_ZTV", sibling),
        record=cls,
        vtable=(f"~{cls.leaf}",),
        assign=(_copy_assign(cls), cls.header_name, True),
    )


# ---------------------------------------------------------------------------
# 3. Real castxml + g++ pipeline.
# ---------------------------------------------------------------------------

_REAL_HEADER = """namespace api { inline namespace v1 {
struct D { virtual ~D(); int x; };
template <class T, class U> struct Box { virtual ~Box(); virtual int get(); T t; U u; };
extern template struct Box<int, long>;
Box<int, long>* mk();
D* mkd();
}}
"""
_REAL_V1 = """#include "api.h"
namespace api { inline namespace v1 {
D::~D() {}
template <class T, class U> Box<T, U>::~Box() {}
template <class T, class U> int Box<T, U>::get() { return 0; }
template struct Box<int, long>;
Box<int, long>* mk() { return new Box<int, long>; }
D* mkd() { return new D; }
}}
namespace hidden { struct Impl { virtual ~Impl(); }; Impl::~Impl() {} }
"""
_REAL_V2 = """#include "api.h"
namespace api { inline namespace v1 {
Box<int, long>* mk() { return nullptr; }
D* mkd() { return nullptr; }
}}
"""


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="ELF/Itanium fixture")
@pytest.mark.skipif(shutil.which("g++") is None, reason="needs g++")
def test_real_specialization_and_dtor_only_vtable_removal_gates(
    tmp_path: Path,
) -> None:
    from abicheck.dumper import dump

    def build(tag: str, source: str) -> AbiSnapshot:
        d = tmp_path / tag
        d.mkdir()
        (d / "api.h").write_text(_REAL_HEADER)
        (d / "lib.cpp").write_text(source)
        so = d / "libapi.so"
        subprocess.run(
            [
                "g++",
                "-shared",
                "-fPIC",
                "-I",
                str(d),
                str(d / "lib.cpp"),
                "-o",
                str(so),
            ],
            check=True,
            timeout=120,
        )
        return dump(so, [d / "api.h"], version=tag)

    result = compare(
        build("1", _REAL_V1),
        build("2", _REAL_V2),
        contract_evaluation=True,
        contract_mode="public",
    )
    by_symbol = {c.symbol: c for c in result.changes}
    for prefix in ("_ZTV", "_ZTI", "_ZTS"):
        for body in ("N3api2v13BoxIilEE", "N3api2v11DE"):
            change = by_symbol[prefix + body]
            assert change.contract_relevance is ContractRelevance.IN_CONTRACT, (
                prefix + body
            )
        hidden = by_symbol[prefix + "N6hidden4ImplE"]
        assert hidden.contract_relevance is not ContractRelevance.IN_CONTRACT
    assert result.verdict is Verdict.BREAKING
