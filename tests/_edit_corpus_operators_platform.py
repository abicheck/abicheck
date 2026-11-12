"""Edit operators for the ``c_elf`` seed: platform-level facts of the ELF object.

Companion of ``_edit_corpus_operators_elf`` (same seed, same conventions):
each edit changes how the library is built or what its object file carries --
its ELF header, program headers, dynamic section, imports, symbol table
representation, or the debug info it ships -- and declares its ground truth
from the loader's and the platform ABI's rules, never from abicheck's answer.
"""

from __future__ import annotations

from typing import Any

from tests._edit_corpus_expect import Rule
from tests._edit_corpus_operators_elf import (
    _AT_MOST_RISK,
    _BINARY,
    _COMPAT_ONLY,
    _RISK,
    _SOURCE_BREAK,
    _build,
    _cflags,
    _decl,
    _ld,
    _op,
    _vscript,
)
from tests._edit_corpus_opkit import (
    _REMOVED,
    Edit,
    EditOp,
    Roles,
    _merge,
    _no_edit,
    _set,
    _with,
)
from tests._edit_corpus_seeds import RoleText

__all__ = ["PLATFORM_OPERATORS"]


def _x86(c: Any) -> str | None:
    # The corpus targets x86 Linux; the asm and -m switches below are x86's.
    return None if c.objfmt == "elf" else f"{c.objfmt}: not an x86 ELF target"


def _x86_64(c: Any) -> str | None:
    return _x86(c) or (
        "an i386 build has no x86-64 ISA level" if c.flags.bits == 32 else None
    )


def _not_32(c: Any) -> str | None:
    return "the producer already builds -m32" if c.flags.bits == 32 else None


_HEADER = (
    _op(
        "rebuild_m32",
        ("elf.vget",),
        _no_edit,
        "an ELFCLASS32 i386 object cannot be loaded into an x86-64 process: "
        "every consumer fails to load the library",
        # EM_X86_64 -> EM_386 and ELFCLASS64 -> ELFCLASS32 together: either
        # names a library built for another architecture.
        any_of=(frozenset({"ELF_MACHINE_CHANGED", "ELF_CLASS_CHANGED"}),),
        touches=(),
        precondition=_not_32,
        rebuild=_with(bits=32),
        **_BINARY,
    ),
    _op(
        "rebuild_x32",
        ("build.cflags",),
        _cflags("-fstack-protector-strong -mx32"),
        "the x32 ABI keeps EM_X86_64 but writes an ELFCLASS32 object: the "
        "loader of an x86-64 process rejects it ('wrong ELF class')",
        expected=frozenset({"ELF_CLASS_CHANGED"}),
        precondition=_not_32,
        probe_flags=("-mx32",),
        **_BINARY,
    ),
)

# --- program headers, dynamic section, hardening ---------------------------------

_TEXTREL_SRC = (
    "int vget(int x) { return x * 2; }\n"
    # An absolute address of g_obj stored in .text: an R_X86_64_64 /
    # R_386_32 relocation the loader must apply to the code segment.
    "#ifdef __x86_64__\n"
    '__asm__(".text\\n.p2align 3\\nedit_textref: .quad g_obj\\n.previous");\n'
    "#else\n"
    '__asm__(".text\\n.p2align 2\\nedit_textref: .long g_obj\\n.previous");\n'
    "#endif"
)
_WX_SRC = (
    "int vget(int x) { return x * 2; }\n"
    # A section flagged allocatable, writable and executable: ld places it
    # in a PT_LOAD segment mapped RWX.
    '__asm__(".section .edit_wx,\\"awx\\",@progbits\\n.byte 0\\n.previous");'
)

_DYNAMIC = (
    _op(
        "text_relocation_introduced",
        ("elf.vget", "build.ldflags"),
        lambda r: _merge(
            _decl("elf.vget", "int vget(int x);", _TEXTREL_SRC)(r),
            _ld("-Wl,-z,notext")(r),
        ),
        "a relocation against .text makes the loader write the code segment "
        "(DT_TEXTREL): refused under W^X policies, and the pages stop being shared",
        expected=frozenset({"TEXT_RELOCATION_INTRODUCED"}),
        precondition=_x86,
        **_RISK,
        **_BINARY,
    ),
    _op(
        "writable_executable_segment",
        ("elf.vget",),
        _decl("elf.vget", "int vget(int x);", _WX_SRC),
        "an RWX PT_LOAD segment: a W^X-enforcing loader refuses the library, "
        "and every process mapping it gets writable code",
        expected=frozenset({"WRITABLE_EXECUTABLE_SEGMENT"}),
        **_RISK,
        **_BINARY,
    ),
    _op(
        "init_fini_dropped",
        ("build.ldflags",),
        _ld("-nostartfiles"),
        "without the start files the library has no DT_INIT/DT_FINI (nor "
        "crtbegin's .fini_array): the loader runs no initializer or "
        "finalizer for it any more",
        expected=frozenset({"ELF_INIT_FINI_CHANGED"}),
        **_RISK,
        **_BINARY,
    ),
    _op(
        "x86_isa_level_raised",
        ("build.ldflags",),
        _ld("-Wl,-z,x86-64-v3"),
        "the GNU_PROPERTY_X86_ISA_1_NEEDED note now demands x86-64-v3: a "
        "glibc with ISA-level checks refuses to load the library on an older CPU",
        expected=frozenset({"X86_ISA_BASELINE_RAISED"}),
        precondition=_x86_64,
        probe_flags=("-Wl,-z,x86-64-v3",),
        **_RISK,
        **_BINARY,
    ),
    _op(
        "target_isa_raised",
        ("build.cflags",),
        _cflags("-fstack-protector-strong -mavx2"),
        "code built for AVX2 may execute instructions an older x86 CPU lacks: "
        "the library can fault with SIGILL there",
        expected=frozenset({"TARGET_ISA_CHANGED"}),
        rules=(
            Rule(
                lambda c: c.producer.family != "gcc",
                {
                    "expected": frozenset(),
                    "verdict_min": "NO_CHANGE",
                    "observable": False,
                },
                "clang records no -m switch in DW_AT_producer and -mavx2 alone "
                "writes no ISA note: nothing in the binary or its debug info says "
                "which ISA the code targets",
            ),
        ),
        precondition=_x86,
        min_evidence="dwarf",
        **_RISK,
    ),
    _op(
        "debug_info_dropped",
        ("build.cflags",),
        _cflags("-fstack-protector-strong -g0"),
        "the new build ships no DWARF: consumers are unaffected, but every "
        "type-level comparison of the release loses its evidence",
        expected=frozenset({"DWARF_INFO_MISSING"}),
        verdict_min="COMPATIBLE",
        verdict_max="COMPATIBLE_WITH_RISK",
        min_evidence="dwarf",
    ),
)

_TOOLCHAIN = (
    _op(
        "vector_math_abi_changed",
        ("build.cflags",),
        _cflags("-fstack-protector-strong -mveclibabi=svml"),
        "vectorized math calls now target Intel SVML's entry points: a "
        "process without that library cannot resolve them",
        expected=frozenset({"VECTOR_ABI_CHANGED"}),
        rules=(
            Rule(
                lambda c: c.producer.family != "gcc",
                {
                    "expected": frozenset(),
                    "verdict_min": "NO_CHANGE",
                    "observable": False,
                },
                "-mveclibabi is a gcc switch; clang records none in DW_AT_producer",
            ),
        ),
        precondition=_x86,
        # A gcc switch: clang refuses it, so its cells read unavailable.
        probe_flags=("-mveclibabi=svml",),
        min_evidence="dwarf",
        **_RISK,
    ),
    _op(
        "short_wchar",
        ("build.cflags",),
        _cflags("-fstack-protector-strong -fshort-wchar"),
        "wchar_t shrinks to 16 bits: any wchar_t that crosses the boundary "
        "changes size (this library passes none, but the model is now foreign "
        "to the C library's)",
        expected=frozenset({"WCHAR_MODEL_CHANGED"}),
        rules=(
            Rule(
                lambda c: c.producer.family != "gcc",
                {
                    "expected": frozenset(),
                    "verdict_min": "NO_CHANGE",
                    "observable": False,
                },
                "clang records no -f switches in DW_AT_producer, and the "
                "library passes no wchar_t",
            ),
        ),
        min_evidence="dwarf",
        **_RISK,
    ),
    _op(
        "object_alignment_reduced",
        ("elf.al",),
        _decl(
            "elf.al",
            "extern int g_al __attribute__((aligned(2)));",
            "int g_al __attribute__((aligned(2))) = 5;",
        ),
        "g_al was promised 16-byte alignment and now has 2: a consumer that "
        "relied on it (aligned vector loads of &g_al) can fault",
        any_of=(
            frozenset({"EXPORTED_OBJECT_ALIGNMENT_REDUCED", "VAR_ALIGNMENT_CHANGED"}),
        ),
        rules=(
            Rule(
                lambda c: c.evidence == "binary",
                {"any_of": (), "verdict_min": "COMPATIBLE", "observable": False},
                "without debug info only the address's low bits speak, and a "
                "2-aligned object can still land 16-aligned",
            ),
        ),
        verdict_min="COMPATIBLE_WITH_RISK",
        min_evidence="dwarf",
    ),
)


def _bit_int_toolchain(c: Any) -> str | None:
    if c.producer.family == "gcc" and c.producer.version < 14:
        return "gcc implements _BitInt from 14"
    if c.producer.family not in ("gcc", "clang"):
        return f"{c.producer.family} has no _BitInt"
    return None


_BIT_INT = (
    _op(
        "field_became_bit_int",
        ("elf.flags",),
        _decl("elf.flags", "struct flags { int a; int b; _BitInt(48) c; };"),
        "c becomes a 48-bit _BitInt stored in 8 bytes: struct flags grows "
        "from 12 to 16 bytes",
        expected=frozenset({"BIT_INT_WIDTH_CHANGED"}),
        features=("bit_int",),
        precondition=_bit_int_toolchain,
        min_evidence="dwarf",
    ),
)

# --- imports and the versions they require ------------------------------------------

_IMPORTS = (
    _op(
        "imported_symbol_added",
        ("elf.vget",),
        _decl(
            "elf.vget",
            "int vget(int x);",
            "#include <stdlib.h>\n"
            'int vget(int x) { return x * 2 + (getenv("EDIT_CORPUS_NONE") != 0); }',
        ),
        "the library now calls getenv: a new undefined symbol it needs the C "
        "library to provide, at a version glibc has always had",
        expected=frozenset({"IMPORTED_SYMBOL_ADDED"}),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
    _op(
        "glibc_version_required_added",
        ("elf.vget",),
        _decl(
            "elf.vget",
            "int vget(int x);",
            "#include <sys/random.h>\n"
            "int vget(int x) { char b; return x * 2 + (int)getrandom(&b, 0, 0); }",
        ),
        "getrandom is versioned GLIBC_2.25, newer than anything the library "
        "required before: it no longer loads on an older glibc",
        expected=frozenset({"IMPORTED_SYMBOL_ADDED", "SYMBOL_VERSION_REQUIRED_ADDED"}),
        **_RISK,
        **_BINARY,
    ),
    _op(
        "glibc_older_version_required",
        ("elf.vget",),
        _decl(
            "elf.vget",
            "int vget(int x);",
            # Declared by hand: <stdlib.h> hides realpath under a strict -std.
            "#include <stdlib.h>\nchar *realpath(const char *path, char *resolved);\n"
            'int vget(int x) { char *p = realpath("/", 0); free(p); return x * 2; }',
        ),
        # The -O0 build calls __stack_chk_fail@GLIBC_2.4 (vsum's guarded
        # frame); realpath's default version, GLIBC_2.3, is older.
        "realpath@GLIBC_2.3 is a new version requirement, but no newer than "
        "the GLIBC_2.4 the library already needs: it loads wherever it loaded",
        expected=frozenset(
            {"IMPORTED_SYMBOL_ADDED", "SYMBOL_VERSION_REQUIRED_ADDED_COMPAT"}
        ),
        rules=(
            Rule(
                lambda c: c.flags.opt == "O2",
                {
                    "expected": frozenset({"IMPORTED_SYMBOL_ADDED"}),
                    "any_of": (
                        frozenset(
                            {
                                "SYMBOL_VERSION_REQUIRED_ADDED",
                                "SYMBOL_VERSION_REQUIRED_ADDED_COMPAT",
                            }
                        ),
                    ),
                    "verdict_max": "COMPATIBLE_WITH_RISK",
                },
                "-O2 leaves no guarded frame, so whether GLIBC_2.4 was already "
                "required depends on what else the optimizer kept",
            ),
        ),
        precondition=_x86_64,
        **_AT_MOST_RISK,
        **_BINARY,
    ),
)

# --- what the library exports, and how ---------------------------------------------

_EXTRA_FUNCS = "\n".join(f"int edit_h{i}(int x);" for i in range(80))
_EXTRA_DEFS = "\n".join(
    f"int edit_h{i}(int x) {{ return x + {i}; }}" for i in range(80)
)

_EXPORTS = (
    _op(
        "export_surface_exploded",
        ("elf.extra",),
        _decl("elf.extra", _EXTRA_FUNCS, _EXTRA_DEFS),
        "eighty new exported functions: every one is a compatible addition, "
        "but the export table more than triples",
        expected=frozenset({"FUNC_ADDED", "ABI_SURFACE_EXPLOSION"}),
        **_COMPAT_ONLY,
        **_BINARY,
    ),
    _op(
        "unversioned_export_added",
        ("build.vscript", "elf.extra"),
        lambda r: _merge(
            # Every old export starts with a letter other than 'e' and stays
            # in LIBEDIT_1.0; the new edit_unv matches no node.
            _build(r, "build.vscript", "LIBEDIT_1.0 { global: [!e]*; };"),
            _decl(
                "elf.extra", "int edit_unv(int x);", "int edit_unv(int x) { return x; }"
            )(r),
        ),
        "edit_unv is exported but bound to no version node: a later "
        "definition of it under a version cannot keep old consumers' "
        "references bound",
        expected=frozenset({"FUNC_ADDED", "UNVERSIONED_EXPORTED_SYMBOL"}),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
    _op(
        "declared_but_not_exported",
        ("elf.extra",),
        _decl("elf.extra", "int edit_missing(int x);", ""),
        "the header declares edit_missing, the library defines nothing: a "
        "consumer that calls it fails to link; built consumers are unaffected",
        expected=frozenset({"PUBLIC_NOT_EXPORTED"}),
        **_AT_MOST_RISK,
        min_evidence="headers",
    ),
    _op(
        "exported_but_undeclared",
        ("elf.wget",),
        _decl("elf.wget", "", "int wget(int x) { return x * 3 + 1; }"),
        "wget leaves the header but stays exported: built consumers still "
        "bind it, consumers that name it no longer compile",
        expected=frozenset({"EXPORTED_NOT_PUBLIC"}),
        rules=(
            Rule(
                lambda c: c.flags.visibility == "hidden",
                {
                    "expected": frozenset(),
                    "any_of": (_REMOVED | {"FUNC_VISIBILITY_CHANGED"},),
                    "verdict_min": "BREAKING",
                    "verdict_max": None,
                },
                "under -fvisibility=hidden only the header's default-visibility "
                "block exported wget: leaving the header hides it, and "
                "consumers fail to load",
                min_evidence="binary",
            ),
        ),
        verdict_min="API_BREAK",
        verdict_max="API_BREAK",
        min_evidence="headers",
    ),
    _op(
        "export_localized_header_kept",
        ("build.vscript",),
        _vscript("LIBEDIT_1.0 { global: *; local: vget; };"),
        "vget becomes local while the header still declares it: consumers "
        "fail with 'undefined symbol: vget'",
        any_of=(_REMOVED | {"FUNC_VISIBILITY_CHANGED"},),
        rules=(
            Rule(
                lambda c: c.evidence == "headers",
                {"expected": frozenset({"PUBLIC_NOT_EXPORTED"})},
                "the public header still declares vget, which nothing exports",
            ),
        ),
        **_BINARY,
    ),
)

# --- c_elf_alt: the baseline carries the feature, the edit removes it ------------
#
# Every operator here names ``alt.gate``, the role that admits an operator to
# the c_elf_alt seed (``Seed.gate_role``); its text is empty and never edited.


def _alt(name: str, roles: tuple[str, ...], edit: Edit, why: str, **kw: Any) -> EditOp:
    kw.setdefault("touches", roles)
    # The baseline has no DT_SONAME, so a break owes no SONAME advisory
    # (``EditOp.with_derived`` skips it for an operator that reads the
    # SONAME role).
    gate = ("alt.gate",) if "build.soname" in roles else ("alt.gate", "build.soname")
    return EditOp(name, (*gate, *roles), edit, why, **kw)


def _drop_ld(*tokens: str) -> Edit:
    """Remove *tokens* from the seed's current link flags."""

    def edit(r: Roles) -> dict[str, RoleText]:
        flags = [t for t in r["build.ldflags"][0].split() if t not in tokens]
        return _build(r, "build.ldflags", " ".join(flags))

    return edit


def _swap_ld(old: str, new: str) -> Edit:
    return lambda r: _build(r, "build.ldflags", r["build.ldflags"][0].replace(old, new))


def _cflags_add(*flags: str) -> Edit:
    return lambda r: _build(r, "build.cflags", " ".join([r["build.cflags"][0], *flags]))


_ALT = (
    _alt(
        "executable_stack_removed",
        ("build.ldflags",),
        _drop_ld("-Wl,-z,execstack"),
        "PT_GNU_STACK loses PF_X: a hardening gain no consumer can observe",
        expected=frozenset({"EXECUTABLE_STACK_REMOVED"}),
        **_COMPAT_ONLY,
        **_BINARY,
    ),
    _alt(
        "relr_relocs_unpacked",
        ("build.ldflags",),
        _drop_ld("-Wl,-z,pack-relative-relocs"),
        "the relative relocations are no longer packed into DT_RELR: an older "
        "loader can apply them again, nothing else changes",
        expected=frozenset({"DT_RELR_REMOVED"}),
        probe_flags=("-Wl,-z,pack-relative-relocs",),
        **_COMPAT_ONLY,
        **_BINARY,
    ),
    _alt(
        "rpath_became_runpath",
        ("build.ldflags",),
        _swap_ld("-Wl,--disable-new-dtags", "-Wl,--enable-new-dtags"),
        "the same search path moves from DT_RPATH to DT_RUNPATH: it now comes "
        "after LD_LIBRARY_PATH and no longer applies to transitive dependencies",
        expected=frozenset({"RPATH_TYPE_CHANGED"}),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
    _alt(
        "soname_added",
        ("build.soname",),
        lambda r: _build(r, "build.soname", "libedit.so"),
        "a library that had no DT_SONAME gets one: consumers linked before "
        "recorded the file name, which still resolves",
        expected=frozenset({"SONAME_MISSING"}),
        verdict_min="COMPATIBLE",
        verdict_max="COMPATIBLE_WITH_RISK",
        **_BINARY,
    ),
    _alt(
        "text_relocation_removed",
        ("alt.textrel",),
        lambda r: _set(r, "alt.textrel", "", ""),
        "no relocation against .text remains: the code pages stay read-only",
        expected=frozenset({"TEXT_RELOCATION_REMOVED"}),
        precondition=_x86,
        **_COMPAT_ONLY,
        **_BINARY,
    ),
    _alt(
        "static_tls_removed",
        ("build.cflags",),
        lambda r: _build(
            r,
            "build.cflags",
            r["build.cflags"][0].replace(" -ftls-model=initial-exec", ""),
        ),
        "t_ie goes back to the dynamic TLS model: DF_STATIC_TLS clears and "
        "dlopen of the library can no longer exhaust the static TLS block",
        expected=frozenset({"STATIC_TLS_REMOVED"}),
        verdict_min="COMPATIBLE",
        verdict_max="COMPATIBLE_WITH_RISK",
        **_BINARY,
    ),
    _alt(
        "ifunc_removed",
        ("alt.ifunc",),
        _decl("alt.ifunc", "int fast(int x);", "int fast(int x) { return x + 7; }"),
        "fast stops being an IFUNC: callers still bind the same name and get "
        "the same behaviour, resolved without a resolver",
        expected=frozenset({"IFUNC_REMOVED"}),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
    _alt(
        "imported_symbol_removed",
        ("alt.env",),
        _decl("alt.env", "int envq(int x);", "int envq(int x) { return x; }"),
        "the library stops calling getenv: one dependency on the C library fewer",
        expected=frozenset({"IMPORTED_SYMBOL_REMOVED"}),
        **_COMPAT_ONLY,
        **_BINARY,
    ),
    _alt(
        "glibc_version_requirement_dropped",
        ("alt.rnd",),
        _decl("alt.rnd", "int rnd(int x);", "int rnd(int x) { return x; }"),
        "getrandom was the only GLIBC_2.25 import: the library loads on an "
        "older glibc again",
        expected=frozenset(
            {"IMPORTED_SYMBOL_REMOVED", "SYMBOL_VERSION_REQUIRED_REMOVED"}
        ),
        **_COMPAT_ONLY,
        **_BINARY,
    ),
    _alt(
        "fortified_call_dropped",
        ("alt.chk",),
        _decl(
            "alt.chk",
            "int chk_copy(char *dst, const char *src, int n);",
            "#include <string.h>\n"
            "int chk_copy(char *dst, const char *src, int n) "
            "{ memcpy(dst, src, (size_t)n); return n; }",
        ),
        "the bounds-checked __memcpy_chk becomes a plain memcpy: an overflow "
        "the old library aborted on now goes unchecked",
        expected=frozenset({"FORTIFY_SOURCE_WEAKENED"}),
        **_RISK,
        **_BINARY,
    ),
    _alt(
        "empty_version_node_dropped",
        ("build.vscript",),
        _vscript("LIBEDIT_1.0 { global: *; };"),
        "LIBEDIT_1.1 bound no symbol, so no consumer could record a need for "
        "it: dropping the node breaks nobody",
        expected=frozenset({"SYMBOL_VERSION_DEFINED_REMOVED"}),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
    _alt(
        "api_version_suffix_bumped",
        ("alt.ver",),
        _decl(
            "alt.ver",
            "int api_v2_open(int x);\nint api_v2_read(int x);\nint api_v2_close(int x);",
            "int api_v2_open(int x) { return x + 1; }\n"
            "int api_v2_read(int x) { return x + 2; }\n"
            "int api_v2_close(int x) { return x + 3; }",
        ),
        "every api_v1_* function is renamed api_v2_*: consumers bound to the "
        "v1 names fail to load",
        expected=frozenset({"VERSIONED_SYMBOL_SCHEME_DETECTED"}),
        # Symbols alone pair the old and new names as a likely rename.
        any_of=(_REMOVED | {"FUNC_LIKELY_RENAMED"},),
        **_BINARY,
    ),
    _alt(
        "returned_struct_grew_past_registers",
        ("alt.pair",),
        _decl(
            "alt.pair",
            "struct pair2 { long a; long b; long c; };\nstruct pair2 mkpair(long a);",
            "struct pair2 mkpair(long a) { struct pair2 p = { a, a + 1, a + 2 }; return p; }",
        ),
        "a 16-byte struct returns in RAX:RDX; at 24 bytes it returns through a "
        "hidden pointer the caller passes: old callers pass none",
        expected=frozenset({"STRUCT_RETURN_CONVENTION_CHANGED"}),
        precondition=_x86_64,
        min_evidence="dwarf",
    ),
    _alt(
        "long_double_made_64bit",
        ("build.cflags",),
        _cflags_add("-mlong-double-64"),
        "long double shrinks from the x87 80-bit format to a double: ld_half "
        "reads its argument from another register class than callers pass it in",
        expected=frozenset({"LONG_DOUBLE_ABI_CHANGED"}),
        precondition=_x86,
        probe_flags=("-mlong-double-64",),
        min_evidence="dwarf",
    ),
    _alt(
        "integer_typedef_widened",
        ("alt.int",),
        _decl(
            "alt.int",
            "typedef long edit_int;\nedit_int iscale(edit_int v);\nedit_int ishift(edit_int v);\n"
            "edit_int imix(edit_int a, edit_int b);",
            "edit_int iscale(edit_int v) { return v * 3; }\n"
            "edit_int ishift(edit_int v) { return v << 1; }\n"
            "edit_int imix(edit_int a, edit_int b) { return a ^ b; }",
        ),
        "the library's integer typedef widens to long: every parameter and "
        "return typed edit_int changes size together",
        expected=frozenset({"INTEGER_MODEL_CHANGED"}),
        precondition=_x86_64,
        min_evidence="dwarf",
    ),
    _alt(
        "time_bits_64",
        ("build.cflags",),
        _cflags_add("-D_TIME_BITS=64", "-D_FILE_OFFSET_BITS=64"),
        "on i386 _TIME_BITS=64 makes time_t 64-bit: when_next's argument and "
        "result double in size",
        expected=frozenset({"TIME64_ABI_CHANGED"}),
        precondition=lambda c: (
            None if c.flags.bits == 32 else "time_t is already 64-bit"
        ),
        min_evidence="dwarf",
    ),
    _alt(
        "header_only_function_params_changed",
        ("alt.inl",),
        _decl(
            "alt.inl",
            "static inline int hscale(int v, int *f) { return v * *f; }\nint huse(int v);",
            "int huse(int v) { int f = 2; return hscale(v, &f); }",
        ),
        "hscale lives only in the header: callers passing an int no longer compile",
        expected=frozenset({"HEADER_ONLY_FUNCTION_PARAMS_CHANGED"}),
        **_SOURCE_BREAK,
        min_evidence="headers",
    ),
    _alt(
        "internal_helper_hidden",
        ("alt.helper",),
        _decl(
            "alt.helper",
            "",
            '__attribute__((visibility("hidden"))) '
            "int edit_internal_helper(int x) { return x - 1; }",
        ),
        "an internal-looking helper leaked into the export table; hiding it "
        "breaks any consumer that bound it",
        any_of=(_REMOVED | {"FUNC_VISIBILITY_CHANGED"},),
        rules=(
            Rule(
                lambda c: c.evidence == "binary",
                {"expected": frozenset({"VISIBILITY_LEAK"})},
                "from the export table alone, the old library's "
                "edit_internal_helper reads as an internal symbol it leaked",
            ),
        ),
        **_BINARY,
    ),
)

# --- C++: cxx_alt (gated, ``xa.gate``) and cxx_more additions ----------------------


_DEFAULT_VIS = '__attribute__((visibility("default")))'


def _xa(name: str, roles: tuple[str, ...], edit: Edit, why: str, **kw: Any) -> EditOp:
    kw.setdefault("touches", roles)
    return EditOp(name, ("xa.gate", *roles), edit, why, **kw)


def _elf_libstdcxx(c: Any) -> str | None:
    if c.objfmt != "elf" or c.producer.family not in ("gcc", "clang"):
        return "the dual std::string ABI is a libstdc++ (ELF gcc/clang) property"
    if c.flags.cxx11_abi == 0:
        return "the producer already builds the old std::string ABI"
    return None


_CXX = (
    _xa(
        "gnu_unique_disabled",
        ("build.cflags",),
        lambda r: _build(r, "build.cflags", "-fno-gnu-unique"),
        "uniq_counter's static loses STB_GNU_UNIQUE: two DSOs that each carry "
        "a copy (dlopen'ed with RTLD_LOCAL) stop sharing one counter",
        expected=frozenset({"SYMBOL_BINDING_LOST_UNIQUE"}),
        # Only gcc emits STB_GNU_UNIQUE, and only gcc knows the switch:
        # clang refuses it, so its cells read unavailable.
        probe_flags=("-fno-gnu-unique",),
        **_RISK,
        **_BINARY,
    ),
    _xa(
        "allocator_replacement_removed",
        ("xa.alloc",),
        lambda r: _set(r, "xa.alloc", "", ""),
        "the library no longer replaces the global operator new/delete: every "
        "allocation in the process goes back to the C++ runtime's",
        expected=frozenset({"ALLOCATOR_REPLACEMENT_REMOVED"}),
        **_RISK,
        **_BINARY,
    ),
    _xa(
        "std_string_abi_flipped",
        ("xa.str",),
        _no_edit,
        "_GLIBCXX_USE_CXX11_ABI=0 re-mangles all six std::string functions: "
        "a consumer built for the new ABI finds none of them",
        expected=frozenset(
            {"GLIBCXX_DUAL_ABI_FLIP_DETECTED", "GLIBCXX_DUAL_ABI_CHANGED"}
        ),
        touches=(),
        precondition=_elf_libstdcxx,
        rebuild=_with(cxx11_abi=0),
        needs_libstdcxx=True,
        **_BINARY,
    ),
    _xa(
        "by_value_struct_became_nontrivial",
        ("xa.tv",),
        _decl(
            "xa.tv",
            "struct Tv { int a; ~Tv(); };\nint tv_get(Tv t);",
            "Tv::~Tv() {}\nint tv_get(Tv t) { return t.a + 1; }",
        ),
        "a user-provided destructor makes Tv non-trivial for calls: it is now "
        "passed by invisible reference, where old callers pass it in a register",
        expected=frozenset({"VALUE_ABI_TRAIT_CHANGED"}),
        precondition=_x86_64,
        min_evidence="dwarf",
    ),
    _xa(
        "internal_type_grew",
        ("xa.leak",),
        _decl(
            "xa.leak",
            "namespace lib {\nnamespace detail { struct Impl { int a; int b; }; }\n"
            "struct Holder { detail::Impl impl; int x; };\nint holder_get(const Holder *h);\n}",
        ),
        "lib::detail::Impl is internal by name, yet lib::Holder embeds it by "
        "value: its growth moves Holder::x",
        expected=frozenset({"INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API"}),
        min_evidence="headers",
    ),
    _xa(
        "header_inline_removed",
        ("xa.inl",),
        lambda r: _set(r, "xa.inl", "", ""),
        "demo::clampi lived only in the header: consumers that call it no "
        "longer compile, built ones carry their own copy",
        expected=frozenset({"INLINE_FUNCTION_REMOVED"}),
        **_SOURCE_BREAK,
        min_evidence="headers",
    ),
    _xa(
        "char_param_became_char8",
        ("xa.ch",),
        _decl(
            "xa.ch",
            "int count_chars(const char8_t *s);",
            "int count_chars(const char8_t *s) { int n = 0; while (s[n]) ++n; return n; }",
        ),
        "const char * becomes const char8_t *: the mangled name changes and "
        "callers passing a char string no longer compile",
        expected=frozenset({"CHAR8T_MIGRATION"}),
        features=("char8_t",),
        min_evidence="dwarf",
    ),
    _xa(
        "internal_template_instance_dropped",
        ("xa.tmpl",),
        lambda r: _set(
            r,
            "xa.tmpl",
            r["xa.tmpl"][0].replace("float", "double"),
            r["xa.tmpl"][1].replace("float", "double"),
        ),
        "walk<float> is no longer instantiated: consumers whose inlined "
        "sum_range<float> calls it fail to load",
        expected=frozenset({"INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API"}),
        min_evidence="headers",
    ),
    _op(
        "gnu_unique_static_added",
        ("x.extra",),
        _decl(
            "x.extra",
            "inline int &edit_ctr() { static int n = 0; return n; }\nint edit_bump();",
            "int edit_bump() { return ++edit_ctr(); }",
        ),
        "the first STB_GNU_UNIQUE export: the loader now pins the library "
        "(dlclose no longer unloads it)",
        expected=frozenset({"SYMBOL_BINDING_BECAME_UNIQUE"}),
        rules=(
            Rule(
                lambda c: c.producer.family != "gcc",
                {
                    "expected": frozenset(),
                    "verdict_min": "COMPATIBLE",
                    "verdict_max": "COMPATIBLE",
                },
                "only gcc emits STB_GNU_UNIQUE; under clang the edit is a plain addition",
            ),
        ),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
    _op(
        "allocator_replacement_added",
        ("x.extra",),
        _decl(
            "x.extra",
            "",
            "#include <cstdlib>\n#include <new>\n"
            f"{_DEFAULT_VIS} void *operator new(std::size_t n) "
            "{ void *p = std::malloc(n ? n : 1); if (!p) throw std::bad_alloc(); return p; }\n"
            f"{_DEFAULT_VIS} void operator delete(void *p) noexcept {{ std::free(p); }}\n"
            f"{_DEFAULT_VIS} void operator delete(void *p, std::size_t) noexcept "
            "{ std::free(p); }",
        ),
        "the library now replaces the global operator new/delete for the whole process",
        expected=frozenset({"ALLOCATOR_REPLACEMENT_ADDED"}),
        **_RISK,
        **_BINARY,
    ),
    _op(
        "std_template_instance_exported",
        ("x.extra",),
        _decl(
            "x.extra",
            "int vec_sum(int n);",
            "#include <vector>\n"
            "int vec_sum(int n) { std::vector<int> v((unsigned)n, 1); int s = 0; "
            "for (unsigned i = 0; i < v.size(); ++i) s += v[i]; return s; }",
        ),
        "the library now exports libstdc++'s std::vector<int> instantiations as "
        "weak symbols: it interposes the runtime's definitions",
        expected=frozenset({"FUNC_ADDED", "SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED"}),
        rules=(
            Rule(
                lambda c: c.flags.opt == "O2" or c.flags.visibility == "hidden",
                {"expected": frozenset({"FUNC_ADDED"})},
                "-O2 inlines the vector code (and hidden visibility keeps the "
                "instantiations out of the export table)",
            ),
        ),
        **_AT_MOST_RISK,
        **_BINARY,
    ),
    _op(
        "class_rtti_became_protected",
        ("x.poly",),
        _decl(
            "x.poly",
            'struct __attribute__((visibility("protected"))) Base '
            "{ virtual int m(); virtual ~Base(); };\n"
            "struct Derived : Base { int m(); };",
        ),
        "Base's vtable and type_info become protected: the library binds its "
        "own copy, so a consumer's copy-relocated _ZTI4Base no longer is the "
        "one dynamic_cast compares against",
        expected=frozenset({"VTABLE_SYMBOL_IDENTITY_CHANGED"}),
        verdict_min="COMPATIBLE_WITH_RISK",
        **_BINARY,
    ),
)

PLATFORM_OPERATORS: tuple[EditOp, ...] = (
    *_HEADER,
    *_DYNAMIC,
    *_TOOLCHAIN,
    *_BIT_INT,
    *_IMPORTS,
    *_EXPORTS,
    *_ALT,
    *_CXX,
)
