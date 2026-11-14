"""Operators that each reach one ChangeKind no other operator expects.

Each edits one ``k.*`` role of the ``cxx_more`` seed (``_KIND_ROLES`` in
``abiforge.model.seeds``), a self-contained declaration group made for it.
The expectation comes from what the edit does to the C++ ABI (Itanium
layout and mangling, ELF exports), stated in each operator's ``why``.
"""

from __future__ import annotations

from typing import Any

from abiforge.model.config import Config
from abiforge.model.expect import Rule
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
    """long and long long are the same type only on an LP64 target."""
    if c.flags.bits != 64:
        return "long is narrower than long long under -m32"
    if c.objfmt == "pe":
        return "Windows is LLP64: long is 32-bit, long long 64-bit"
    return None


def _elf(c: Config) -> str | None:
    """An asm label names the ELF symbol as written (no Mach-O underscore)."""
    return None if c.objfmt == "elf" else "the asm label spells an ELF symbol name"


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
        # A source-namespace fact: abicheck reads it from declarations (DWARF
        # or headers), never from a bare export table (in_source_declaration_index).
        min_evidence="dwarf",
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
        # A source-namespace fact: abicheck reads it from declarations (DWARF
        # or headers), never from a bare export table (in_source_declaration_index).
        min_evidence="dwarf",
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
        rules=(
            Rule(
                lambda c: c.evidence != "headers",
                {
                    "expected": frozenset(),
                    "any_of": (frozenset({"FUNC_REMOVED", "FUNC_REMOVED_ELF_ONLY"}),),
                },
                "without headers the surviving declaration is not observable: "
                "the evidence shows an exported function removed",
            ),
        ),
        **_BINARY,
    ),
    _op(
        "declared_function_export_became_c_namesake",
        "k.del",
        lambda r: _set(
            r,
            "k.del",
            None,
            XCXX_SEED.roles["k.del"][1].replace(
                "int kdl_gone(int a) { return a + 1; }",
                'extern "C" int kdl_gone_c(int a) __asm__("kdl_gone");\n'
                'extern "C" int kdl_gone_c(int a) { return a + 1; }',
            ),
        ),
        "the header still declares the C++ function kdl_gone(int), but the "
        "library now defines a C-linkage function under the bare symbol "
        "kdl_gone: _Z8kdl_gonei is gone from the dynamic table, so binaries "
        "that import it no longer load, and recompiled callers fail to link. "
        "The export table holds only a namesake of the declaration, never its "
        "own linker spelling",
        expected=frozenset({"FUNC_DELETED_ELF_FALLBACK"}),
        precondition=_elf,
        rules=(
            Rule(
                lambda c: c.evidence != "headers",
                {
                    "expected": frozenset(),
                    "any_of": (frozenset({"FUNC_REMOVED", "FUNC_REMOVED_ELF_ONLY"}),),
                },
                "without headers the surviving declaration is not observable: "
                "the evidence shows the mangled export removed",
            ),
        ),
        **_BINARY,
    ),
    _op(
        "deduced_lambda_return_named",
        "k.urt",
        _replace(
            "k.urt",
            (
                "inline auto kmake() { return [](int x) { return x + 7; }; }",
                "struct KFn { int operator()(int x) const { return x + 7; } };\n"
                "inline KFn kmake() { return KFn{}; }",
            ),
        ),
        "kmake returned a lambda, which callers could hold only in auto; it "
        "now returns the named function object KFn. auto still deduces it, "
        "so source keeps compiling, and kmake's non-template mangling does "
        "not encode its return type. The library's own weak copy of kmake "
        "does return a different type now, a return-type change of an "
        "exported function",
        expected=frozenset({"UNSPECIFIED_RETURN_NOW_NAMED", "FUNC_RETURN_CHANGED"}),
        features=("deduced_return",),
        verdict_min="COMPATIBLE",
        verdict_max=None,
        **_HEADERS,
    ),
    _op(
        "std_reexport_dropped",
        "k.rex",
        _replace("k.rex", (" using std::abs;", "")),
        "the header drops `using std::abs;` from namespace krx: nothing is "
        "exported for it and std::abs is unchanged, but consumer source that "
        "called krx::abs no longer compiles",
        expected=frozenset({"STD_REEXPORT_REMOVED"}),
        verdict_min="API_BREAK",
        verdict_max="API_BREAK",
        **_HEADERS,
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
    _op(
        "cpu_dispatch_variants_dropped",
        "k.isa",
        _replace(
            "k.isa",
            (
                "int ksum_avx2(int a);\nint kdot_avx2(int a);\nint kscl_avx2(int a);\n",
                "",
            ),
            source=(
                (
                    "int ksum_avx2(int a) { return a + 1; }\n"
                    "int kdot_avx2(int a) { return a + 2; }\n"
                    "int kscl_avx2(int a) { return a + 3; }\n",
                    "",
                ),
            ),
        ),
        "every _avx2 variant is removed while the _sse2 ones stay: binaries "
        "that bound the AVX2 entry points no longer load",
        expected=frozenset({"CPU_DISPATCH_ISA_DROPPED"}),
        **_BINARY,
    ),
    _op(
        "handle_typedef_retargeted",
        "k.hnd",
        _replace("k.hnd", ("typedef KHC* khandle;", "typedef void* khandle;")),
        "the opaque handle now names void*: the pointer is the same size, but "
        "code that used khandle as a KHC* no longer compiles, and the library "
        "may now hand out something that is not a KHC",
        expected=frozenset({"HANDLE_TYPE_CHANGED"}),
        verdict_min="API_BREAK",
        verdict_max=None,
        **_HEADERS,
    ),
    _op(
        "opaque_record_defined",
        "k.opq",
        _replace(
            "k.opq",
            ("struct KOC;", "struct KOC { int v; };"),
            source=(("struct KOC { int v; };\n", ""),),
        ),
        "the opaque record's definition moves into the header: nothing changes "
        "for existing binaries, but consumers can now allocate it and read v, "
        "so its layout becomes part of the contract",
        expected=frozenset({"OPAQUE_INVARIANT_BROKEN"}),
        verdict_min="COMPATIBLE",
        verdict_max=None,
        **_HEADERS,
    ),
    _op(
        "overload_retyped",
        "k.ovl",
        _replace(
            "k.ovl",
            ("int kput(int a);", "int kput(long a);"),
            source=(
                (
                    "int kput(int a) { return a; }",
                    "int kput(long a) { return (int)a; }",
                ),
            ),
        ),
        "kput(int) becomes kput(long) beside kput(double): the old symbol is "
        "gone, and recompiled calls with an int argument become ambiguous",
        expected=frozenset({"OVERLOAD_SET_REROUTED"}),
        **_BINARY,
    ),
    _op(
        "version_sentinel_typedef_bumped",
        "k.tvs",
        _replace("k.tvs", ("kfoo_version_1_2_3", "kfoo_version_1_2_4")),
        "the typedef that encodes the library version is renamed for the new "
        "version: nothing is exported for it, but code naming the old "
        "sentinel stops compiling, which is its purpose",
        expected=frozenset({"TYPEDEF_VERSION_SENTINEL"}),
        verdict_min="COMPATIBLE",
        verdict_max="API_BREAK",
        **_HEADERS,
    ),
    _op(
        "explicit_instantiation_dropped",
        "k.inst",
        lambda r: _set(
            r,
            "k.inst",
            None,
            XCXX_SEED.roles["k.inst"][1].replace(
                "\ntemplate double ktwice<double>(double);", ""
            ),
        ),
        "the library stops instantiating ktwice<double>: the header still "
        "declares the template, so callers compile and then fail to link or load",
        expected=frozenset({"INSTANTIATION_MISSING_FROM_BINARY"}),
        **_BINARY,
    ),
    _op(
        "struct_became_class",
        "k.slk",
        _replace("k.slk", ("struct KSK { int a; };", "class KSK { public: int a; };")),
        "struct becomes class with the same public members: Itanium mangles and "
        "lays out both the same (MSVC mangling distinguishes them)",
        expected=frozenset({"SOURCE_LEVEL_KIND_CHANGED"}),
        verdict_min="COMPATIBLE",
        verdict_max="COMPATIBLE_WITH_RISK",
        **_HEADERS,
    ),
    _op(
        "tag_type_renamed",
        "k.tag",
        _replace(
            "k.tag",
            ("struct ktag_a {};", "struct ktag_b {};"),
            source=(("kfam<ktag_a>", "kfam<ktag_b>"),),
        ),
        "the empty tag type used to select a template instantiation is "
        "renamed: kfam<ktag_a> re-mangles as kfam<ktag_b>",
        expected=frozenset({"TAG_TYPE_RENAMED"}),
    ),
    _op(
        "mandatory_template_param_added",
        "k.mtp",
        _replace(
            "k.mtp",
            ("template <class T> struct KMb", "template <class T, class A> struct KMb"),
            ("KMb<int>* p", "KMb<int, char>* p"),
            source=(("KMb<int>* p", "KMb<int, char>* p"),),
        ),
        "KMb gains a template parameter with no default: every use must name "
        "it, and kmb_use re-mangles",
        expected=frozenset({"MANDATORY_TEMPLATE_PARAM_ADDED"}),
    ),
    _op(
        "nonpod_field_added_in_tail_padding",
        "k.tpr",
        _replace("k.tpr", ("int i; char c; };", "int i; char c; char d; };")),
        "d fits KTP's tail padding, so sizeof stays 8, but KTP is not POD for "
        "layout: its data size grows from 5 to 6, and a class deriving from it "
        "that placed members in those bytes now overlaps d",
        expected=frozenset({"TAIL_PADDING_REUSE_CHANGED"}),
        # The data size is not in DWARF: no DIE is emitted for an unnamed
        # bit-field (which counts toward it), and DWARF cannot show a class is
        # POD for layout (a default member initializer leaves no trace, Clang
        # marks no `= default`). Only headers carry what decides it.
        min_evidence="headers",
        precondition=_no_pack,
        verdict_min="COMPATIBLE_WITH_RISK",
        verdict_max=None,
    ),
    _op(
        "polymorphic_type_added_without_virtual_dtor",
        "k.pnv",
        lambda r: _set(
            r,
            "k.pnv",
            "struct KPn { virtual int run(); };\nKPn* kpn_make();",
            "int KPn::run() { return 1; }\nKPn* kpn_make() { return new KPn(); }",
        ),
        "a new polymorphic class handed out by pointer has no virtual "
        "destructor: deleting it through a base pointer is undefined, a hazard "
        "the release introduces, though nothing existing changes",
        expected=frozenset({"POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR"}),
        **_RISK,
        **_HEADERS,
    ),
    _op(
        "stl_by_value_api_added",
        "k.stl",
        lambda r: _set(
            r,
            "k.stl",
            "#include <string>\nint kstl_len(std::string s);",
            "int kstl_len(std::string s) { return (int)s.size(); }",
        ),
        "a new function takes std::string by value: its ABI now depends on the "
        "standard library's string layout, which a consumer built against "
        "another library or dual-ABI setting does not share",
        expected=frozenset({"PUBLIC_API_EXPOSES_STL_BY_VALUE"}),
        **_RISK,
        **_HEADERS,
    ),
)
