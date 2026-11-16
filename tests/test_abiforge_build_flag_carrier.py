"""A build-flag edit is observable only where its flag is recorded (WS-6 A2.10).

A layout change made by a build flag (``-fpack-struct``) lives in neither the
binary nor a distributed header, so the stripped ``{B,H}`` configuration cannot
see it. An operator declares that with ``EditOp.carrier``. This table is
hand-written from the edit's meaning, not from any checker's answer: the build
flags are the carriers, and the header-source edits are not.
"""

from __future__ import annotations

import pytest

from abiforge.model.config import default_config
from abiforge.model.observable import min_level_cells, unclassified_kinds
from abiforge.model.opkit import BUILD_RECORD
from abiforge.model.seeds import SEEDS, Seed
from abiforge.operators import OPERATORS, matrix
from abiforge.run.toolchains import toolchain

PRODUCER = toolchain("gcc", 13)
CARRIERS = frozenset({"D", "BD"})

#: operator name -> whether its edit is a build flag (carried by D or BD only).
BUILD_FLAG_EDITS = {
    "rebuild_pack_struct": True,
    # A source-level packing (attribute or pragma) lives in the header text.
    "record_became_packed": False,
}


def _op(name):
    return next(op for op in OPERATORS if op.name == name)


def _seeds(op) -> list[Seed]:
    return [s for s in SEEDS if op.applies_to(s)]


@pytest.mark.parametrize("name", sorted(BUILD_FLAG_EDITS))
def test_declared_carrier_matches_the_table(name) -> None:
    op = _op(name)
    assert bool(op.carrier) is BUILD_FLAG_EDITS[name], name
    if op.carrier:
        assert op.carrier == CARRIERS


@pytest.mark.parametrize("name", sorted(BUILD_FLAG_EDITS))
def test_stripped_headers_cannot_show_the_edit_exactly_when_it_is_a_build_flag(
    name,
) -> None:
    op = _op(name)
    for seed in _seeds(op)[:2]:
        base = default_config(seed.lang, PRODUCER)
        stripped = base.with_(evidence="headers", stripped=True)
        dwarf = base.with_(evidence="dwarf", stripped=False)
        assert op.expect(stripped).observable is (not BUILD_FLAG_EDITS[name]), seed.name
        assert op.expect(dwarf).observable is True, seed.name


@pytest.mark.parametrize("name", sorted(BUILD_FLAG_EDITS))
def test_minimal_cells_of_a_build_flag_edit_include_a_carrier(name) -> None:
    op = _op(name)
    for seed in _seeds(op)[:2]:
        base = default_config(seed.lang, PRODUCER)
        cells, _ = min_level_cells(op, base)
        at_min = [c for c in cells if c.role == "at-minimum"]
        assert at_min, seed.name
        for cell in at_min:
            if BUILD_FLAG_EDITS[name]:
                assert cell.atoms & CARRIERS, (seed.name, sorted(cell.atoms))
            else:
                assert cell.atoms, seed.name


def test_every_matrix_pair_of_a_carrier_operator_has_a_floor_cell() -> None:
    """No build-flag edit is silently left without a cell (plan B2)."""
    for op, seed in matrix():
        if not op.carrier:
            continue
        base = default_config(seed.lang, PRODUCER)
        if op.requires(base) is not None or not op.applies_to(seed):
            continue
        cells, unsupported = min_level_cells(op, base)
        at_min = [c for c in cells if c.role == "at-minimum"]
        # An UNCLASSIFIED kind or an unbuildable floor is reported, never dropped;
        # otherwise the edit owes a real at-minimum cell (not just below-minimum).
        reported = bool(unsupported) or bool(unclassified_kinds(op))
        assert at_min or reported, (op.name, seed.name)


# --- the compiler is the oracle for the layout a build flag moves ----------

#: Packed and aligned record shapes; the same header text under each flag set.
SHAPES = {
    "mixed": "struct rec { char tag; double weight; int id; };",
    "pair": "struct rec { char a; int b; };",
    "tail": "struct rec { int x; char y; };",
    "aligned": "struct rec { char tag; _Alignas(16) double w; };",
}


@pytest.mark.integration
@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_same_header_has_a_different_layout_under_the_build_flag(
    tmp_path, shape
) -> None:
    """``-fpack-struct=1`` moves ``sizeof``/``alignof`` with the header text unchanged.

    The probe is compiled by gcc under each flag set; the header is one string.
    A stripped ``{B,H}`` side parses the header without the flag, so its layout
    is the unpacked one: the two differ, which is why the edit needs its carrier.
    """
    import shutil
    import subprocess

    if shutil.which("gcc") is None:
        pytest.skip("needs gcc")
    probe = tmp_path / "probe.c"
    probe.write_text(
        SHAPES[shape]
        + "\n#include <stdio.h>\n#include <stddef.h>\n"
        + 'int main(void){printf("%zu %zu\\n",sizeof(struct rec),_Alignof(struct rec));return 0;}\n',
        encoding="utf-8",
    )

    def layout(*flags: str) -> str:
        exe = tmp_path / ("p" + "".join(flags).replace("=", "").replace("-", "_"))
        subprocess.run(
            ["gcc", *flags, str(probe), "-o", str(exe)], check=True, capture_output=True
        )
        return subprocess.run(
            [str(exe)], check=True, capture_output=True, text=True
        ).stdout

    unpacked = layout()
    packed = layout("-fpack-struct=1")
    assert packed != unpacked, shape
    # alignof falls to 1 under -fpack-struct=1 for every shape: the header carries
    # the same declaration, so only the flag tells the two layouts apart.
    assert packed.split()[1] == "1", shape


# --- the operator enumeration: every build-flag edit is classified ---------

#: Build flags the header text and the binary both lack: a configuration sees
#: the edit only through a recorded build (DWARF or build data). Hand-written
#: from the flag's meaning, never from a checker.
CARRIED_FLAG_EDITS = frozenset(
    {
        "rebuild_pack_struct",  # -fpack-struct: layout only
        "rebuild_short_enums",  # -fshort-enums: enum size only
        "char_made_unsigned",  # -funsigned-char: signedness, no symbol
        "short_wchar",  # -fshort-wchar: wchar_t width
        "target_isa_raised",  # -mavx2: code, not exported symbols
        "vector_math_abi_changed",  # -mveclibabi: the call target is in the IR
        "debug_info_dropped",  # -g0: removes the DWARF record itself
        "long_double_made_64bit",  # -mlong-double-64: x87 format, in the DWARF type
        "l3_exceptions_enabled",  # -fexceptions: unwind tables, build-recorded
        "l3_tls_model_local_dynamic",  # -ftls-model: build-recorded TLS model
        "l3_lto_enabled",  # -flto: build-recorded
        "l3_struct_packing_8",  # -fpack-struct=8 (build data)
        "l3_language_standard_raised",  # -std: build-recorded
        "l3_threadsafe_statics_disabled",  # -fno-threadsafe-statics (build data)
        "l3_stdlib_assertions",  # _GLIBCXX_ASSERTIONS (build data)
        "l3_whole_program_vtables",  # -fwhole-program-vtables (build data)
    }
)

#: Build flags the binary (symbols, relocations, imports, notes) shows: the
#: edit is observable at {B}, so it must NOT declare a carrier. Declaring one
#: would hide a real binary break at the cheapest evidence level.
BINARY_VISIBLE_FLAG_EDITS = frozenset(
    {
        "rebuild_m32",  # ELF machine and class
        "rebuild_x32",  # ELF class with EM_X86_64
        "rebuild_cxx11_abi_off",  # mangled names
        "rebuild_against_libcxx",  # mangled names and imports
        "rebuild_optimized",  # imports and the canary call
        "rebuild_hidden_visibility",  # export visibility
        "std_string_abi_flipped",  # mangled names
        "stack_protector_dropped",  # the canary call
        "tls_initial_exec",  # TLS relocations
        "cet_disabled",  # ELF property notes
        "cet_enabled",  # ELF property notes
        "no_common_spelled",  # symbol binding
        "static_tls_removed",  # TLS segment flag
        "gnu_unique_disabled",  # STB_GNU_UNIQUE binding
        "l3_sanitizer_enabled",  # sanitizer runtime imports
        "l3_asan_enabled",  # __asan_* imports
        "l3_rtti_disabled",  # typeinfo symbols
    }
)

#: Build flags whose macros reach the stripped header dump (the harness passes
#: a build role's -D/-U to castxml), so the header records the change. Their
#: declared floor says {B,D}; whether that floor is right is an open question
#: this table records, not a carrier to declare (a carrier would hide a header
#: change the dump can see).
HEADER_VISIBLE_FLAG_EDITS = frozenset(
    {
        "time_bits_64",  # -D_TIME_BITS=64 widens time_t in the header
    }
)


def _is_build_flag_edit(op) -> bool:
    return op.rebuild is not None or "build.cflags" in op.roles


def test_every_build_flag_edit_is_classified_by_hand() -> None:
    """A new flag operator must be placed in one of the two tables above."""
    names = {op.name for op in OPERATORS}
    for listed in (
        CARRIED_FLAG_EDITS | BINARY_VISIBLE_FLAG_EDITS | HEADER_VISIBLE_FLAG_EDITS
    ):
        assert listed in names, f"{listed} is listed but no operator has that name"
    derived = {op.name for op in OPERATORS if _is_build_flag_edit(op)}
    unclassified = derived - (
        CARRIED_FLAG_EDITS | BINARY_VISIBLE_FLAG_EDITS | HEADER_VISIBLE_FLAG_EDITS
    )
    assert not unclassified, (
        f"build-flag edits with no hand classification: {sorted(unclassified)}"
    )


def test_carrier_is_declared_exactly_on_the_carried_flag_edits() -> None:
    """No source or header edit declares a carrier; no binary-visible flag does."""
    for op in OPERATORS:
        if op.name in CARRIED_FLAG_EDITS:
            assert op.carrier == BUILD_RECORD, op.name
        else:
            assert not op.carrier, f"{op.name} declares a carrier it should not"


def test_stripped_headers_cannot_show_each_carried_edit() -> None:
    """The carried edits are unobservable at {B,H}; binary-visible ones are not gated."""
    for op in OPERATORS:
        if (
            op.name not in CARRIED_FLAG_EDITS
            or op.requires(default_config("c", PRODUCER)) is not None
        ):
            continue
        for seed in _seeds(op)[:1]:
            base = default_config(seed.lang, PRODUCER)
            stripped = base.with_(evidence="headers", stripped=True)
            assert op.expect(stripped).observable is False, op.name
