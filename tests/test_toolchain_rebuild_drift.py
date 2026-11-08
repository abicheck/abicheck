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

"""A rebuild of unchanged sources must not manufacture a break or a risk from
what the toolchain owns (``policy/toolchain_rebuild_drift.py``).

Bug class: a toolchain-owned observation of a rebuild (a dependency's
coalesced copy, an import the old requirements cover) is scored as the
library's own surface change. The invariant is checked over the whole small
domain -- every export-presence kind x every binding combination x owner --
against an oracle written from the rule's statement, not from the
implementation's helpers; and over every version relation for imports.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.buildsource.export_accounting import toolchain_runtime_name_predicate
from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol, SymbolBinding
from abicheck.extract.stdlib_local_origin import is_user_specializable_std_symbol
from abicheck.model import AbiSnapshot
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.registry import Verdict
from abicheck.policy.toolchain_rebuild_drift import (
    COALESCED_COPY_RULE,
    COVERED_IMPORT_RULE,
    demote_toolchain_rebuild_drift,
)

# (mangled, dependency-owned?) -- the ownership oracle is the namespace a
# reader sees in the demangled name, written out by hand.
_NAMES = {
    "_ZN9__gnu_cxx11char_traitsIcE2eqERKcS3_": True,  # __gnu_cxx::char_traits<char>::eq
    "_ZNSt11char_traitsIcE6lengthEPKc": True,  # std::char_traits<char>::length
    "_ZStplIcSt11char_traitsIcESaIcEENSt7__cxx1112basic_stringIT_T0_T1_EEPKS5_RKS8_": True,
    "_ZN5mylib6detail6helperEv": False,  # the library's own namespace
    "_Z8name_lenv": False,  # a global function of the library
    "_ZNKSt4hashI6MyTypeEclERKS0_": False,  # std::hash<MyType>: user-authored
}

_EXPORT_KINDS = (
    ChangeKind.FUNC_REMOVED,
    ChangeKind.FUNC_REMOVED_ELF_ONLY,
    ChangeKind.VAR_REMOVED_ELF_ONLY,
    ChangeKind.FUNC_VISIBILITY_CHANGED,
    ChangeKind.SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED,
)

_BINDINGS = (SymbolBinding.GLOBAL, SymbolBinding.WEAK, SymbolBinding.UNIQUE, None)


def _owned(name: str) -> bool:
    return _NAMES[name]


def _elf(symbols: dict[str, SymbolBinding | None], **kw: object) -> ElfMetadata:
    return ElfMetadata(
        symbols=[ElfSymbol(name=n, binding=b) for n, b in symbols.items() if b],
        **kw,  # type: ignore[arg-type]
    )


def _change(kind: ChangeKind, symbol: str) -> Change:
    return Change(kind=kind, symbol=symbol, description="d")


@pytest.mark.parametrize(
    ("name", "kind", "old_b", "new_b"),
    [
        (n, k, o, w)
        for n, k, (o, w) in itertools.product(
            _NAMES, _EXPORT_KINDS, itertools.product(_BINDINGS, _BINDINGS)
        )
        if (o, w) != (None, None)
    ],
)
def test_only_a_coalesced_dependency_copy_is_demoted(name, kind, old_b, new_b):
    coalesced = {SymbolBinding.WEAK, SymbolBinding.UNIQUE}
    expected = _owned(name) and all(b in coalesced for b in (old_b, new_b) if b)
    change = _change(kind, name)
    demote_toolchain_rebuild_drift(
        [change], _elf({name: old_b}), _elf({name: new_b}), _owned
    )
    if expected:
        assert change.effective_verdict is Verdict.COMPATIBLE
        assert change.modulation_rule == COALESCED_COPY_RULE
    else:
        assert change.effective_verdict is None
        assert change.modulation_rule is None


def test_unrelated_kind_on_a_coalesced_copy_is_untouched():
    name = "_ZNSt11char_traitsIcE6lengthEPKc"
    change = _change(ChangeKind.SYMBOL_SIZE_CHANGED, name)
    weak = _elf({name: SymbolBinding.WEAK})
    demote_toolchain_rebuild_drift([change], weak, weak, _owned)
    assert change.effective_verdict is None


def test_a_prior_modulation_is_kept():
    name = "_ZNSt11char_traitsIcE6lengthEPKc"
    change = _change(ChangeKind.FUNC_REMOVED_ELF_ONLY, name)
    change.effective_verdict = Verdict.COMPATIBLE_WITH_RISK
    change.modulation_rule = "earlier"
    demote_toolchain_rebuild_drift(
        [change], _elf({name: SymbolBinding.WEAK}), _elf({}), _owned
    )
    assert (change.effective_verdict, change.modulation_rule) == (
        Verdict.COMPATIBLE_WITH_RISK,
        "earlier",
    )


# (old requirement held for libstdc++.so.6, new import's version, covered?)
_IMPORT_CASES = [
    ("GLIBCXX_3.4.21", "GLIBCXX_3.4.21", True),  # same node
    ("GLIBCXX_3.4.21", "GLIBCXX_3.4", True),  # older node of the same series
    ("GLIBCXX_3.4.21", "GLIBCXX_3.4.9", True),  # 3.4.9 < 3.4.21 numerically
    ("GLIBCXX_3.4.21", "GLIBCXX_3.4.30", False),  # newer node: a real new floor
    ("GLIBCXX_3.4.21", "CXXABI_1.3", False),  # other series, never held
    ("GLIBCXX_3.4.21", "GLIBCXX_PRIVATE", False),  # unparseable
    ("GLIBCXX_3.4.21", "", False),  # unversioned
    (None, "GLIBCXX_3.4", False),  # provider not required before
]


@pytest.mark.parametrize(("held", "wanted", "covered"), _IMPORT_CASES)
def test_import_is_demoted_only_when_the_old_requirements_cover_it(
    held, wanted, covered
):
    lib = "libstdc++.so.6"
    old = _elf({}, versions_required={lib: [held]} if held else {})
    new = _elf(
        {},
        versions_required={lib: [wanted]} if wanted else {},
        imports=[
            ElfImport(
                name="_ZdlPv", version=wanted, version_soname=lib if wanted else ""
            )
        ],
    )
    change = _change(ChangeKind.IMPORTED_SYMBOL_ADDED, "_ZdlPv")
    demote_toolchain_rebuild_drift([change], old, new, lambda _n: False)
    assert (change.effective_verdict is Verdict.COMPATIBLE) is covered
    assert (change.modulation_rule == COVERED_IMPORT_RULE) is covered


def test_import_from_another_provider_is_not_covered():
    old = _elf({}, versions_required={"libc.so.6": ["GLIBC_2.34"]})
    new = _elf(
        {},
        versions_required={"libm.so.6": ["GLIBC_2.2.5"]},
        imports=[
            ElfImport(name="cos", version="GLIBC_2.2.5", version_soname="libm.so.6")
        ],
    )
    change = _change(ChangeKind.IMPORTED_SYMBOL_ADDED, "cos")
    demote_toolchain_rebuild_drift([change], old, new, lambda _n: False)
    assert change.effective_verdict is None


def test_the_shipped_origin_predicate_matches_the_ownership_oracle():
    snap = AbiSnapshot(library="libx.so", version="1")
    snap.elf = ElfMetadata(needed=["libstdc++.so.6", "libc.so.6"])
    is_owned = toolchain_runtime_name_predicate([snap])
    assert {n: is_owned(n) for n in _NAMES} == _NAMES


# A program may specialize a standard template only for a program-defined type
# ([namespace.std]/2): over builtins only, the specialization is the runtime's.
_SPECIALIZATIONS = [
    ("_ZNSt11char_traitsIcE6lengthEPKc", False),
    ("_ZNSt11char_traitsIwE6lengthEPKw", False),
    ("_ZNSt11char_traitsIDiE6lengthEPKDi", False),
    ("_ZNKSt4hashImEclEm", False),
    ("_ZNSt14numeric_limitsIdE3maxEv", False),
    ("_ZNKSt4hashI6MyTypeEclERKS0_", True),
    ("_ZNKSt4hashIP6MyTypeEclES1_", True),  # pointer to a program type
    ("_ZNSt11char_traitsIN5mylib4CharEE6lengthEPKS1_", True),
]


@pytest.mark.parametrize(("name", "user_owned"), _SPECIALIZATIONS)
def test_customization_point_is_user_owned_only_over_a_program_type(name, user_owned):
    assert is_user_specializable_std_symbol(name) is user_owned
