"""Recorded corpus mismatches: strict xfails, each tied to a known-gaps entry.

A ``Known`` entry says: for this operator on these seeds, under configs where
``when`` holds, the cell reconciles to exactly ``cls``. The corpus test
xfails such a cell only while that class reproduces; any other class, a pass
included, fails, so a fixed detector forces the entry (and its
``docs/contribute/known-gaps.md`` row, named in ``gap``) to be removed.

Nothing here is fixed in abicheck: entries record detector findings, they do
not excuse harness bugs (``oracle-conflict`` is never allowed).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tests._edit_corpus_config import Config

_CXX_SEEDS = frozenset({"cxx_classes", "cxx_stdlib"})
_CLASSES = frozenset({"cxx_classes"})

GAP_CXX_PARAM = "A C++ parameter change is reported as a removal plus an addition"
GAP_VIRTUAL_REMOVED = "Removing a virtual method reports no `func_virtual_removed`"
GAP_NOEXCEPT = "castxml output carries no `noexcept` exception specification"
GAP_TEMPLATE_DEFAULT = (
    "A header-only default template-argument change reads `NO_CHANGE` at L2 on castxml"
)
GAP_CONCEPT = (
    "A new `requires`-clause on a header-only template reads `NO_CHANGE` under castxml"
)
GAP_DWARF_ARRAY = "A member array-bound change is invisible in DWARF-only comparison"
GAP_DWARF_PURE = (
    "DWARF-only comparison reports a virtual that became pure as a plain removal"
)
GAP_DWARF_CV = "DWARF-only comparison reports a method's dropped `const` as a removal plus an addition"
GAP_CXX11_ABI = "A `_GLIBCXX_USE_CXX11_ABI` rebuild is refused as a profile mismatch, not reported as a break"
GAP_O2_STD = "An `-O2` rebuild of a C++ library that uses `std::string` reads as a break or a risk"


def _always(_: Config) -> bool:
    return True


def _ev(*levels: str) -> Callable[[Config], bool]:
    return lambda c: c.evidence in levels


@dataclass(frozen=True)
class Known:
    op: str
    seeds: frozenset[str]
    cls: str
    when: Callable[[Config], bool]
    reason: str
    gap: str


KNOWN: tuple[Known, ...] = (
    *(
        Known(
            op,
            _CXX_SEEDS,
            "wrong-kind",
            _ev("headers"),
            "the mangled name changes, so old and new are not paired: func_removed + func_added, "
            "never func_params_changed (binary-only and DWARF evidence pair them)",
            GAP_CXX_PARAM,
        )
        for op in ("func_param_append", "func_param_type_change", "func_param_remove")
    ),
    Known(
        "virtual_remove",
        _CLASSES,
        "wrong-kind",
        _ev("dwarf", "headers"),
        "removal reads func_removed + vtable kinds; func_virtual_removed is never emitted",
        GAP_VIRTUAL_REMOVED,
    ),
    Known(
        "method_noexcept_add",
        _CLASSES,
        "false-clean",
        _ev("headers"),
        "castxml writes no exception specification: NO_CHANGE while an overrider without "
        "noexcept no longer compiles",
        GAP_NOEXCEPT,
    ),
    Known(
        "template_default_arg_change",
        _CLASSES,
        "wrong-verdict",
        _ev("headers"),
        "castxml reads a header-only template default change as NO_CHANGE",
        GAP_TEMPLATE_DEFAULT,
    ),
    Known(
        "concept_constraint_added",
        _CLASSES,
        "false-clean",
        _ev("headers"),
        "a requires-clause added to a header-only function template reads NO_CHANGE while a "
        "consumer call stops compiling",
        GAP_CONCEPT,
    ),
    Known(
        "struct_array_shrink_in_padding",
        frozenset({"c_core", "cxx_classes"}),
        "wrong-verdict",
        lambda c: c.evidence == "dwarf" and c.flags.pack is None,
        "char[8] -> char[6] reads NO_CHANGE from DWARF (headers report type_field_type_changed)",
        GAP_DWARF_ARRAY,
    ),
    Known(
        "struct_array_shrink_in_padding",
        frozenset({"c_core", "cxx_classes"}),
        "wrong-kind",
        lambda c: c.evidence == "dwarf" and c.flags.pack is not None,
        "packed, DWARF shows only type_size_changed; the member's type change is not reported",
        GAP_DWARF_ARRAY,
    ),
    Known(
        "virtual_become_pure",
        _CLASSES,
        "wrong-kind",
        _ev("dwarf"),
        "from DWARF the now-pure virtual reads func_removed_elf_only; neither "
        "func_virtual_became_pure nor type_became_abstract",
        GAP_DWARF_PURE,
    ),
    Known(
        "method_const_drop",
        _CLASSES,
        "wrong-kind",
        _ev("dwarf"),
        "from DWARF the const drop reads func_removed_elf_only + func_added_elf_only; "
        "func_cv_changed is not emitted (headers emit it)",
        GAP_DWARF_CV,
    ),
    Known(
        "rebuild_cxx11_abi_off",
        frozenset({"cxx_stdlib"}),
        "refused",
        _ev("headers"),
        "the new side's -D_GLIBCXX_USE_CXX11_ABI=0 makes compare raise ProfileMismatchError; "
        "consumers fail with undefined std::__cxx11 symbols",
        GAP_CXX11_ABI,
    ),
    Known(
        "rebuild_optimized",
        frozenset({"cxx_stdlib"}),
        "false-positive",
        _always,
        "-O2 inlines the weak std::string instantiations the -O0 build exported (and drops "
        "stack canaries); their disappearance reads func_visibility_changed / "
        "func_removed_elf_only (BREAKING) or a hardening risk, while every consumer still runs",
        GAP_O2_STD,
    ),
)


def known_mismatch(op: str, seed: str, config: Config) -> Known | None:
    for k in KNOWN:
        if k.op == op and seed in k.seeds and k.when(config):
            return k
    return None
