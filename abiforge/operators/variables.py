"""Edit operators for the ``variable`` classes of ``abiforge.model.edit_model``.

One operator per variable edit class that had none: the ``vr.*`` probes of
``cxx_more`` (``abiforge.model.probes``) and ``g_obj`` of ``c_elf`` for the
version node. Ground truth is declared from ELF and the SysV psABI: a
consumer that references an exported object copy-relocates it, so its
``st_size`` and type are fixed in the consumer, its symbol name is the only
thing that binds a reference, a weak or vague-linkage definition in a DSO
still satisfies an existing reference, and a version node is part of the
symbol's identity for a versioned reference.
"""

from __future__ import annotations

from typing import Any

from abiforge.model.config import Config
from abiforge.model.opkit import EditOp, _merge, _set
from abiforge.operators.layout import _replace

__all__ = ["VARIABLE_OPERATORS"]

_REMOVED = frozenset({"VAR_REMOVED", "VAR_REMOVED_ELF_ONLY"})
_AT_MOST_RISK: dict[str, Any] = {
    "verdict_min": "COMPATIBLE",
    "verdict_max": "COMPATIBLE_WITH_RISK",
}


def _cxx17(c: Config) -> str | None:
    return None if c.std.year >= 2017 else "inline variables need C++17"


def _op(name: str, role: str, edit: Any, why: str, **kw: Any) -> EditOp:
    kw.setdefault("touches", (role,))
    return EditOp(name, kw.pop("roles", (role,)), edit, why, **kw)


VARIABLE_OPERATORS: tuple[EditOp, ...] = (
    _op(
        "object_shrank",
        "vr.shrink",
        _replace(
            "vr.shrink",
            "extern long long g_vshr;",
            "extern int g_vshr;",
            source=("long long g_vshr = 1;", "int g_vshr = 1;"),
        ),
        "long long -> int: the object shrinks 8 -> 4 bytes, but a consumer's "
        "copy relocation and every access it compiled still use 8",
        any_of=(frozenset({"VAR_TYPE_CHANGED", "SYMBOL_SIZE_CHANGED"}),),
    ),
    _op(
        "object_retyped_same_size",
        "vr.same",
        _replace(
            "vr.same",
            "extern int g_vsame;",
            "extern unsigned g_vsame;",
            source=("int g_vsame = 2;", "unsigned g_vsame = 2;"),
        ),
        "int -> unsigned: the size and symbol stay, but the value range old "
        "code reads and writes changes",
        expected=frozenset({"VAR_TYPE_CHANGED"}),
    ),
    _op(
        "object_became_weak",
        "vr.weak",
        _replace(
            "vr.weak",
            "extern int g_vweak;",
            "extern int g_vweak;",
            source=("int g_vweak = 3;", "__attribute__((weak)) int g_vweak = 3;"),
        ),
        "a weak definition in a DSO still satisfies a consumer's reference",
        expected=frozenset({"SYMBOL_BINDING_CHANGED"}),
        min_evidence="binary",
        **_AT_MOST_RISK,
    ),
    _op(
        "object_renamed",
        "vr.rename",
        _replace(
            "vr.rename",
            "extern int g_vold;",
            "extern int g_vnew;",
            source=("int g_vold = 4;", "int g_vnew = 4;"),
        ),
        "g_vold becomes g_vnew: consumers referencing g_vold fail to load",
        any_of=(_REMOVED,),
        min_evidence="binary",
    ),
    _op(
        "object_became_inline_variable",
        "vr.inline",
        _replace(
            "vr.inline",
            "extern int g_vinl;",
            "inline int g_vinl = 5;",
            source=("int g_vinl = 5;\n", ""),
        ),
        "g_vinl becomes a C++17 inline variable: the library still emits it "
        "(vinl_get odr-uses it), now with vague linkage (STB_GNU_UNIQUE or "
        "weak), so existing references bind as before",
        any_of=(
            frozenset(
                {
                    "SYMBOL_BINDING_CHANGED",
                    "SYMBOL_BINDING_BECAME_UNIQUE",
                }
            ),
        ),
        min_evidence="binary",
        precondition=_cxx17,
        **_AT_MOST_RISK,
    ),
    _op(
        "object_moved_version_node",
        "build.vscript",
        lambda r: _merge(
            _set(
                r,
                "build.vscript",
                "LIBEDIT_1.0 { global: *; };\nLIBEDIT_1.1 { global: g_obj; } LIBEDIT_1.0;",
            )
        ),
        "g_obj@@LIBEDIT_1.0 becomes g_obj@@LIBEDIT_1.1: a consumer linked "
        "against the old library asks for g_obj@LIBEDIT_1.0 and fails to load",
        roles=("build.vscript", "elf.obj"),
        expected=frozenset({"SYMBOL_MOVED_VERSION_NODE"}),
        min_evidence="binary",
    ),
)
