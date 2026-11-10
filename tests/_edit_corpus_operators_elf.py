"""Edit operators for the ``c_elf`` seed: the library as an ELF object, and C types.

Ground truth is declared from the edit's meaning (the ELF loader's rules for
version nodes, SONAMEs, bindings, copy relocations and TLS; C layout rules),
never from abicheck's answer; see ``_edit_corpus_operators`` for the
``EditOp`` fields. Many edits here change one *build role* (a version script,
the SONAME, a link or compile flag) rather than a declaration.

Evidence floors: a dynamic-section or symbol-table fact is visible from the
binary alone; a type layout needs DWARF; a qualifier, attribute, deprecation,
header constant or a name with no layout effect needs headers (abicheck's
evidence model: DWARF carries no source-only API intent).
"""

from __future__ import annotations

from typing import Any

from tests._edit_corpus_expect import Rule
from tests._edit_corpus_opkit import _REMOVED, Edit, EditOp, Roles, _merge, _set
from tests._edit_corpus_seeds import RoleText

__all__ = ["ELF_OPERATORS"]

_LDFLAGS = "-Wl,--no-as-needed -lm -lgcc_s -Wl,--as-needed -Wl,-z,now"
_V10 = "LIBEDIT_1.0 { global: *; };"


def _build(r: Roles, role: str, value: str) -> dict[str, RoleText]:
    return _set(r, role, value, "")


def _ld(extra: str = "", base: str = _LDFLAGS) -> Edit:
    return lambda r: _build(r, "build.ldflags", f"{base} {extra}".strip())


def _cflags(value: str) -> Edit:
    return lambda r: _build(r, "build.cflags", value)


def _vscript(value: str) -> Edit:
    return lambda r: _build(r, "build.vscript", value)


def _decl(role: str, header: str, source: str | None = None) -> Edit:
    return lambda r: _set(r, role, header, source)


def _op(name: str, roles: tuple[str, ...], edit: Edit, why: str, **kw: Any) -> EditOp:
    kw.setdefault("touches", roles)
    return EditOp(name, roles, edit, why, **kw)


# Verdict bands, by what the edit does to consumers.
_COMPAT_ONLY: dict[str, Any] = {
    "verdict_min": "COMPATIBLE",
    "verdict_max": "COMPATIBLE",
}
_AT_MOST_RISK: dict[str, Any] = {
    "verdict_min": "COMPATIBLE",
    "verdict_max": "COMPATIBLE_WITH_RISK",
}
_RISK: dict[str, Any] = {
    "verdict_min": "COMPATIBLE_WITH_RISK",
    "verdict_max": "COMPATIBLE_WITH_RISK",
}
_SOURCE_BREAK: dict[str, Any] = {"verdict_min": "API_BREAK", "verdict_max": "API_BREAK"}
_BINARY = {"min_evidence": "binary"}
_HEADERS = {"min_evidence": "headers"}


# --- symbol versions -----------------------------------------------------------

_VERSIONING = (
    _op(
        "version_node_added_for_new_symbol",
        ("build.vscript", "elf.extra"),
        lambda r: _merge(
            _build(
                r,
                "build.vscript",
                _V10 + "\nLIBEDIT_1.1 { global: vnew; } LIBEDIT_1.0;",
            ),
            _set(
                r, "elf.extra", "int vnew(int x);", "int vnew(int x) { return x + 2; }"
            ),
        ),
        "a new symbol in a new version node: every old reference still binds",
        expected=frozenset({"FUNC_ADDED", "SYMBOL_VERSION_DEFINED_ADDED"}),
        **_COMPAT_ONLY,
        **_BINARY,
    ),
    _op(
        "version_script_dropped",
        ("build.vscript",),
        _vscript(""),
        "consumers require version LIBEDIT_1.0, which an unversioned library "
        "no longer defines: they fail to load",
        expected=frozenset({"SYMBOL_VERSION_NODE_REMOVED", "VERSION_SCRIPT_MISSING"}),
        **_BINARY,
    ),
    _op(
        "version_node_renamed",
        ("build.vscript",),
        _vscript("LIBEDIT_2.0 { global: *; };"),
        "every symbol leaves LIBEDIT_1.0, which consumers require",
        expected=frozenset({"SYMBOL_VERSION_NODE_REMOVED"}),
        **_BINARY,
    ),
    _op(
        "symbol_moved_version_node",
        ("build.vscript",),
        _vscript(_V10 + "\nLIBEDIT_1.1 { global: wget; } LIBEDIT_1.0;"),
        "wget@LIBEDIT_1.0 is no longer defined, only wget@@LIBEDIT_1.1: old "
        "consumers fail with 'undefined symbol: wget, version LIBEDIT_1.0'",
        expected=frozenset({"SYMBOL_MOVED_VERSION_NODE"}),
        **_BINARY,
    ),
    _op(
        "symbol_moved_version_node_keeping_alias",
        ("build.vscript", "elf.wget"),
        lambda r: _merge(
            _build(
                r,
                "build.vscript",
                "LIBEDIT_1.0 { global: *; local: wget_v1; wget_v2; };\n"
                "LIBEDIT_1.1 { global: wget; } LIBEDIT_1.0;",
            ),
            _set(
                r,
                "elf.wget",
                None,
                "int wget_v1(int x) { return x * 3 + 1; }\n"
                "int wget_v2(int x) { return x * 3 + 1; }\n"
                '__asm__(".symver wget_v1, wget@LIBEDIT_1.0");\n'
                '__asm__(".symver wget_v2, wget@@LIBEDIT_1.1");',
            ),
        ),
        "the default becomes wget@@LIBEDIT_1.1 and wget@LIBEDIT_1.0 is kept: "
        "old references still bind",
        expected=frozenset({"SYMBOL_VERSION_ALIAS_CHANGED"}),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
)

# --- SONAME, NEEDED, search paths, hardening ------------------------------------

_DYNAMIC = (
    _op(
        "soname_changed",
        ("build.soname",),
        lambda r: _build(r, "build.soname", "libedit.so.2"),
        "a new SONAME declares a new ABI: consumers needing libedit.so do not load it",
        expected=frozenset({"SONAME_CHANGED"}),
        verdict_min="COMPATIBLE_WITH_RISK",
        **_BINARY,
    ),
    _op(
        "soname_dropped",
        ("build.soname",),
        lambda r: _build(r, "build.soname", ""),
        "without DT_SONAME a consumer records the file name it linked, so a "
        "versioned install no longer satisfies it",
        expected=frozenset({"SONAME_CHANGED"}),
        verdict_min="COMPATIBLE_WITH_RISK",
        **_BINARY,
    ),
    _op(
        "needed_added",
        ("build.ldflags",),
        _ld("-Wl,--no-as-needed -lstdc++ -Wl,--as-needed"),
        "a new DT_NEEDED entry is a new load-time dependency, not an interface change",
        expected=frozenset({"NEEDED_ADDED"}),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
    _op(
        "needed_removed",
        ("build.ldflags",),
        _ld(base="-Wl,--no-as-needed -lm -Wl,--as-needed -Wl,-z,now"),
        "dropping DT_NEEDED libgcc_s removes a dependency consumers may reach transitively",
        expected=frozenset({"NEEDED_REMOVED"}),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
    _op(
        "needed_reordered",
        ("build.ldflags",),
        _ld(base="-Wl,--no-as-needed -lgcc_s -lm -Wl,--as-needed -Wl,-z,now"),
        "DT_NEEDED order decides interposition between dependencies",
        expected=frozenset({"NEEDED_ORDER_CHANGED"}),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
    _op(
        "rpath_added",
        ("build.ldflags",),
        _ld("-Wl,-rpath,/opt/edit/lib -Wl,--disable-new-dtags"),
        "DT_RPATH changes where dependencies are searched, ahead of LD_LIBRARY_PATH",
        expected=frozenset({"RPATH_CHANGED"}),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
    _op(
        "runpath_added",
        ("build.ldflags",),
        _ld("-Wl,-rpath,/opt/edit/lib -Wl,--enable-new-dtags"),
        "DT_RUNPATH changes where dependencies are searched",
        expected=frozenset({"RUNPATH_CHANGED"}),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
    _op(
        "executable_stack",
        ("build.ldflags",),
        _ld("-Wl,-z,execstack"),
        "PT_GNU_STACK becomes executable: a hardening loss, not an interface change",
        expected=frozenset({"EXECUTABLE_STACK"}),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
    _op(
        "relro_disabled",
        ("build.ldflags",),
        _ld("-Wl,-z,norelro"),
        "without PT_GNU_RELRO the GOT stays writable",
        expected=frozenset({"RELRO_WEAKENED"}),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
    _op(
        "bind_now_dropped",
        ("build.ldflags",),
        _ld(base="-Wl,--no-as-needed -lm -lgcc_s -Wl,--as-needed"),
        "lazy binding: full RELRO falls back to partial",
        any_of=(frozenset({"RELRO_WEAKENED", "BIND_NOW_DISABLED"}),),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
    _op(
        "symbolic_binding",
        ("build.ldflags",),
        _ld("-Wl,-Bsymbolic"),
        "-Bsymbolic binds the library's own references locally: an exported "
        "object a consumer copy-relocates is no longer the one the library uses",
        expected=frozenset({"SYMBOLIC_BINDING_MODE_CHANGED"}),
        verdict_min="COMPATIBLE_WITH_RISK",
        **_BINARY,
    ),
    _op(
        "gnu_hash_dropped",
        ("build.ldflags",),
        _ld("-Wl,--hash-style=sysv"),
        "without DT_GNU_HASH a loader that reads only it cannot look symbols up",
        expected=frozenset({"HASH_STYLE_REMOVED"}),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
    _op(
        "stack_protector_dropped",
        ("build.cflags",),
        _cflags("-fno-stack-protector"),
        "the stack canary goes: a hardening loss, not an interface change",
        expected=frozenset({"STACK_CANARY_REMOVED"}),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
    _op(
        "tls_initial_exec",
        ("build.cflags",),
        _cflags("-fstack-protector-strong -ftls-model=initial-exec"),
        "initial-exec TLS sets DF_STATIC_TLS: dlopen of the library can fail",
        expected=frozenset({"STATIC_TLS_INTRODUCED"}),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
    _op(
        "char_made_unsigned",
        ("build.cflags",),
        _cflags("-fstack-protector-strong -funsigned-char"),
        "plain char becomes unsigned: a char value above 127 the library reads from "
        "a caller built signed changes meaning (x86-64 and i386 default to signed)",
        expected=frozenset({"CHAR_SIGNEDNESS_CHANGED"}),
        rules=(
            Rule(
                lambda c: c.producer.family == "clang",
                {
                    "expected": frozenset(),
                    "verdict_min": "NO_CHANGE",
                    "observable": False,
                },
                "clang's DW_AT_producer records no -f switches; nothing else carries "
                "char signedness (known gap, canary in test_dwarf_lane_extractor_parity)",
            ),
        ),
        **_RISK,
        min_evidence="dwarf",
    ),
)

# --- binding, visibility, exported data -----------------------------------------

_SYMBOLS = (
    _op(
        "func_became_weak",
        ("elf.vget",),
        _decl(
            "elf.vget",
            "int vget(int x);",
            "__attribute__((weak)) int vget(int x) { return x * 2; }",
        ),
        "a weak definition in a DSO resolves the same for consumers",
        expected=frozenset({"SYMBOL_BINDING_CHANGED"}),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
    _op(
        "weak_became_global",
        ("elf.weak",),
        _decl("elf.weak", "int soft(int x);", "int soft(int x) { return x + 5; }"),
        "a weak definition becomes strong",
        expected=frozenset({"SYMBOL_BINDING_STRENGTHENED"}),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
    _op(
        "func_became_protected",
        ("elf.vget",),
        _decl("elf.vget", 'int vget(int x) __attribute__((visibility("protected")));'),
        "a protected function can no longer be interposed",
        expected=frozenset({"FUNC_VISIBILITY_PROTECTED_CHANGED"}),
        **_RISK,
        **_BINARY,
    ),
    _op(
        "object_became_protected",
        ("elf.obj",),
        _decl("elf.obj", 'extern int g_obj __attribute__((visibility("protected")));'),
        "a protected object a consumer copy-relocates: the library keeps its own copy",
        expected=frozenset({"PROTECTED_VISIBILITY_CHANGED"}),
        verdict_min="COMPATIBLE_WITH_RISK",
        **_BINARY,
    ),
    _op(
        "object_hidden",
        ("elf.obj",),
        _decl("elf.obj", 'extern int g_obj __attribute__((visibility("hidden")));'),
        "a hidden object leaves the dynamic symbol table: consumers fail to load",
        any_of=(
            frozenset(
                {"VAR_REMOVED", "VAR_REMOVED_ELF_ONLY", "VAR_VISIBILITY_CHANGED"}
            ),
        ),
        **_BINARY,
    ),
    _op(
        "object_added",
        ("elf.extra",),
        _decl("elf.extra", "extern int g_new;", "int g_new = 1;"),
        "a new exported object is compatible",
        expected=frozenset({"VAR_ADDED"}),
        **_COMPAT_ONLY,
        **_BINARY,
    ),
    _op(
        "object_removed",
        ("elf.k",),
        _decl("elf.k", "", ""),
        "consumers referencing g_k fail to load",
        any_of=(frozenset({"VAR_REMOVED", "VAR_REMOVED_ELF_ONLY"}),),
        **_BINARY,
    ),
    _op(
        "object_became_const",
        ("elf.obj",),
        _decl("elf.obj", "extern const int g_obj;", "const int g_obj = 11;"),
        "writes to a now-const object no longer compile",
        expected=frozenset({"VAR_BECAME_CONST"}),
        verdict_min="API_BREAK",
    ),
    _op(
        "object_lost_const",
        ("elf.k",),
        _decl("elf.k", "extern int g_k;", "int g_k = 3;"),
        "the object becomes writable: consumers that folded the constant diverge",
        expected=frozenset({"VAR_LOST_CONST"}),
        verdict_min="COMPATIBLE_WITH_RISK",
    ),
    _op(
        "const_object_value_changed",
        ("elf.k",),
        _decl("elf.k", "extern const int g_k;", "const int g_k = 4;"),
        "the value lives in the library; consumers read it at run time",
        expected=frozenset({"VAR_VALUE_CHANGED"}),
        **_AT_MOST_RISK,
    ),
    _op(
        "object_array_grew",
        ("elf.tab",),
        _decl(
            "elf.tab",
            "extern const int g_tab[8];",
            "const int g_tab[8] = {1, 2, 3, 4};",
        ),
        "a copy-relocated object's size is fixed in every consumer",
        expected=frozenset({"SYMBOL_SIZE_CHANGED"}),
        **_BINARY,
    ),
    _op(
        "const_string_object_grew",
        ("elf.banner",),
        _decl(
            "elf.banner",
            "extern const char banner[];",
            'const char banner[] = "edit-longer";',
        ),
        "an unbounded const array grew: consumers that copy-relocated it hold the old size",
        any_of=(
            frozenset({"SYMBOL_SIZE_CHANGED_CONST_OBJECT", "SYMBOL_SIZE_CHANGED"}),
        ),
        verdict_min="COMPATIBLE_WITH_RISK",
        rules=(
            Rule(
                lambda c: c.evidence == "headers",
                {
                    "expected": frozenset({"SYMBOL_SIZE_CHANGED_CONST_OBJECT"}),
                    "any_of": (),
                },
                "the header shows banner is an unbounded const char[]",
            ),
        ),
        **_BINARY,
    ),
    _op(
        "tls_object_grew",
        ("elf.tls",),
        _decl(
            "elf.tls",
            "extern __thread long long t_slot;\nint tls_get(void);",
            "__thread long long t_slot = 9;\nint tls_get(void) { return (int)t_slot; }",
        ),
        "a TLS object's size is baked into consumers' TLS accesses",
        expected=frozenset({"TLS_VAR_SIZE_CHANGED"}),
        **_BINARY,
    ),
    _op(
        "object_became_tls",
        ("elf.obj",),
        _decl("elf.obj", "extern __thread int g_obj;", "__thread int g_obj = 11;"),
        "STT_OBJECT becomes STT_TLS: consumers' relocations no longer match",
        expected=frozenset({"SYMBOL_TYPE_CHANGED"}),
        **_BINARY,
    ),
    _op(
        "func_became_ifunc",
        ("elf.wget",),
        _decl(
            "elf.wget",
            "int wget(int x);",
            "static int wget_impl(int x) { return x * 3 + 1; }\n"
            "static void *wget_resolve(void) { return (void *)wget_impl; }\n"
            'int wget(int x) __attribute__((ifunc("wget_resolve")));',
        ),
        "an IFUNC resolves the same entry point through a resolver",
        expected=frozenset({"IFUNC_INTRODUCED"}),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
)

# --- header constants, unions, records, enums, typedefs --------------------------

_TYPES = (
    _op(
        "header_constant_changed",
        ("elf.const",),
        _decl("elf.const", "static const int EDIT_LIMIT = 32;"),
        "consumers compiled the old value in",
        expected=frozenset({"CONSTANT_CHANGED"}),
        verdict_min="API_BREAK",
        **_HEADERS,
    ),
    _op(
        "header_constant_added",
        ("elf.extra",),
        _decl("elf.extra", "static const int EDIT_NEW_LIMIT = 4;", ""),
        "a new header constant is compatible",
        expected=frozenset({"CONSTANT_ADDED"}),
        **_COMPAT_ONLY,
        **_HEADERS,
    ),
    _op(
        "header_constant_removed",
        ("elf.const",),
        _decl("elf.const", ""),
        "consumers naming EDIT_LIMIT no longer compile",
        expected=frozenset({"CONSTANT_REMOVED"}),
        verdict_min="API_BREAK",
        **_HEADERS,
    ),
    _op(
        "union_member_added",
        ("elf.union",),
        _decl("elf.union", "union num { int i; float f; double d; };"),
        "a wider member grows the union passed by value",
        expected=frozenset({"UNION_FIELD_ADDED"}),
    ),
    _op(
        "union_member_removed",
        ("elf.union",),
        _decl("elf.union", "union num { int i; };"),
        "users of u.f no longer compile; the size is unchanged",
        expected=frozenset({"UNION_FIELD_REMOVED"}),
        verdict_min="API_BREAK",
    ),
    _op(
        "union_member_retyped",
        ("elf.union",),
        _decl("elf.union", "union num { int i; double f; };"),
        "float -> double grows the union passed by value",
        expected=frozenset({"UNION_FIELD_TYPE_CHANGED"}),
    ),
    _op(
        "field_became_bitfield",
        ("elf.flags",),
        _decl("elf.flags", "struct flags { int a; int b : 4; int c; };"),
        "a 4-bit field shares storage with its neighbours: c moves",
        expected=frozenset({"FIELD_BITFIELD_CHANGED"}),
    ),
    _op(
        "field_renamed",
        ("elf.flags", "elf.flags_b"),
        lambda r: _merge(
            _set(r, "elf.flags", "struct flags { int a; int b; int count; };"),
            _set(
                r,
                "elf.flags_b",
                None,
                "int flags_b(struct flags *f) { return f->b + f->count; }",
            ),
        ),
        "a renamed field keeps its layout; users of the old name no longer compile",
        expected=frozenset({"FIELD_RENAMED"}),
        **_SOURCE_BREAK,
        **_HEADERS,
    ),
    _op(
        "field_became_const",
        ("elf.flags",),
        _decl("elf.flags", "struct flags { int a; const int b; int c; };"),
        "writes to the field no longer compile; the layout is unchanged",
        expected=frozenset({"FIELD_BECAME_CONST"}),
        **_SOURCE_BREAK,
        **_HEADERS,
    ),
    _op(
        "field_became_volatile",
        ("elf.flags",),
        _decl("elf.flags", "struct flags { int a; volatile int b; int c; };"),
        "volatile changes how the field is accessed, not where it is",
        expected=frozenset({"FIELD_BECAME_VOLATILE"}),
        verdict_min="COMPATIBLE_WITH_RISK",
        verdict_max="API_BREAK",
        **_HEADERS,
    ),
    _op(
        "field_became_atomic",
        ("elf.flags",),
        _decl("elf.flags", "struct flags { int a; _Atomic int b; int c; };"),
        "_Atomic changes the field's type and how it is accessed",
        expected=frozenset({"ATOMIC_QUALIFIER_CHANGED"}),
        verdict_min="COMPATIBLE_WITH_RISK",
        features=("atomic",),
    ),
    _op(
        "flexible_array_member",
        ("elf.buf",),
        _decl("elf.buf", "struct buf { int n; int d[]; };"),
        "d[1] -> d[] shrinks the struct",
        expected=frozenset({"FLEXIBLE_ARRAY_MEMBER_CHANGED"}),
        features=("flexible_array",),
    ),
    _op(
        "record_became_opaque",
        ("elf.ctx", "elf.ctx_a"),
        lambda r: _merge(
            _set(r, "elf.ctx", "struct ctx;"),
            _set(
                r,
                "elf.ctx_a",
                None,
                "struct ctx { char a; int b; };\nint ctx_a(struct ctx *c) { return c->a; }",
            ),
        ),
        "consumers can no longer declare or size a struct ctx",
        expected=frozenset({"TYPE_BECAME_OPAQUE"}),
        verdict_min="API_BREAK",
        **_HEADERS,
    ),
    _op(
        "record_became_packed",
        ("elf.ctx",),
        _decl("elf.ctx", "struct __attribute__((packed)) ctx { char a; int b; };"),
        "packing removes the padding after a: b moves from offset 4 to 1",
        expected=frozenset({"STRUCT_PACKING_CHANGED"}),
    ),
    _op(
        "struct_became_union",
        ("elf.ctx", "elf.ctx_a"),
        lambda r: _merge(
            _set(r, "elf.ctx", "union ctx { char a; int b; };"),
            _set(
                r,
                "elf.ctx_a",
                "int ctx_a(union ctx *c);",
                "int ctx_a(union ctx *c) { return c->a; }",
            ),
        ),
        "a struct becoming a union overlaps its members",
        expected=frozenset({"TYPE_KIND_CHANGED"}),
    ),
    _op(
        "record_added",
        ("elf.extra",),
        _decl(
            "elf.extra",
            "struct pt { int q; };\nint pt_q(struct pt *p);",
            "int pt_q(struct pt *p) { return p->q; }",
        ),
        "a new type and function are compatible",
        expected=frozenset({"TYPE_ADDED", "FUNC_ADDED"}),
        **_COMPAT_ONLY,
    ),
    _op(
        "record_removed",
        ("elf.ctx", "elf.ctx_a"),
        lambda r: _merge(_set(r, "elf.ctx", ""), _set(r, "elf.ctx_a", "", "")),
        "struct ctx and the function taking it are gone",
        expected=frozenset({"TYPE_REMOVED"}),
        any_of=(_REMOVED,),
    ),
    _op(
        "typedef_removed",
        ("elf.count_t", "elf.counted"),
        lambda r: _merge(
            _set(r, "elf.count_t", ""),
            _set(
                r,
                "elf.counted",
                "int counted(int c);",
                "int counted(int c) { return c + 1; }",
            ),
        ),
        "code naming count_t no longer compiles; the underlying type is unchanged",
        expected=frozenset({"TYPEDEF_REMOVED"}),
        **_SOURCE_BREAK,
        **_HEADERS,
    ),
    _op(
        "enum_member_renamed",
        ("elf.level", "elf.level_code"),
        lambda r: _merge(
            _set(r, "elf.level", "enum level { LOW, MEDIUM, LEVEL_LAST };"),
            _set(
                r,
                "elf.level_code",
                None,
                "int level_code(enum level l) { return l == LOW ? 1 : l == MEDIUM ? 2 : 3; }",
            ),
        ),
        "the values stay; users of MID no longer compile",
        expected=frozenset({"ENUM_MEMBER_RENAMED"}),
        **_SOURCE_BREAK,
        **_HEADERS,
    ),
    _op(
        "enum_sentinel_moved",
        ("elf.level",),
        _decl("elf.level", "enum level { LOW, MID, HIGH, LEVEL_LAST };"),
        # LEVEL_LAST's value changes: consumers compiled with 2 now name HIGH.
        # Conventionally a count, so at least a risk; never benign.
        "a member inserted before the LAST sentinel changes the sentinel's value",
        expected=frozenset({"ENUM_LAST_MEMBER_VALUE_CHANGED"}),
        verdict_min="COMPATIBLE_WITH_RISK",
    ),
)

# --- deprecation -------------------------------------------------------------------

_DEPRECATION = (
    _op(
        "api_deprecated",
        ("elf.vget", "elf.flags", "elf.level", "elf.obj", "elf.union"),
        lambda r: _merge(
            _set(r, "elf.vget", "__attribute__((deprecated)) int vget(int x);"),
            _set(
                r,
                "elf.flags",
                "struct __attribute__((deprecated)) flags { int a; int b; int c; };",
            ),
            _set(
                r,
                "elf.level",
                "enum __attribute__((deprecated)) level { LOW, MID, LEVEL_LAST };",
            ),
            _set(r, "elf.obj", "extern int g_obj __attribute__((deprecated));"),
            _set(
                r,
                "elf.union",
                "union num { int i; float f __attribute__((deprecated)); };",
            ),
        ),
        "deprecation warns at compile time; nothing breaks",
        constructs=("deprecation",),
        expected=frozenset(
            {
                "FUNC_DEPRECATED_ADDED",
                "TYPE_DEPRECATED_ADDED",
                "ENUM_DEPRECATED_ADDED",
                "VAR_DEPRECATED_ADDED",
                "FIELD_DEPRECATED_ADDED",
            }
        ),
        **_COMPAT_ONLY,
        **_HEADERS,
    ),
    _op(
        "api_undeprecated",
        ("elf.legacy",),
        _decl(
            "elf.legacy",
            "struct legacy { int x; };\nenum old_mode { OLD_A, OLD_B };\n"
            "extern int g_old;\nint old_api(struct legacy *l, int x);",
        ),
        "lifting a deprecation changes nothing for consumers",
        constructs=("deprecation",),
        expected=frozenset(
            {
                "FUNC_DEPRECATED_REMOVED",
                "TYPE_DEPRECATED_REMOVED",
                "ENUM_DEPRECATED_REMOVED",
                "VAR_DEPRECATED_REMOVED",
            }
        ),
        **_COMPAT_ONLY,
        **_HEADERS,
    ),
)

# --- function signatures -------------------------------------------------------------

_FUNCTIONS = (
    _op(
        "func_became_variadic",
        ("elf.vsum",),
        _decl(
            "elf.vsum",
            "int vsum(int n, ...);",
            "int vsum(int n, ...) { char b[32]; memset(b, n, sizeof b); return b[1] + n; }",
        ),
        "a variadic callee follows the variadic calling convention (%al on x86-64)",
        expected=frozenset({"FUNC_VARIADIC_ADDED"}),
        min_evidence="dwarf",  # DW_TAG_unspecified_parameters
    ),
    _op(
        "func_lost_variadic",
        ("elf.vfmt",),
        _decl("elf.vfmt", "int vfmt(int n);", "int vfmt(int n) { return n * 4; }"),
        "callers passing extra arguments no longer compile; the convention changes",
        expected=frozenset({"FUNC_VARIADIC_REMOVED"}),
        min_evidence="dwarf",  # DW_TAG_unspecified_parameters
    ),
    _op(
        "param_restrict_added",
        ("elf.pread",),
        _decl(
            "elf.pread",
            "int pread_int(int *restrict p);",
            "int pread_int(int *restrict p) { return *p + 1; }",
        ),
        "restrict promises no aliasing: a caller that aliases now has undefined behaviour",
        expected=frozenset({"PARAM_RESTRICT_ADDED"}),
        **_RISK,
        **_HEADERS,
        features=("restrict",),
    ),
    _op(
        "param_pointer_level_changed",
        ("elf.pread",),
        _decl(
            "elf.pread",
            "int pread_int(int **p);",
            "int pread_int(int **p) { return **p + 1; }",
        ),
        "int * -> int **: the callee dereferences what the caller passed once more",
        expected=frozenset({"PARAM_POINTER_LEVEL_CHANGED"}),
    ),
    _op(
        "return_pointer_level_changed",
        ("elf.pref",),
        _decl(
            "elf.pref",
            "int **pref(void);",
            "static int s_ref = 7;\nstatic int *s_refp = &s_ref;\nint **pref(void) { return &s_refp; }",
        ),
        "int * -> int **: callers dereference the wrong level",
        expected=frozenset({"RETURN_POINTER_LEVEL_CHANGED"}),
    ),
    _op(
        "param_pointee_const_added",
        ("elf.pread",),
        _decl(
            "elf.pread",
            "int pread_int(const int *p);",
            "int pread_int(const int *p) { return *p + 1; }",
        ),
        "a const pointee widens what callers may pass",
        expected=frozenset({"PARAM_POINTEE_QUALIFIER_ADDED"}),
        **_RISK,
        **_HEADERS,
    ),
    _op(
        "return_pointee_const_added",
        ("elf.pref",),
        _decl(
            "elf.pref",
            "const int *pref(void);",
            "static int s_ref = 7;\nconst int *pref(void) { return &s_ref; }",
        ),
        "callers that write through the result no longer compile",
        expected=frozenset({"FUNC_RETURN_POINTEE_QUALIFIER_ADDED"}),
        **_SOURCE_BREAK,
        **_HEADERS,
    ),
)

ELF_OPERATORS: tuple[EditOp, ...] = (
    *_VERSIONING,
    *_DYNAMIC,
    *_SYMBOLS,
    *_TYPES,
    *_DEPRECATION,
    *_FUNCTIONS,
)
