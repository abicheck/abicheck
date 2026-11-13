"""Edit operators for ``types``-taxonomy kinds: records, templates, namespaces, idioms.

Ground truth is declared from the edit's meaning (the Itanium C++ ABI's
layout, vtable and mangling rules, C++ overload and template rules), never
from abicheck's answer; see ``_edit_corpus_operators`` for the ``EditOp``
fields. The operators edit roles of the ``c_elf`` (``elf.*``), ``cxx_more``
(``x.*``) and ``cxx_stdlib`` seeds.
"""

from __future__ import annotations

from typing import Any

from tests._edit_corpus_config import Config
from tests._edit_corpus_expect import Rule
from tests._edit_corpus_opkit import _REMOVED, Edit, EditOp, _set
from tests._edit_corpus_seeds import STDLIB_SEED, XCXX_SEED

__all__ = ["TYPE_OPERATORS"]


def _decl(role: str, header: str, source: str | None = None) -> Edit:
    return lambda r: _set(r, role, header, source)


def _replace(
    role: str, *pairs: tuple[str, str], source: tuple[str, str] = ("", "")
) -> Edit:
    """Re-spell fragments of *role*'s ``cxx_more`` header (and one in its source)."""
    header, src = XCXX_SEED.roles[role]
    for old, new in pairs:
        if old not in header:
            raise ValueError(f"{role}: {old!r} is not in its header text")
        header = header.replace(old, new, 1)
    if source[0]:
        if source[0] not in src:
            raise ValueError(f"{role}: {source[0]!r} is not in its source text")
        src = src.replace(source[0], source[1], 1)
    return _decl(role, header, src)


def _op(name: str, roles: tuple[str, ...], edit: Edit, why: str, **kw: Any) -> EditOp:
    kw.setdefault("touches", roles)
    return EditOp(name, roles, edit, why, **kw)


_COMPAT_ONLY: dict[str, Any] = {
    "verdict_min": "COMPATIBLE",
    "verdict_max": "COMPATIBLE",
}
_RISK: dict[str, Any] = {
    "verdict_min": "COMPATIBLE_WITH_RISK",
    "verdict_max": "COMPATIBLE_WITH_RISK",
}
_SOURCE_BREAK: dict[str, Any] = {"verdict_min": "API_BREAK", "verdict_max": "API_BREAK"}
_HEADERS = {"min_evidence": "headers"}
_BINARY = {"min_evidence": "binary"}


def _not_headers(c: Config) -> bool:
    return c.evidence != "headers"


def _removal_only_below_headers(why: str) -> Rule:
    # The finding reads declarations; below headers only the lost export shows.
    return Rule(_not_headers, {"expected": frozenset(), "any_of": (_REMOVED,)}, why)


def _cxx14(c: Config) -> str | None:
    return None if c.std.year >= 2014 else "a deduced return type needs C++14"


def _lp64(c: Config) -> str | None:
    return None if c.flags.bits == 64 else "long and long long differ in size on ILP32"


_RECORDS = (
    _op(
        "long_to_long_long",
        ("elf.lng",),
        _decl(
            "elf.lng",
            "long long lng_f(long long a);",
            "long long lng_f(long long a) { return a * 2; }",
        ),
        "long -> long long on LP64: same size, alignment and register class, "
        "so every call is unchanged; the spelling (and C++ overloading) differs",
        expected=frozenset({"ABI_EQUIVALENT_TYPE_CHANGED"}),
        precondition=_lp64,
        **_RISK,
    ),
    _op(
        "reserved_field_used",
        ("elf.res",),
        _decl(
            "elf.res",
            "struct res { int a; int flags; };\nint res_a(struct res *r);",
            "int res_a(struct res *r) { return r->a; }",
        ),
        "a reserved slot gets a name and a use: same type, same offset, no layout change",
        expected=frozenset({"USED_RESERVED_FIELD"}),
        **_COMPAT_ONLY,
    ),
    _op(
        "struct_spelled_class",
        ("x.kind",),
        _replace(
            "x.kind",
            (
                "struct Kd { int a; int get(); };",
                "class Kd { public: int a; int get(); };",
            ),
        ),
        "struct -> class with every member still public: no layout or Itanium "
        "mangling change; only the declared class-key differs (MSVC mangles it)",
        expected=frozenset({"SOURCE_LEVEL_KIND_CHANGED"}),
        verdict_min="NO_CHANGE",
        verdict_max="API_BREAK",
        **_HEADERS,
    ),
    _op(
        "standard_layout_lost",
        ("x.slay",),
        _replace(
            "x.slay",
            (
                "  int get() const;\nprivate:\n  int a;",
                "  int get() const;\n  int a;\nprivate:",
            ),
        ),
        "a made public, b still private: mixed access makes SL non-standard-layout "
        "(offsetof and C interop lose their guarantee); size and offsets are unchanged",
        expected=frozenset({"STANDARD_LAYOUT_LOST"}),
        **_RISK,
        **_HEADERS,
    ),
    _op(
        "record_became_pod_tail_padding",
        ("x.tp",),
        _replace(
            "x.tp",
            (
                "  int get() const;\nprivate:\n  int a;\n  char b;",
                "  int get() const;\n  int a;\n  char b;",
            ),
        ),
        "a and b made public: TP becomes an aggregate, POD for layout, so its 3 "
        "tail-padding bytes stop being reusable and a class deriving from TP "
        "lays out differently; TP's own size and offsets are unchanged",
        expected=frozenset({"TAIL_PADDING_REUSE_CHANGED"}),
        **_RISK,
        **_HEADERS,
    ),
    _op(
        "trivially_copyable_lost",
        ("x.tc",),
        _replace(
            "x.tc",
            ("struct TC { int a; };", "struct TC { int a; TC(const TC& o); };"),
            source=(
                "int tc_get(TC t) { return t.a; }",
                "TC::TC(const TC& o) : a(o.a) {}\nint tc_get(TC t) { return t.a; }",
            ),
        ),
        "a user-provided copy constructor: TC is passed by value through a hidden "
        "reference now, where old callers pass it in a register",
        expected=frozenset({"TRIVIALLY_COPYABLE_LOST"}),
        **_HEADERS,
    ),
)

_VTABLES = (
    _op(
        "secondary_base_became_polymorphic",
        ("x.sec",),
        _replace(
            "x.sec",
            ("struct SB { int b; };", "struct SB { int b; virtual ~SB(); };"),
            source=(
                "int SA::f() { return 1; }",
                "SB::~SB() {}\nint SA::f() { return 1; }",
            ),
        ),
        "SB gains a vptr: SD gets a secondary vtable group for SB, and SB's "
        "subobject (and b) move",
        expected=frozenset({"SECONDARY_VTABLE_GROUP_CHANGED"}),
    ),
    _op(
        "virtual_bases_swapped",
        ("x.vb2",),
        _replace("x.vb2", ("virtual VA1, virtual VA2", "virtual VA2, virtual VA1")),
        "the virtual bases swap: their offsets in VD2 and the vbase-offset "
        "entries old code reads from the vtable change",
        expected=frozenset({"VIRTUAL_BASE_OFFSET_CHANGED"}),
    ),
)

_TEMPLATES = (
    _op(
        "explicit_instantiation_dropped",
        ("x.inst",),
        _replace(
            "x.inst",
            ("extern template long getv<long>(long);\n", ""),
            source=("template long getv<long>(long);\n", ""),
        ),
        "getv<long> is no longer instantiated in the library: callers of the "
        "declared template that relied on the export fail to link or load",
        expected=frozenset({"INSTANTIATION_MISSING_FROM_BINARY"}),
        any_of=(_REMOVED,),
        rules=(
            _removal_only_below_headers("below headers only the lost export shows"),
        ),
        **_BINARY,
    ),
    _op(
        "template_param_added",
        ("x.inst",),
        _replace(
            "x.inst",
            (
                "template <class T> T conv(int a);\nextern template int conv<int>(int);",
                "template <class T, class U> T conv(int a);\n"
                "extern template int conv<int, int>(int);",
            ),
            source=(
                "template <class T> T conv(int a) { return (T)a; }\n"
                "template int conv<int>(int);\n#pragma",
                "template <class T, class U> T conv(int a) { return (T)a; }\n"
                "template int conv<int, int>(int);\n#pragma",
            ),
        ),
        "conv gains a second, mandatory template parameter: conv<int>(1) no "
        "longer compiles, and the conv<int> export is gone",
        expected=frozenset({"MANDATORY_TEMPLATE_PARAM_ADDED"}),
        any_of=(_REMOVED,),
        rules=(
            _removal_only_below_headers("below headers only the lost export shows"),
        ),
        **_BINARY,
    ),
    _op(
        "overload_rerouted",
        ("x.route",),
        _decl(
            "x.route",
            "int route(int a);\nint route(float a);",
            "int route(int a) { return a; }\nint route(float a) { return (int)a + 1; }",
        ),
        "route(double) becomes route(float): old binaries' calls to route(double) "
        "fail to bind, and rebuilt callers passing a double now pick another overload",
        expected=frozenset({"OVERLOAD_SET_REROUTED"}),
        any_of=(_REMOVED,),
        rules=(
            _removal_only_below_headers("below headers only the lost export shows"),
        ),
        **_BINARY,
    ),
    _op(
        "unnamed_type_export_added",
        ("x.extra",),
        _decl(
            "x.extra",
            "struct UCfg { enum { kMode = 1 }; };\ntemplate <class T> int tagu(T t);\n"
            "extern template int tagu(decltype(UCfg::kMode));",
            "#pragma GCC visibility push(default)\n"
            "template <class T> int tagu(T t) { return (int)t; }\n"
            "template int tagu(decltype(UCfg::kMode));\n#pragma GCC visibility pop",
        ),
        "a new export whose mangled name embeds an unnamed enum (Ut_): its name "
        "depends on declaration order in the header, so an unrelated edit there "
        "can re-mangle it later",
        expected=frozenset({"UNNAMED_TYPE_IN_PUBLIC_ABI"}),
        **_RISK,
        **_BINARY,
    ),
    _op(
        "tag_type_renamed",
        ("x.tag",),
        _decl(
            "x.tag",
            "namespace lib { struct quick_tag {}; template <class T> int run(); }",
            "#pragma GCC visibility push(default)\n"
            "template <class T> int lib::run() { return 1; }\n"
            "template int lib::run<lib::quick_tag>();\n#pragma GCC visibility pop",
        ),
        "the dispatch tag is renamed: run<fast_tag> no longer exists, in the "
        "header or the export table",
        expected=frozenset({"TAG_TYPE_RENAMED"}),
        any_of=(_REMOVED,),
        rules=(
            _removal_only_below_headers("below headers only the lost export shows"),
        ),
        **_BINARY,
    ),
    _op(
        "template_param_arg_changed",
        ("x.box",),
        _replace(
            "x.box",
            (
                'extern "C" int consume(Box<int>* b);',
                'extern "C" int consume(Box<long>* b);',
            ),
            source=(
                'extern "C" int consume(Box<int>* b)',
                'extern "C" int consume(Box<long>* b)',
            ),
        ),
        "Box<int>* -> Box<long>*: the unmangled C entry point now reads 8 bytes "
        "where old callers' Box holds 4",
        expected=frozenset({"TEMPLATE_PARAM_TYPE_CHANGED"}),
    ),
    _op(
        "template_return_arg_changed",
        ("x.box",),
        _replace(
            "x.box",
            ("Box<int> make_box(int a);", "Box<long> make_box(int a);"),
            source=(
                "Box<int> make_box(int a) { Box<int> b; b.v = a; return b; }",
                "Box<long> make_box(int a) { Box<long> b; b.v = a; return b; }",
            ),
        ),
        "Box<int> -> Box<long> return: the mangled name is unchanged, old callers "
        "read 4 bytes of an 8-byte result",
        expected=frozenset({"TEMPLATE_RETURN_TYPE_CHANGED"}),
    ),
)

_NAMESPACES = (
    _op(
        "unspecified_return_now_named",
        ("x.autoret",),
        _decl(
            "x.autoret",
            "typedef int (*cb_t)(int);\ncb_t make_cb();",
            "cb_t make_cb() { return [](int a) { return a + 1; }; }",
        ),
        "an inline factory returning a lambda (an unnamed closure type) now "
        "returns a named function-pointer type from the library: `auto f = "
        "make_cb(); f(1);` still compiles, code spelling the closure type does not",
        expected=frozenset({"UNSPECIFIED_RETURN_NOW_NAMED"}),
        precondition=_cxx14,
        **_SOURCE_BREAK,
        **_HEADERS,
    ),
    _op(
        "function_became_cpo",
        ("x.cpo",),
        _decl(
            "x.cpo",
            "namespace lib { struct sort_fn { int operator()(int a) const; }; "
            "extern const sort_fn sort; }",
            "int lib::sort_fn::operator()(int a) const { return a + 1; }\n"
            "const lib::sort_fn lib::sort{};",
        ),
        "lib::sort becomes a function object: lib::sort(1) still compiles, but "
        "the old lib::sort(int) export is gone and &lib::sort changes type",
        expected=frozenset({"CPO_KIND_CHANGED"}),
        any_of=(_REMOVED,),
        rules=(
            _removal_only_below_headers("below headers only the lost export shows"),
        ),
        **_BINARY,
    ),
    _op(
        "experimental_graduated",
        ("x.exp",),
        _decl(
            "x.exp",
            "namespace lib { namespace experimental { "
            "inline int frob(int a) { return a + 1; } }\nint frob(int a); }",
            "int lib::frob(int a) { return a + 1; }",
        ),
        "a stable lib::frob joins the experimental one: nothing is removed",
        expected=frozenset({"EXPERIMENTAL_GRADUATED"}),
        **_COMPAT_ONLY,
        **_HEADERS,
    ),
    _op(
        "experimental_removed",
        ("x.exp",),
        _decl("x.exp", "", ""),
        "the header-only experimental frob is gone with no stable twin: callers "
        "no longer compile; it exported nothing, so binaries are unaffected",
        expected=frozenset({"EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT"}),
        **_SOURCE_BREAK,
        **_HEADERS,
    ),
)


def _x86(c: Config) -> str | None:
    return None if c.flags.bits == 64 else "the ISA-suffixed kernels name x86-64 ISAs"


_IDIOMS = (
    _op(
        "pimpl_member_renamed_under_inline_accessor",
        ("x.pimpl",),
        _replace(
            "x.pimpl",
            (
                "struct impl { int count_; int cap_; };",
                "struct impl { int cap_; int n_; };",
            ),
            ("return p_->count_;", "return p_->n_;"),
            source=("new detail::impl{3, 8}", "new detail::impl{8, 3}"),
        ),
        "the pimpl's count_ becomes n_ at a new offset: Desc::count() was inlined "
        "into old callers, which still read offset 0 (now cap_)",
        expected=frozenset({"INLINE_BODY_REFERENCES_RENAMED_MEMBER"}),
        **_HEADERS,
    ),
    _op(
        "cpu_dispatch_isa_dropped",
        ("elf.isa",),
        _decl(
            "elf.isa",
            "int k1(int a);\nint k2(int a);\nint k3(int a);\n"
            "int k1_sse42(int a);\nint k2_sse42(int a);\nint k3_sse42(int a);",
            "int k1_sse42(int a) { return a + 1; }\nint k2_sse42(int a) { return a + 2; }\n"
            "int k3_sse42(int a) { return a + 3; }\n"
            "int k1(int a) { return k1_sse42(a); }\nint k2(int a) { return k2_sse42(a); }\n"
            "int k3(int a) { return k3_sse42(a); }",
        ),
        "the AVX2 tier is dropped; the dispatchers and the SSE4.2 tier stay: "
        "callers through k1..k3 are unaffected, but the k*_avx2 kernels were "
        "declared and exported, so a caller pinned to one fails to load",
        expected=frozenset({"CPU_DISPATCH_ISA_DROPPED"}),
        verdict_min="BREAKING",
        precondition=_x86,
        **_BINARY,
    ),
    _op(
        "version_stamp_typedef_bumped",
        ("elf.vstamp",),
        _decl(
            "elf.vstamp",
            "typedef const char *mylib_version_1_2_4;\nmylib_version_1_2_4 mylib_vstamp(void);",
            'mylib_version_1_2_4 mylib_vstamp(void) { return "1.2.4"; }',
        ),
        "a version-stamp typedef renamed for the release, the way such stamps "
        "are meant to be: consumers check it at build time, never name it in code",
        expected=frozenset({"TYPEDEF_VERSION_SENTINEL"}),
        verdict_min="COMPATIBLE",
        verdict_max="API_BREAK",
        **_HEADERS,
    ),
    _op(
        "handle_retargeted",
        ("elf.hdl",),
        _decl(
            "elf.hdl",
            "struct ctx2_s;\ntypedef struct ctx2_s *ctx_t;\nctx_t ctx_open(void);\n"
            "int ctx_close(ctx_t c);",
            "struct ctx2_s { int n; };\nstatic struct ctx2_s s_ctx;\n"
            "ctx_t ctx_open(void) { return &s_ctx; }\nint ctx_close(ctx_t c) { return c->n; }",
        ),
        "the opaque handle now points to another incomplete type: the pointer "
        "is passed the same way, but `struct ctx_s *p = ctx_open();` no longer compiles",
        expected=frozenset({"HANDLE_TYPE_CHANGED"}),
        verdict_min="API_BREAK",
        **_HEADERS,
    ),
    _op(
        "opaque_type_exposed",
        ("elf.opq",),
        _decl(
            "elf.opq",
            "struct opq { int a; };\nstruct opq *opq_new(void);\nint opq_val(struct opq *o);",
            "static struct opq s_opq;\n"
            "struct opq *opq_new(void) { return &s_opq; }\nint opq_val(struct opq *o) { return o->a; }",
        ),
        "the opaque struct's definition becomes public: nothing built breaks, but "
        "consumers can now sizeof or embed it, so its layout has joined the ABI",
        expected=frozenset({"OPAQUE_INVARIANT_BROKEN"}),
        verdict_min="COMPATIBLE_WITH_RISK",
        **_HEADERS,
    ),
    _op(
        "polymorphic_class_without_virtual_dtor_added",
        ("x.extra",),
        _decl(
            "x.extra",
            "struct NP { virtual int g(); };\nNP* make_np();",
            "int NP::g() { return 1; }\nNP* make_np() { static NP n; return &n; }",
        ),
        "a new polymorphic class returned by pointer without a virtual "
        "destructor: deleting a derived object through NP* is undefined; "
        "nothing existing changes",
        expected=frozenset({"POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR"}),
        **_RISK,
        **_HEADERS,
    ),
    _op(
        "stl_by_value_function_added",
        ("func.greet",),
        _decl(
            "func.greet",
            STDLIB_SEED.roles["func.greet"][0] + "\nstd::string greet_n(int n);",
            STDLIB_SEED.roles["func.greet"][1]
            + "\nstd::string greet_n(int n) { return std::string(n, 'x'); }",
        ),
        "a new function returns std::string by value: an addition, but one "
        "that ties the library's ABI to the standard library's string layout",
        expected=frozenset({"PUBLIC_API_EXPOSES_STL_BY_VALUE"}),
        **_RISK,
        **_HEADERS,
    ),
)

TYPE_OPERATORS: tuple[EditOp, ...] = (
    *_RECORDS,
    *_VTABLES,
    *_TEMPLATES,
    *_NAMESPACES,
    *_IDIOMS,
)
