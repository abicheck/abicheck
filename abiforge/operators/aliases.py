"""Edit operators for the ``typedef`` and ``template`` classes of
``abiforge.model.edit_model``.

One operator per class that had none, each editing one ``td.*``/``tp.*``
probe of ``cxx_more`` (``abiforge.model.probes``). Ground truth is declared
from the Itanium C++ ABI's mangling: a typedef is not mangled, the type it
names is, so retargeting it re-mangles every function taking it; a class
template specialization's mangled name carries every template argument,
defaulted or not, with a non-type argument's type; a function template
specialization is exported only where the library instantiates or
explicitly specializes it.
"""

from __future__ import annotations

from typing import Any

from abiforge.model.opkit import _REMOVED, EditOp, _binary_mangled
from abiforge.operators.layout import _op, _replace

__all__ = ["ALIAS_OPERATORS"]

_PARAMS = frozenset(
    {"FUNC_PARAMS_CHANGED", "TEMPLATE_PARAM_TYPE_CHANGED", "TYPEDEF_BASE_CHANGED"}
)
_REMANGLED: dict[str, Any] = {
    "any_of": (_PARAMS | _REMOVED,),
    "min_evidence": "binary",
    "rules": (_binary_mangled(*_PARAMS),),
}

_TYPEDEFS = (
    _op(
        "typedef_retargeted_same_size",
        "td.same",
        _replace("td.same", "typedef int TdS;", "typedef unsigned TdS;"),
        "TdS names unsigned instead of int: the size is unchanged, but "
        "tds_use(TdS) re-mangles from tds_use(int) to tds_use(unsigned), so old "
        "callers' symbol is gone",
        **_REMANGLED,
    ),
    _op(
        "typedef_added",
        "td.added",
        lambda r: {"td.added": ("typedef int TdNew;", "")},
        # abicheck records no kind for a typedef addition by design
        # (compare/typedefs.py): a new name for an existing type adds no
        # entity, so the truth is only that nothing breaks.
        "a new typedef is declared: nothing existing changes",
        verdict_min="NO_CHANGE",
        verdict_max="COMPATIBLE",
        min_evidence="headers",
    ),
    _op(
        "typedef_respelled_as_alias",
        "td.respell",
        _replace("td.respell", "typedef int TdR;", "using TdR = int;"),
        "typedef int TdR becomes using TdR = int: the same type under the same "
        "name, so nothing changes",
        verdict_min="NO_CHANGE",
        verdict_max="NO_CHANGE",
        min_evidence="headers",
    ),
)

_TEMPLATES = (
    _op(
        "template_added",
        "tp.add",
        lambda r: {"tp.add": ("template <class T> struct TpNew { T v; };", "")},
        "a new class template is declared: nothing is instantiated or exported, "
        "nothing existing changes",
        verdict_min="NO_CHANGE",
        verdict_max="COMPATIBLE",
        min_evidence="headers",
    ),
    _op(
        "template_param_added_defaulted",
        "tp.adddef",
        _replace(
            "tp.adddef",
            "template <class T> struct TpA { T v; };",
            "template <class T, class U = int> struct TpA { T v; };",
        ),
        "TpA gains a defaulted parameter: source spelling TpA<int> still "
        "compiles, but it is now TpA<int, int>, so tpa_use re-mangles and old "
        "callers' symbol is gone",
        **_REMANGLED,
    ),
    _op(
        "template_param_removed",
        "tp.rmparam",
        _replace(
            "tp.rmparam",
            "template <class T, class U = int> struct TpR { T v; };",
            "template <class T> struct TpR { T v; };",
        ),
        "TpR loses its defaulted parameter: TpR<int> was TpR<int, int>, so "
        "tpr_use re-mangles and old callers' symbol is gone",
        **_REMANGLED,
    ),
    _op(
        "template_nontype_param_retyped",
        "tp.retype",
        _replace(
            "tp.retype",
            "template <int N> struct TpK { int v[N]; };",
            "template <long N> struct TpK { int v[N]; };",
        ),
        "TpK's non-type parameter becomes long: TpK<2> mangles its argument as "
        "a long, so tpk_use re-mangles and old callers' symbol is gone",
        **_REMANGLED,
    ),
    _op(
        "template_instantiation_added",
        "tp.instadd",
        _replace(
            "tp.instadd",
            "extern template int tpi<int>(int);",
            "extern template int tpi<int>(int);\nextern template long tpi<long>(long);",
            source=(
                "template int tpi<int>(int);",
                "template int tpi<int>(int);\ntemplate long tpi<long>(long);",
            ),
        ),
        "the library also instantiates and promises tpi<long>: a new export, "
        "nothing existing changes",
        expected=frozenset({"FUNC_ADDED"}),
        verdict_min="COMPATIBLE",
        verdict_max="COMPATIBLE",
        min_evidence="binary",
    ),
    _op(
        "template_specialization_added",
        "tp.specadd",
        _replace(
            "tp.specadd",
            "int tps_use(int v);",
            "template <> int tps<int>(int v);\nint tps_use(int v);",
            source=(
                "int tps_use(int v) { return tps(v); }",
                "template <> int tps<int>(int v) { return v; }\n"
                "int tps_use(int v) { return tps(v); }",
            ),
        ),
        "an explicit specialization tps<int> replaces the library's weak "
        "instantiation of the primary template with a strong definition that "
        "does something else: old consumers keep their own inlined or weak "
        "copy of the primary, so tps<int> behaves differently on each side",
        verdict_min="COMPATIBLE",
        verdict_max="COMPATIBLE_WITH_RISK",
        min_evidence="binary",
    ),
    _op(
        "template_specialization_removed",
        "tp.specrm",
        _replace(
            "tp.specrm",
            "template <> int tpz<int>(int v);",
            "",
            source=("template <> int tpz<int>(int v) { return v; }", ""),
        ),
        "the explicit specialization tpz<int> goes: old consumers that called "
        "it hold an undefined reference to its symbol and fail to load",
        any_of=(_REMOVED,),
        min_evidence="binary",
    ),
)

ALIAS_OPERATORS: tuple[EditOp, ...] = (*_TYPEDEFS, *_TEMPLATES)
