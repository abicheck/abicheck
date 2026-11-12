"""Edit operators for ``symbols``-taxonomy kinds: functions, variables, parameters.

Ground truth is declared from the edit's meaning (C/C++ conversion and
overload rules, the ELF loader, the platform calling convention), never from
abicheck's answer; see ``_edit_corpus_operators`` for the ``EditOp`` fields.
The operators edit roles of the ``c_elf`` (``elf.*``) and ``cxx_more``
(``x.*``) seeds.
"""

from __future__ import annotations

from typing import Any

from tests._edit_corpus_config import Config
from tests._edit_corpus_expect import Rule
from tests._edit_corpus_opkit import _REMOVED, Edit, EditOp, _set

__all__ = ["SYMBOL_OPERATORS"]


def _decl(role: str, header: str, source: str | None = None) -> Edit:
    return lambda r: _set(r, role, header, source)


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

_POINTEE_QUALIFIERS = (
    _op(
        "return_pointee_const_removed",
        ("elf.cref",),
        _decl(
            "elf.cref",
            "int *cref(void);",
            "static int s_cref = 8;\nint *cref(void) { return &s_cref; }",
        ),
        "const int * -> int *: every direct call still compiles (int * converts "
        "to const int *), but the function's type changed, so a consumer "
        "holding it as `const int *(*)(void)` no longer compiles: a risk",
        expected=frozenset({"FUNC_RETURN_POINTEE_QUALIFIER_REMOVED"}),
        **_RISK,
        **_HEADERS,
    ),
    _op(
        "return_deep_pointee_const_removed",
        ("elf.cpp",),
        _decl(
            "elf.cpp",
            "int **cpp(void);",
            "static int s_cpp = 9;\nstatic int *s_cppp = &s_cpp;\n"
            "int **cpp(void) { return &s_cppp; }",
        ),
        "const int ** -> int **: int ** does not convert to const int ** "
        "(only const at every level above would), so `const int **p = cpp();` "
        "no longer compiles; the return register is unchanged",
        expected=frozenset({"FUNC_RETURN_POINTEE_QUALIFIER_CHANGED"}),
        **_SOURCE_BREAK,
        **_HEADERS,
    ),
    _op(
        "param_pointee_const_to_volatile",
        ("elf.cread",),
        _decl(
            "elf.cread",
            "int cread(volatile int *p);",
            "int cread(volatile int *p) { return *p + 2; }",
        ),
        "const int * -> volatile int *: a caller passing a const int * drops "
        "const and no longer compiles; the call is unchanged",
        expected=frozenset({"PARAM_POINTEE_QUALIFIER_CHANGED"}),
        **_SOURCE_BREAK,
        **_HEADERS,
    ),
    _op(
        "var_alignment_raised",
        ("elf.al",),
        _decl(
            "elf.al",
            "extern int g_al __attribute__((aligned(64)));",
            "int g_al __attribute__((aligned(64))) = 5;",
        ),
        "aligned(16) -> aligned(64): a consumer's copy-relocated g_al keeps the "
        "old alignment while the library may now assume 64",
        expected=frozenset({"VAR_ALIGNMENT_CHANGED"}),
    ),
)

_EXPORTS = (
    _op(
        "source_only_var_export_added",
        ("elf.extra",),
        _decl("elf.extra", "", "int g_source_only = 21;"),
        "a variable defined only in the source becomes a new export no header "
        "declares: nothing existing changes, but against the headers' public "
        "contract it is an accidental export consumers can start binding to "
        "(an ABI-hygiene risk, ADR-035)",
        expected=frozenset({"VAR_ADDED_ELF_ONLY"}),
        rules=(
            Rule(
                lambda c: c.flags.visibility == "hidden",
                {
                    "expected": frozenset(),
                    "verdict_min": "NO_CHANGE",
                    "observable": False,
                },
                "under -fvisibility=hidden only declarations inside the header's "
                "visibility push(default) are exported; the source-only variable "
                "stays local, so the export table does not change",
            ),
            Rule(
                lambda c: c.evidence in ("binary", "dwarf") or c.objfmt != "elf",
                {"expected": frozenset({"VAR_ADDED"}), "verdict_max": "COMPATIBLE"},
                "without a header to miss it from, or off ELF, the export is an "
                "ordinary addition",
            ),
        ),
        verdict_min="COMPATIBLE",
        verdict_max="COMPATIBLE_WITH_RISK",
        min_evidence="binary",
    ),
)


def _x86_64_gnu(c: Config) -> str | None:
    if c.producer.family not in ("gcc", "clang") or c.objfmt != "elf":
        return "ms_abi is a GNU attribute; the edit targets x86-64 Linux"
    if c.flags.bits != 64 or c.platform[0] != "linux":
        return "ms_abi selects a different convention only on x86-64"
    return None


def _cxx_before_17(c: Config) -> str | None:
    if c.std.lang == "c++" and c.std.year < 2017:
        return None
    return "dynamic exception specifications were removed in C++17"


_CONVENTIONS = (
    _op(
        "func_became_ms_abi",
        ("elf.ms",),
        _decl(
            "elf.ms",
            "__attribute__((ms_abi)) int msf(int a, int b);",
            "__attribute__((ms_abi)) int msf(int a, int b) { return a * b + 1; }",
        ),
        "SysV -> Microsoft x64 convention: the callee now reads a and b from "
        "rcx/rdx, where an old caller left them in rdi/rsi",
        expected=frozenset({"CALLING_CONVENTION_CHANGED"}),
        precondition=_x86_64_gnu,
        **_HEADERS,
    ),
    _op(
        "func_contract_attribute_added",
        ("elf.attr",),
        _decl(
            "elf.attr",
            "int put(char *p) __attribute__((nonnull(1)));\n"
            "char *dupc(char *s) __attribute__((returns_nonnull));",
        ),
        "nonnull(1) promises callers never pass NULL: the call is unchanged, "
        "but the library may now drop its NULL check, so a caller that "
        "passes NULL has undefined behaviour",
        expected=frozenset({"FUNC_CONTRACT_ATTRIBUTE_ADDED"}),
        **_RISK,
        **_HEADERS,
    ),
    _op(
        "func_contract_attribute_removed",
        ("elf.attr",),
        _decl(
            "elf.attr",
            "int put(char *p);\nchar *dupc(char *s);",
        ),
        "returns_nonnull withdrawn: callers compiled with the promise may "
        "have dropped their own NULL checks on the result",
        expected=frozenset({"FUNC_CONTRACT_ATTRIBUTE_REMOVED"}),
        **_RISK,
        **_HEADERS,
    ),
    _op(
        "func_exception_spec_widened",
        ("x.throws",),
        _decl(
            "x.throws",
            "#if __cplusplus >= 201703L\n#define EDIT_THROWS(...)\n#else\n"
            "#define EDIT_THROWS(...) throw(__VA_ARGS__)\n#endif\n"
            "int thrower(int a) EDIT_THROWS(int, long);",
            "int thrower(int a) EDIT_THROWS(int, long) { if (a < 0) throw 1; return a; }",
        ),
        "throw(int) -> throw(int, long): the mangled name and call are "
        "unchanged, but the promised exception set grew",
        expected=frozenset({"FUNC_EXCEPTION_SPEC_CHANGED"}),
        precondition=_cxx_before_17,
        **_RISK,
        **_HEADERS,
    ),
)

_LATE_DEF = "int late_f(int x) { return x + 4; }\nint g_late = 2;"

_EXPORT_GAINED = (
    _op(
        "declared_exports_defined",
        ("elf.late",),
        _decl("elf.late", "int late_f(int x);\nextern int g_late;", _LATE_DEF),
        "a function and a variable the header already declared gain a "
        "definition: an export appears; nothing existing changes",
        expected=frozenset({"FUNC_EXPORT_ADDED", "VAR_EXPORT_ADDED"}),
        **_COMPAT_ONLY,
        **_HEADERS,
    ),
    _op(
        "inline_function_outlined",
        ("x.inl",),
        _decl(
            "x.inl",
            "int inl_add(int a, int b);",
            "int inl_add(int a, int b) { return a + b; }",
        ),
        "an inline function moves out of line: code built against the inline "
        "body keeps its copy, new callers call the new export",
        expected=frozenset({"FUNC_LOST_INLINE"}),
        **_COMPAT_ONLY,
        **_HEADERS,
    ),
)

_VA_LIST = (
    _op(
        "param_became_va_list",
        ("elf.va",),
        _decl(
            "elf.va",
            "int vlog(const char *fmt, va_list ap);\nint vlog2(const char *fmt, va_list ap);",
            "int vlog(const char *fmt, va_list ap) { return fmt[0] + va_arg(ap, int); }\n"
            "int vlog2(const char *fmt, va_list ap) { return fmt[0] + va_arg(ap, int); }",
        ),
        "void * -> va_list: on x86-64 a va_list parameter is a pointer to the "
        "caller's __va_list_tag, still passed in one register, but the callee "
        "now reads a va_list through it",
        expected=frozenset({"PARAM_BECAME_VA_LIST"}),
        **_HEADERS,
    ),
    _op(
        "param_lost_va_list",
        ("elf.va",),
        _decl(
            "elf.va",
            "int vlog(const char *fmt, void *ap);\nint vlog2(const char *fmt, void *ap);",
            "int vlog(const char *fmt, void *ap) { return fmt[0] + (ap != 0); }\n"
            "int vlog2(const char *fmt, void *ap) { return fmt[0] + (ap != 0); }",
        ),
        "va_list -> void *: the register still holds a pointer, but the callee "
        "no longer reads the caller's va_list through it",
        expected=frozenset({"PARAM_LOST_VA_LIST"}),
        **_HEADERS,
    ),
)

_VALUES_AND_NAMES = (
    _op(
        "serialization_tag_swapped",
        ("elf.ser",),
        _decl(
            "elf.ser",
            "enum widget_tag_id { WIDGET_A = 2, WIDGET_B = 1 };\n"
            "int widget_tag(enum widget_tag_id t);",
        ),
        "the tag ids swap: data serialized by old code with WIDGET_A = 1 "
        "now decodes as WIDGET_B, and old callers pass the old values",
        expected=frozenset({"SERIALIZATION_TAG_CHANGED", "ENUM_MEMBER_VALUE_CHANGED"}),
        **_HEADERS,
    ),
    _op(
        "symbols_prefixed_batch",
        ("elf.ren",),
        _decl(
            "elf.ren",
            "int mylib_r_init(void);\nint mylib_r_run(int n);",
            "int mylib_r_init(void) { return 1; }\nint mylib_r_run(int n) { return n + 1; }",
        ),
        "every function gains a mylib_ prefix: each old name an existing "
        "binary imports is gone",
        expected=frozenset({"SYMBOL_RENAMED_BATCH"}),
        # Each old name is gone; from symbols alone a tool may pair it with
        # its prefixed twin instead of reporting a bare removal.
        any_of=(_REMOVED | {"FUNC_LIKELY_RENAMED"},),
        min_evidence="binary",
    ),
    _op(
        "internal_table_grown",
        ("elf.itab",),
        _decl(
            "elf.itab",
            "extern int _lib_table[8];",
            "int _lib_table[8] = {1, 2, 3, 4};",
        ),
        "a reserved-name exported array doubles: a consumer's copy relocation "
        "reserves the old 16 bytes, and the library now writes 32 there",
        expected=frozenset({"SYMBOL_SIZE_CHANGED_INTERNAL"}),
        min_evidence="binary",
    ),
)

SYMBOL_OPERATORS: tuple[EditOp, ...] = (
    *_POINTEE_QUALIFIERS,
    *_EXPORTS,
    *_CONVENTIONS,
    *_EXPORT_GAINED,
    *_VA_LIST,
    *_VALUES_AND_NAMES,
)
