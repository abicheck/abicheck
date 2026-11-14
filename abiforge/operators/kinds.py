"""Operators that each reach one ChangeKind no other operator expects.

Each edits one ``k.*`` role of the ``cxx_more`` seed (``_KIND_ROLES`` in
``abiforge.model.seeds``), a self-contained declaration group made for it.
The expectation comes from what the edit does to the C++ ABI (Itanium
layout and mangling, ELF exports), stated in each operator's ``why``.
"""

from __future__ import annotations

from typing import Any

from abiforge.model.config import Config
from abiforge.model.opkit import Edit, EditOp, Roles, _set
from abiforge.model.seeds import XCXX_SEED

__all__ = ["KIND_OPERATORS"]


def _op(name: str, role: str, edit: Edit, why: str, **kw: Any) -> EditOp:
    kw.setdefault("touches", (role,))
    return EditOp(name, (role,), edit, why, **kw)


def _replace(
    role: str, *pairs: tuple[str, str], source: tuple[tuple[str, str], ...] = ()
) -> Edit:
    """Re-spell fragments of *role*'s header (and source) text."""
    header, src = XCXX_SEED.roles[role]
    for old, new in pairs:
        if old not in header:
            raise ValueError(f"{role}: {old!r} is not in its header text")
        header = header.replace(old, new)
    for old, new in source:
        if old not in src:
            raise ValueError(f"{role}: {old!r} is not in its source text")
        src = src.replace(old, new)

    def edit(r: Roles) -> dict[str, tuple[str, str]]:
        return _set(r, role, header, src)

    return edit


def _no_pack(c: Config) -> str | None:
    return (
        "-fpack-struct leaves no padding to reuse" if c.flags.pack is not None else None
    )


def _lp64(c: Config) -> str | None:
    return "long is narrower than long long under -m32" if c.flags.bits != 64 else None


_COMPAT = {"verdict_min": "COMPATIBLE", "verdict_max": "COMPATIBLE"}
_RISK = {"verdict_min": "COMPATIBLE_WITH_RISK", "verdict_max": "COMPATIBLE_WITH_RISK"}
_BINARY = {"min_evidence": "binary"}
_HEADERS = {"min_evidence": "headers"}

KIND_OPERATORS: tuple[EditOp, ...] = (
    _op(
        "virtual_bases_reordered",
        "k.vbo",
        _replace("k.vbo", ("virtual KVA, virtual KVB", "virtual KVB, virtual KVA")),
        "the two virtual bases swap declaration order: their subobjects swap "
        "places at the end of KVD and the vbase offsets in KVD's vtable are "
        "reassigned, so code compiled against the old order reaches the wrong base",
        expected=frozenset({"VIRTUAL_BASE_OFFSET_CHANGED"}),
    ),
    _op(
        "secondary_base_became_polymorphic",
        "k.svg",
        _replace(
            "k.svg",
            ("struct KSB { int x; };", "struct KSB { virtual int g(); int x; };"),
            source=(
                (
                    "int KSA::f() { return 1; }",
                    "int KSA::f() { return 1; }\nint KSB::g() { return 2; }",
                ),
            ),
        ),
        "KSD's second base gains a vtable pointer: KSD now has a secondary vtable "
        "group for KSB, its KSB subobject grows, and d moves",
        expected=frozenset({"SECONDARY_VTABLE_GROUP_CHANGED"}),
    ),
    _op(
        "reserved_field_used",
        "k.rsv",
        _replace("k.rsv", ("int reserved1;", "int flags;")),
        "a reserved slot is given a name and a use: same type, same offset, "
        "the layout is unchanged",
        expected=frozenset({"USED_RESERVED_FIELD"}),
        **_COMPAT,
    ),
    _op(
        "inline_namespace_version_bumped",
        "k.inv",
        _replace(
            "k.inv",
            ("inline namespace v1", "inline namespace v2"),
            source=(("inline namespace v1", "inline namespace v2"),),
        ),
        "the inline namespace is bumped to v2: every symbol in it re-mangles, "
        "so binaries built against v1 no longer find kv::kv_f",
        expected=frozenset({"INLINE_NAMESPACE_VERSION_BUMPED"}),
        **_BINARY,
    ),
    _op(
        "experimental_api_graduated",
        "k.exp",
        _replace(
            "k.exp",
            (
                "int kx_g(int a); } }",
                "int kx_g(int a); } }\nnamespace kx { int kx_f(int a); }",
            ),
            source=(("} }", "} }\nnamespace kx { int kx_f(int a) { return a; } }"),),
        ),
        "kx::experimental::kx_f gains a stable kx::kx_f twin and keeps its "
        "experimental copy: purely additive",
        expected=frozenset({"EXPERIMENTAL_GRADUATED"}),
        **_COMPAT,
    ),
    _op(
        "experimental_api_removed",
        "k.exp",
        _replace(
            "k.exp",
            (" int kx_g(int a); } }", " } }"),
            source=(("\nint kx_g(int a) { return a + 1; }", ""),),
        ),
        "kx::experimental::kx_g is removed with no stable replacement: binaries "
        "that used the experimental API no longer load",
        expected=frozenset({"EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT"}),
        **_BINARY,
    ),
    _op(
        "function_became_customization_object",
        "k.cpo",
        _replace(
            "k.cpo",
            (
                "namespace kc { int kc_swap(int a); }",
                "namespace kc { struct kc_swap_fn { int operator()(int a) const; };\n"
                "extern const kc_swap_fn kc_swap; }",
            ),
            source=(
                (
                    "namespace kc { int kc_swap(int a) { return -a; } }",
                    "namespace kc { int kc_swap_fn::operator()(int a) const { return -a; }\n"
                    "const kc_swap_fn kc_swap{}; }",
                ),
            ),
        ),
        "kc::kc_swap becomes a function object: calls still compile, but the "
        "function symbol is gone and a variable takes its name",
        expected=frozenset({"CPO_KIND_CHANGED"}),
    ),
    _op(
        "template_return_argument_changed",
        "k.ret",
        _replace(
            "k.ret",
            ("KBx<int> kbx_get();", "KBx<long> kbx_get();"),
            source=(
                (
                    "KBx<int> kbx_get() { return KBx<int>{1}; }",
                    "KBx<long> kbx_get() { return KBx<long>{1}; }",
                ),
            ),
        ),
        "the return type changes from KBx<int> to KBx<long>: the return type "
        "of a non-template function is not mangled, so the symbol stays and "
        "old callers read 4 bytes of an 8-byte value",
        expected=frozenset({"TEMPLATE_RETURN_TYPE_CHANGED"}),
    ),
    _op(
        "template_param_argument_changed",
        "k.tpp",
        _replace(
            "k.tpp",
            ("KPb<int> b", "KPb<long> b"),
            source=(("KPb<int> b", "KPb<long> b"),),
        ),
        'the parameter changes from KPb<int> to KPb<long>; an extern "C" name '
        "carries no parameter types, so old callers still bind and pass the "
        "wrong-sized argument",
        expected=frozenset({"TEMPLATE_PARAM_TYPE_CHANGED"}),
    ),
    _op(
        "field_retyped_abi_equivalent",
        "k.aeq",
        _replace("k.aeq", ("long a;", "long long a;")),
        "long becomes long long: on LP64 both are 8-byte, 8-aligned INTEGER "
        "class, so the layout and calling convention are unchanged",
        expected=frozenset({"ABI_EQUIVALENT_TYPE_CHANGED"}),
        precondition=_lp64,
        verdict_min="COMPATIBLE",
        verdict_max="COMPATIBLE_WITH_RISK",
    ),
    _op(
        "declared_function_lost_definition",
        "k.del",
        lambda r: _set(
            r,
            "k.del",
            None,
            XCXX_SEED.roles["k.del"][1].replace(
                "\nint kdl_gone(int a) { return a + 1; }", ""
            ),
        ),
        "the header still declares kdl_gone but the library no longer defines "
        "or exports it: binaries that import it no longer load. The new "
        "library's export table shows it gone while the header still declares "
        "it, so the loss reads as a visibility change of a declared function",
        expected=frozenset({"FUNC_VISIBILITY_CHANGED"}),
        **_BINARY,
    ),
    _op(
        "unnamed_type_api_added",
        "k.anon",
        lambda r: _set(
            r,
            "k.anon",
            "struct KAn { struct { int v; } m; };\nint kanon_use(decltype(KAn::m)* p);",
            "int kanon_use(decltype(KAn::m)* p) { return p->v; }",
        ),
        "a new export mangles an unnamed type (Ut_): its symbol name depends on "
        "the unnamed type's position in KAn, so a harmless reorder later "
        "renames the export",
        expected=frozenset({"UNNAMED_TYPE_IN_PUBLIC_ABI"}),
        **_RISK,
        **_BINARY,
    ),
    _op(
        "pimpl_member_renamed_under_inline_body",
        "k.ibr",
        _replace("k.ibr", ("int count;", "int size_;"), ("p->count", "p->size_")),
        "a member of the detail:: implementation is renamed and the public "
        "inline accessor follows it: the offset is unchanged, but every "
        "consumer carries its own compiled copy of the inline body, so the "
        "detail type has become part of the interface",
        expected=frozenset({"INLINE_BODY_REFERENCES_RENAMED_MEMBER"}),
        verdict_min="COMPATIBLE",
        verdict_max=None,
        **_HEADERS,
    ),
)
