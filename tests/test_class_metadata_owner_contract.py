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

"""A class-metadata export (``_ZTV``/``_ZTI``/``_ZTS``/``_ZTT``) belongs to
its class, and the class's header declaration decides whether its removal is
in contract.

Bug class (field report, ELF-only library, clang header frontend): a public
polymorphic class declared in a public header had its vtable exported; the
new binary dropped that export. The removal was classified as in-surface but
never *confirmed*: the mangled spelling never equals a declared type, so the
finding read ``UNKNOWN_UNRESOLVED`` (Not Evaluated,
``required_evidence_incomplete``) and never reached the verdict, although the
class declaration was present in the header.

The invariant, over every class-metadata prefix and namespace depth:

- the owner is recovered exactly from the mangling;
- a removal whose owning class a public header declares is in contract and
  breaks the verdict;
- a removal whose owning class no header declares is not confirmed in
  contract (it stays reported, it does not score as breaking);
- a template-specialization owner is not confirmed by its primary template.

The oracle is the hand-written Itanium spelling built in ``_spell`` below,
never the parser under test, so a wrong owner in the parser cannot also be
the expected value.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.contract_relevance_types import ContractRelevance
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, RecordType, Visibility
from abicheck.model.elf_facts import SymbolBinding, SymbolType
from abicheck.model.owner_recovery import (
    itanium_class_metadata_owner,
    itanium_special_name_owner_identifiers,
)
from abicheck.model.vocabulary import ScopeOrigin

PREFIXES = ("_ZTV", "_ZTI", "_ZTS", "_ZTT")

#: Each path is a class's enclosing namespaces followed by the class itself.
#: Depth 1 is a global class (no ``N...E`` wrapper); the deeper ones nest.
PATHS = (
    ("Widget",),
    ("api", "Widget"),
    ("svs", "runtime", "v0", "Widget"),
    ("a", "b", "c", "d", "Widget"),
)

_FUNC = "_ZN3api4makeEv"


def _spell(prefix: str, path: tuple[str, ...]) -> str:
    """Hand-written Itanium spelling of *prefix* applied to the class at *path*.

    ``<length><name>`` per component, wrapped in ``N...E`` when nested.
    """
    body = "".join(f"{len(part)}{part}" for part in path)
    if len(path) > 1:
        body = f"N{body}E"
    return f"{prefix}{body}"


def _snapshot(
    version: str,
    *,
    class_path: tuple[str, ...] | None,
    exports: tuple[str, ...],
    qualified_name: str | None = None,
    vtable: tuple[str, ...] = (),
) -> AbiSnapshot:
    """A header-aware snapshot: a public function, optionally one public-header
    class, and the given class-metadata exports in the ELF table.

    *qualified_name* overrides the header's spelling of the class (clang drops an
    inline namespace from it); *vtable* is the class's own virtual-slot list."""
    function = Function(
        name="make",
        mangled=_FUNC,
        return_type="int",
        visibility=Visibility.PUBLIC,
        source_header="api.h",
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    types = []
    if class_path is not None:
        types.append(
            RecordType(
                name=class_path[-1],
                kind="class",
                size_bits=64,
                qualified_name=qualified_name or "::".join(class_path),
                source_header="api.h",
                origin=ScopeOrigin.PUBLIC_HEADER,
                vtable=list(vtable),
            )
        )
    symbols = [
        ElfSymbol(name=_FUNC, binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC)
    ]
    symbols += [
        ElfSymbol(name=name, binding=SymbolBinding.GLOBAL, sym_type=SymbolType.OBJECT)
        for name in exports
    ]
    return AbiSnapshot(
        library="libapi.so",
        version=version,
        functions=[function],
        types=types,
        from_headers=True,
        elf=ElfMetadata(machine="x86-64", symbols=symbols),
    )


def _removal_of(mangled: str, *, class_path: tuple[str, ...] | None):
    """Compare a version that exports *mangled* against one that drops it, in
    the stated ``public`` contract, and return the result."""
    old = _snapshot("1.0", class_path=class_path, exports=(mangled,))
    new = _snapshot("2.0", class_path=class_path, exports=())
    return compare(old, new, contract_evaluation=True, contract_mode="public")


def _change_for(result, mangled: str):
    matches = [c for c in result.changes if c.symbol == mangled]
    assert len(matches) == 1, [c.symbol for c in result.changes]
    return matches[0]


# ---------------------------------------------------------------------------
# 1. The owner is recovered exactly from every prefix and depth.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("path", PATHS, ids=lambda p: "::".join(p))
def test_owner_is_the_exact_class_path(prefix: str, path: tuple[str, ...]) -> None:
    assert itanium_class_metadata_owner(_spell(prefix, path)) == "::".join(path)


@pytest.mark.parametrize("path", PATHS, ids=lambda p: "::".join(p))
def test_typeinfo_name_has_a_surface_owner(path: tuple[str, ...]) -> None:
    """``_ZTS`` was the one class-metadata prefix the owner parser skipped, so
    a typeinfo name fell through to the optional demangler. It must resolve to
    its class like the other three, with no demangler involved."""
    owners = itanium_special_name_owner_identifiers(_spell("_ZTS", path))
    assert owners is not None
    assert "::".join(path) in owners


@pytest.mark.parametrize(
    "mangled",
    ["_ZN3api4makeEv", "_Z4drawi", "make"],
    ids=["ordinary-member", "free-function", "unmangled"],
)
def test_non_class_metadata_symbol_has_no_class_owner(mangled: str) -> None:
    assert itanium_class_metadata_owner(mangled) is None


def test_template_specialization_owner_is_not_recovered_exactly() -> None:
    """``_ZTVN3api3BoxIiEE`` is the vtable of ``api::Box<int>``. The path does
    not spell the arguments, and a primary template's declaration is not
    evidence about one of its specializations, so no exact owner is claimed."""
    assert itanium_class_metadata_owner("_ZTVN3api3BoxIiEE") is None


# ---------------------------------------------------------------------------
# 2. Declared class: the removal is in contract, breaks, and says which class.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("path", PATHS, ids=lambda p: "::".join(p))
def test_removal_of_a_declared_class_metadata_is_in_contract_and_breaks(
    prefix: str, path: tuple[str, ...]
) -> None:
    mangled = _spell(prefix, path)
    result = _removal_of(mangled, class_path=path)
    change = _change_for(result, mangled)

    assert change.kind is ChangeKind.VAR_REMOVED_ELF_ONLY
    assert change.contract_relevance is ContractRelevance.IN_CONTRACT
    assert result.verdict is Verdict.BREAKING
    label = {
        "_ZTV": "vtable",
        "_ZTI": "typeinfo",
        "_ZTS": "typeinfo name",
        "_ZTT": "VTT",
    }[prefix]
    assert change.description == (
        f"Removed exported C++ ABI artifact ({label}) of class "
        f"{'::'.join(path)}: {mangled}"
    )


# ---------------------------------------------------------------------------
# 3. Undeclared class, and a template owner: never confirmed in contract, and
#    never dropped.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("path", PATHS, ids=lambda p: "::".join(p))
def test_removal_of_an_undeclared_class_metadata_is_not_in_contract(
    prefix: str, path: tuple[str, ...]
) -> None:
    mangled = _spell(prefix, path)
    result = _removal_of(mangled, class_path=None)
    change = _change_for(result, mangled)

    assert change.contract_relevance is not ContractRelevance.IN_CONTRACT
    assert result.verdict is not Verdict.BREAKING


def test_template_owner_is_not_confirmed_by_its_primary_template() -> None:
    """The primary template ``api::Box`` is declared; the exported vtable is the
    specialization ``api::Box<int>``. The header does not declare that
    specialization, so the removal must not be confirmed in contract."""
    mangled = "_ZTVN3api3BoxIiEE"
    old = _snapshot("1.0", class_path=("api", "Box"), exports=(mangled,))
    new = _snapshot("2.0", class_path=("api", "Box"), exports=())
    result = compare(old, new, contract_evaluation=True, contract_mode="public")
    change = _change_for(result, mangled)

    assert change.contract_relevance is not ContractRelevance.IN_CONTRACT
    assert result.verdict is not Verdict.BREAKING


# ---------------------------------------------------------------------------
# 4. Inline namespaces: the header drops ``v0``, the mangling keeps it.
# ---------------------------------------------------------------------------

#: What clang records for ``svs::runtime::v0::DynamicIVFIndex`` (no ``v0``), and
#: its own virtual slot, mangled with the inline namespace spelled out.
_INLINE_QUALIFIED = "svs::runtime::DynamicIVFIndex"
_INLINE_OWNER = "svs::runtime::v0::DynamicIVFIndex"
_INLINE_SLOT = "_ZNK3svs7runtime2v015DynamicIVFIndex4sizeEv"


def test_inline_namespace_spelled_only_by_the_mangler_confirms_a_declared_class() -> (
    None
):
    """The header's qualified name omits the inline namespace ``v0``, but the
    class's own virtual slot spells it, so the class is still proven declared
    and its vtable removal is in contract."""
    mangled = _spell("_ZTV", ("svs", "runtime", "v0", "DynamicIVFIndex"))
    assert itanium_class_metadata_owner(mangled) == _INLINE_OWNER
    old = _snapshot(
        "1.0",
        class_path=("svs", "runtime", "DynamicIVFIndex"),
        qualified_name=_INLINE_QUALIFIED,
        vtable=("~DynamicIVFIndex", _INLINE_SLOT),
        exports=(mangled,),
    )
    new = _snapshot(
        "2.0",
        class_path=("svs", "runtime", "DynamicIVFIndex"),
        qualified_name=_INLINE_QUALIFIED,
        vtable=("~DynamicIVFIndex", _INLINE_SLOT),
        exports=(),
    )
    result = compare(old, new, contract_evaluation=True, contract_mode="public")
    change = _change_for(result, mangled)

    assert change.contract_relevance is ContractRelevance.IN_CONTRACT
    assert result.verdict is Verdict.BREAKING


def test_a_same_leaf_class_in_another_namespace_is_not_confirmed_by_slots() -> None:
    """A declared ``svs::runtime::DynamicIVFIndex`` proves only its own owner,
    ``svs::runtime::v0::DynamicIVFIndex``. An exported vtable of a different
    class that shares the leaf, ``svs::runtime::v0::other::DynamicIVFIndex``,
    must not be confirmed by that proof."""
    mangled = _spell("_ZTV", ("svs", "runtime", "v0", "other", "DynamicIVFIndex"))
    old = _snapshot(
        "1.0",
        class_path=("svs", "runtime", "DynamicIVFIndex"),
        qualified_name=_INLINE_QUALIFIED,
        vtable=("~DynamicIVFIndex", _INLINE_SLOT),
        exports=(mangled,),
    )
    new = _snapshot(
        "2.0",
        class_path=("svs", "runtime", "DynamicIVFIndex"),
        qualified_name=_INLINE_QUALIFIED,
        vtable=("~DynamicIVFIndex", _INLINE_SLOT),
        exports=(),
    )
    result = compare(old, new, contract_evaluation=True, contract_mode="public")
    change = _change_for(result, mangled)

    assert change.contract_relevance is not ContractRelevance.IN_CONTRACT
    assert result.verdict is not Verdict.BREAKING
