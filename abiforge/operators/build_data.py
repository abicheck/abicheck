"""Edit operators that change a compile mode only L3 build data records.

Each edit changes one compile flag of the library build (the seed's
``build.cflags`` role) whose setting nothing in the binary, DWARF or public
header carries: the language-runtime mode (exceptions, RTTI, thread-safe
statics), the TLS model, LTO, a sanitizer, the struct-packing width, the
standard-library assertion macro, the language standard and the sysroot.
A checker sees them only from the build's compile commands, so every
operator here needs a ``build_data`` config (``Config.build_data``: each
side's ``compile_commands.json`` is given to the checker).

Ground truth, from the flag's meaning: each edit is chosen so the shipped
interface is unchanged (no layout, symbol or calling-convention effect on
the seed), so the right answer is the build-mode risk alone, never a break.
Where a flag would also change the binary interface (``-fpack-struct``
packing a padded struct, ``-fno-rtti`` dropping typeinfo a seed exports) the
operator picks the spelling that does not, and says so. The exception is a
sanitizer runtime the library cannot load without (ASan always, UBSan under
clang, which links no runtime into a shared library): that edit breaks every
uninstrumented consumer, and its ground truth says so.
"""

from __future__ import annotations

from typing import Any

from abiforge.model.config import Config
from abiforge.model.expect import Rule
from abiforge.model.opkit import Edit, EditOp, Roles, _set
from abiforge.model.seeds import RoleText

__all__ = ["BUILD_DATA_OPERATORS", "needs_build_data"]

# c_elf's baseline compile flags (``build.cflags``); an edit keeps them.
_ELF_BASE = "-fstack-protector-strong"

_RISK: dict[str, Any] = {
    "verdict_min": "COMPATIBLE_WITH_RISK",
    "verdict_max": "COMPATIBLE_WITH_RISK",
}


def needs_build_data(config: Config) -> str | None:
    """Applicability: the cell must give the checker L3 build data."""
    if config.build_data:
        return None
    return (
        "the flag is recorded only in the build's compile commands (L3); "
        "needs a build_data config"
    )


def _packing_not_tighter(config: Config) -> str | None:
    """Applicability of the -fpack-struct=8 edit: a baseline packing tighter
    than 8 (``Flags(pack=1)``) is relaxed by the edit (the later flag wins),
    which changes the seed's layout -- not this operator's ground truth."""
    pack = config.flags.pack
    if pack is not None and pack < 8:
        return f"baseline -fpack-struct={pack} is tighter than 8: the edit would change layout"
    return needs_build_data(config)


def _clang_with_build_data(config: Config) -> str | None:
    if config.producer.family != "clang":
        return "-fwhole-program-vtables is a clang option"
    return needs_build_data(config)


def _cflags(value: str) -> Edit:
    def edit(r: Roles) -> dict[str, RoleText]:
        return _set(r, "build.cflags", value, "")

    return edit


def _elf(name: str, flags: str, kind: str, why: str, **kw: Any) -> EditOp:
    """A c_elf build edit: the baseline flags plus *flags*."""
    kw.setdefault("precondition", needs_build_data)
    return EditOp(
        name,
        ("build.cflags",),
        _cflags(f"{_ELF_BASE} {flags}"),
        why,
        expected=kw.pop("expected", frozenset({kind})),
        touches=("build.cflags",),
        min_evidence="headers",
        **{**_RISK, **kw},
    )


def _cxx(name: str, flags: str, kind: str, why: str, **kw: Any) -> EditOp:
    """A cxx_alt build edit (its baseline adds no compile flags)."""
    kw.setdefault("precondition", needs_build_data)
    return EditOp(
        name,
        ("build.cflags", "xa.gate"),
        _cflags(flags),
        why,
        expected=kw.pop("expected", frozenset({kind})),
        touches=("build.cflags",),
        min_evidence="headers",
        **{**_RISK, **kw},
    )


BUILD_DATA_OPERATORS: tuple[EditOp, ...] = (
    _elf(
        "l3_exceptions_enabled",
        "-fexceptions",
        "EXCEPTIONS_MODE_CHANGED",
        "C code built with unwind tables: a C++ exception may now pass through "
        "the library; nothing in the interface records the mode",
    ),
    _elf(
        "l3_tls_model_local_dynamic",
        "-ftls-model=local-dynamic",
        "TLS_MODEL_CHANGED",
        "an explicit TLS model a consumer cannot see; for an exported __thread "
        "variable the compiler keeps global-dynamic access, so the binary "
        "interface is unchanged and only the build mode moved",
    ),
    _elf(
        "l3_lto_enabled",
        "-flto",
        "LTO_MODE_CHANGED",
        "link-time optimization changes code generation, not the interface",
        # A toolchain that compiles but cannot load its LTO linker plugin
        # fails at link time: probe the link so the cell is unavailable.
        probe_flags=("-flto",),
    ),
    _elf(
        "l3_sanitizer_enabled",
        "-fsanitize=undefined",
        "SANITIZER_MODE_CHANGED",
        "gcc links UBSan's shared runtime (a new NEEDED entry and its "
        "imports), which loads into an uninstrumented process: deployment "
        "risk, no interface change",
        verdict_min="COMPATIBLE",
        probe_flags=("-fsanitize=undefined",),
        rules=(
            Rule(
                lambda c: c.producer.family == "clang",
                {
                    "expected": frozenset(
                        {"SANITIZER_MODE_CHANGED", "SANITIZER_RUNTIME_REQUIRED"}
                    ),
                    "verdict_min": "BREAKING",
                    "verdict_max": "BREAKING",
                },
                "clang links no sanitizer runtime into a -shared library: it "
                "imports __ubsan_* with nothing among its DT_NEEDED to provide "
                "them, so no uninstrumented process can load it",
            ),
        ),
    ),
    _elf(
        "l3_asan_enabled",
        "-fsanitize=address",
        "SANITIZER_MODE_CHANGED",
        "an ASan library needs the ASan runtime as the first library of the "
        "process (gcc: DT_NEEDED libasan; clang: __asan_* imports only an "
        "instrumented executable provides): every uninstrumented consumer "
        "fails to load it",
        expected=frozenset({"SANITIZER_MODE_CHANGED", "SANITIZER_RUNTIME_REQUIRED"}),
        verdict_min="BREAKING",
        verdict_max="BREAKING",
        probe_flags=("-fsanitize=address",),
    ),
    _elf(
        "l3_struct_packing_8",
        "-fpack-struct=8",
        "STRUCT_PACKING_MODE_CHANGED",
        "-fpack-struct=8 caps member alignment at 8, which no member of the "
        "seed's structs exceeds (none is a long double or vector): every "
        "layout is unchanged, but a "
        "packing mode a consumer must match is now in force",
        precondition=_packing_not_tighter,
    ),
    _elf(
        "l3_language_standard_raised",
        "-std=gnu2x",
        "ABI_RELEVANT_BUILD_FLAG_CHANGED",
        "the library is compiled as a newer C dialect (the later -std wins); "
        "the seed's declarations mean the same in both, so only the "
        "ABI-relevant build flag moved",
    ),
    _cxx(
        "l3_threadsafe_statics_disabled",
        "-fno-threadsafe-statics",
        "THREADSAFE_STATICS_MODE_CHANGED",
        "the seed's function-local static is constant-initialized, so no "
        "guard is emitted either way; a later dynamic static would race",
    ),
    _cxx(
        "l3_rtti_disabled",
        "-fno-rtti",
        "RTTI_MODE_CHANGED",
        "the seed declares no polymorphic class, so it exports no typeinfo "
        "-fno-rtti could drop; a consumer's dynamic_cast or typeid across a "
        "later polymorphic type would no longer work",
    ),
    _cxx(
        "l3_stdlib_assertions",
        "-D_GLIBCXX_ASSERTIONS",
        "STDLIB_DEBUG_MODE_CHANGED",
        "libstdc++ assertions add checks, not layout (unlike _GLIBCXX_DEBUG); "
        "the build's standard-library mode moved",
    ),
    _cxx(
        "l3_whole_program_vtables",
        "-flto=thin -fwhole-program-vtables",
        "WHOLE_PROGRAM_VTABLES_MODE_CHANGED",
        "whole-program devirtualization assumes no class outside the LTO unit "
        "derives from the library's classes: a consumer subclass breaks that "
        "assumption, with no interface change to show it",
        expected=frozenset({"WHOLE_PROGRAM_VTABLES_MODE_CHANGED", "LTO_MODE_CHANGED"}),
        precondition=_clang_with_build_data,
        probe_flags=("-flto=thin", "-fwhole-program-vtables"),
    ),
)
