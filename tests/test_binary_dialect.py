"""Unit tests for :mod:`abicheck.extract.binary_dialect`.

The producer string is the one input the header parse trusts to pick a C++
dialect, so these tests run the parser over a wide set of real-looking GCC,
clang and ICX ``DW_AT_producer`` strings rather than the one the bug report
happened to use. Expected values are written out by hand from the
compiler's documented output, not computed from the module's own tables.
"""

from __future__ import annotations

import pytest

from abicheck.extract.binary_dialect import (
    BinaryDialect,
    binary_dialect_option_tokens,
    parse_producer_dialect,
)

# DWARF ``DW_AT_language`` codes, written out from the DWARF 5 spec.
DW_LANG_C11 = 0x1D
DW_LANG_C_PLUS_PLUS_14 = 0x21  # GCC and clang stamp this for every C++14+
DW_LANG_C_PLUS_PLUS_17 = 0x2A
DW_LANG_C_PLUS_PLUS_20 = 0x2B
DW_LANG_C_PLUS_PLUS_23 = 0x3A


@pytest.mark.parametrize(
    ("producer", "expected"),
    [
        # GCC prints the language standard in its language token, with -m
        # ISA flags it records by default; -std= appears only under
        # -grecord-gcc-switches.
        pytest.param(
            "GNU C++20 13.3.0 -mavx2 -mtune=generic -march=x86-64 -g -fPIC",
            (True, 20, "producer", ("-mavx2", "-mtune=generic", "-march=x86-64")),
            id="gcc13-cxx20-avx2",
        ),
        pytest.param(
            "GNU C++17 11.4.0 -mtune=generic -march=x86-64 -g -O2",
            (True, 17, "producer", ("-mtune=generic", "-march=x86-64")),
            id="gcc11-cxx17-default",
        ),
        pytest.param(
            "GNU C++14 9.4.0 -mtune=generic -march=x86-64 -g",
            (True, 14, "producer", ("-mtune=generic", "-march=x86-64")),
            id="gcc9-cxx14",
        ),
        pytest.param(
            "GNU C++20 12.2.0 -g",
            (True, 20, "producer", ()),
            id="gcc12-cxx20-no-flags",
        ),
        pytest.param(
            "GNU C++23 14.1.0 -mtune=generic -march=x86-64 -g",
            (True, 23, "producer", ("-mtune=generic", "-march=x86-64")),
            id="gcc14-cxx23",
        ),
        pytest.param(
            "GNU C++2b 12.1.0 -g",
            (True, 23, "producer", ()),
            id="gcc12-cxx2b-draft-spelling",
        ),
        pytest.param(
            "GNU C++2a 10.2.0 -g",
            (True, 20, "producer", ()),
            id="gcc10-cxx2a-draft-spelling",
        ),
        pytest.param(
            "GNU C++98 4.8.5 -g",
            (True, None, "producer", ()),
            id="cxx98-no-usable-year",
        ),
        pytest.param(
            "GNU C11 13.3.0 -mtune=generic -march=x86-64 -g",
            (False, None, "producer", ("-mtune=generic", "-march=x86-64")),
            id="gcc-c11-not-cxx",
        ),
        pytest.param(
            "GNU C17 11.4.0 -mavx -mavx2 -g",
            (False, None, "producer", ("-mavx", "-mavx2")),
            id="gcc-c17-avx",
        ),
        # A recorded -std= is the strongest evidence: it overrides the
        # language token, and the last one wins.
        pytest.param(
            "GNU C++20 13.3.0 -g -std=c++17",
            (True, 17, "producer", ()),
            id="recorded-std-overrides-token",
        ),
        pytest.param(
            "GNU C++17 13.3.0 -g -std=gnu++20 -fPIC -std=c++23",
            (True, 23, "producer", ()),
            id="last-recorded-std-wins",
        ),
        pytest.param(
            "GNU C++17 13.3.0 -g -std=gnu11",
            (False, None, "producer", ()),
            id="recorded-c-std-means-not-cxx",
        ),
        pytest.param(
            "GNU C++20 13.3.0 -std=c++1z",
            (True, 17, "producer", ()),
            id="legacy-cxx1z-spelling",
        ),
        # Target ABI flags are excluded: forwarding -m32 would make castxml
        # require multilib headers the host may not have.
        pytest.param(
            "GNU C++20 13.3.0 -m32 -mx32 -m64 -mavx2 -g",
            (True, 20, "producer", ("-mavx2",)),
            id="target-abi-flags-excluded",
        ),
        pytest.param(
            "GNU C++20 13.3.0 -mavx2 -mavx2 -march=x86-64 -march=x86-64 -g",
            (True, 20, "producer", ("-mavx2", "-march=x86-64")),
            id="machine-flags-deduplicated",
        ),
        # clang does not record its flags, so the DWARF language code decides.
        pytest.param(
            "clang version 18.1.3 (https://github.com/llvm/llvm-project)",
            (True, None, "dwarf", ()),
            id="clang-cxx14-stamp-is-ambiguous",
        ),
        pytest.param(
            "Intel(R) oneAPI DPC++/C++ Compiler 2025.0.0 -march=alderlake",
            (True, None, "dwarf", ("-march=alderlake",)),
            id="icx-no-gnu-token-falls-back-to-dwarf",
        ),
    ],
)
def test_producer_string_parses_to_dialect(
    producer: str, expected: tuple[bool | None, int | None, str, tuple[str, ...]]
) -> None:
    is_cxx, year, source, machine = expected
    # clang and ICX name no GNU language token, so they are read through the
    # DWARF code they stamp; everything else is decided by its producer.
    dwarf_language = DW_LANG_C_PLUS_PLUS_14 if not producer.startswith("GNU") else None
    dialect = parse_producer_dialect(producer, dwarf_language)
    assert dialect.is_cxx is is_cxx
    assert dialect.cxx_year == year
    assert dialect.source == source
    assert dialect.machine_flags == machine


@pytest.mark.parametrize(
    ("dwarf_language", "expected_year", "expected_is_cxx"),
    [
        pytest.param(DW_LANG_C_PLUS_PLUS_20, 20, True, id="dwarf-cxx20"),
        pytest.param(DW_LANG_C_PLUS_PLUS_23, 23, True, id="dwarf-cxx23"),
        pytest.param(DW_LANG_C_PLUS_PLUS_17, 17, True, id="dwarf-cxx17"),
        pytest.param(DW_LANG_C_PLUS_PLUS_14, None, True, id="dwarf-cxx14-or-later"),
        pytest.param(DW_LANG_C11, None, False, id="dwarf-c11"),
    ],
)
def test_dwarf_language_is_used_only_without_a_producer(
    dwarf_language: int, expected_year: int | None, expected_is_cxx: bool
) -> None:
    dialect = parse_producer_dialect(None, dwarf_language)
    assert dialect.is_cxx is expected_is_cxx
    assert dialect.cxx_year == expected_year
    assert dialect.source == "dwarf"


def test_no_producer_and_no_language_names_nothing() -> None:
    assert parse_producer_dialect(None, None) == BinaryDialect()
    assert parse_producer_dialect("", None) == BinaryDialect()


def test_producer_language_token_beats_ambiguous_dwarf_code() -> None:
    # GCC's C++20 build is stamped DW_LANG_C_plus_plus_14; the token must win.
    dialect = parse_producer_dialect("GNU C++20 13.3.0 -g", DW_LANG_C_PLUS_PLUS_14)
    assert dialect.cxx_year == 20


def test_unknown_dwarf_code_falls_through_to_unknown() -> None:
    assert parse_producer_dialect(
        "some unrecognised producer", 0xFFFF
    ) == BinaryDialect(machine_flags=())


def _tokens(
    dialect: BinaryDialect,
    *,
    explicit: tuple[str, ...] = (),
    explicit_std: bool = False,
    cxx_mode: bool = True,
) -> tuple[str, ...]:
    return binary_dialect_option_tokens(
        dialect,
        explicit_tokens=explicit,
        explicit_std=explicit_std,
        cxx_mode=cxx_mode,
    )


CXX20_AVX2 = BinaryDialect(
    is_cxx=True, cxx_year=20, source="producer", machine_flags=("-mavx2",)
)


def test_cxx20_binary_in_cxx_parse_selects_cxx20_and_forwards_isa() -> None:
    assert _tokens(CXX20_AVX2) == ("-x", "c++", "-std=gnu++20", "-mavx2")


@pytest.mark.parametrize("year", [20, 23], ids=["cxx20", "cxx23"])
def test_cxx20_or_later_is_parsed_as_gnu_cxx20(year: int) -> None:
    dialect = BinaryDialect(is_cxx=True, cxx_year=year, source="producer")
    assert _tokens(dialect) == ("-x", "c++", "-std=gnu++20")


@pytest.mark.parametrize("year", [11, 14, 17, None], ids=["11", "14", "17", "none"])
def test_older_standard_adds_no_std_flag(year: int | None) -> None:
    dialect = BinaryDialect(is_cxx=True, cxx_year=year, source="producer")
    assert _tokens(dialect) == ()


def test_dialect_never_turns_a_c_parse_into_c_plus_plus() -> None:
    # The guard: a C++20 binary's dialect refines a C++ parse only. A C header
    # in such a binary stays C, the case _exported_symbols_indicate_cpp exists
    # to protect (``struct options { int new; };``).
    assert _tokens(CXX20_AVX2, cxx_mode=False) == ("-mavx2",)


def test_user_standard_is_never_overridden() -> None:
    assert _tokens(CXX20_AVX2, explicit_std=True) == ("-mavx2",)


def test_user_machine_flag_wins_over_the_producers() -> None:
    dialect = BinaryDialect(
        is_cxx=True,
        cxx_year=20,
        source="producer",
        machine_flags=("-march=x86-64", "-mavx2"),
    )
    tokens = _tokens(dialect, explicit=("-march=native",))
    assert "-march=x86-64" not in tokens
    assert tokens == ("-x", "c++", "-std=gnu++20", "-mavx2")


def test_c_binary_forwards_only_its_isa_flags() -> None:
    dialect = BinaryDialect(
        is_cxx=False, cxx_year=None, source="producer", machine_flags=("-mavx2",)
    )
    assert _tokens(dialect, cxx_mode=True) == ("-mavx2",)


def test_unknown_language_adds_no_std_flag() -> None:
    dialect = BinaryDialect(machine_flags=("-mavx2",))
    assert _tokens(dialect) == ("-mavx2",)


#: ELF ``e_machine`` spellings, as pyelftools reports them.
EM_AARCH64 = "EM_AARCH64"
EM_RISCV = "EM_RISCV"
EM_PPC64 = "EM_PPC64"
EM_MIPS = "EM_MIPS"
EM_X86_64 = "EM_X86_64"


@pytest.mark.parametrize(
    ("producer", "e_machine", "expected"),
    [
        # AArch64 GCC records -mlittle-endian -mabi=lp64 in every producer;
        # castxml's clang rejects "-mabi=lp64" ("unknown target ABI"), so the
        # AArch64 -mabi= is dropped. Byte-order options are never forwarded
        # (the byte order is checked against EI_DATA instead). ISA flags stay.
        pytest.param(
            "GNU C17 13.3.0 -mlittle-endian -mabi=lp64 -g -fPIC",
            EM_AARCH64,
            (),
            id="aarch64-default-abi-dropped",
        ),
        pytest.param(
            "GNU C++17 13.3.0 -mbig-endian -mabi=ilp32 -march=armv8.2-a+crypto -g",
            EM_AARCH64,
            ("-march=armv8.2-a+crypto",),
            id="aarch64-ilp32-big-endian-keeps-march",
        ),
        # Elsewhere clang accepts GCC's -mabi= spelling, and a non-default ABI
        # selects different declarations in an ABI-dependent header, so it is
        # forwarded: RISC-V soft-float lp64, PowerPC elfv1, MIPS n64.
        pytest.param(
            "GNU C11 13.2.0 -march=rv64imac -mabi=lp64 -mcmodel=medany -g",
            EM_RISCV,
            ("-march=rv64imac", "-mabi=lp64", "-mcmodel=medany"),
            id="riscv64-soft-float-abi-forwarded",
        ),
        pytest.param(
            "GNU C++20 13.2.0 -march=rv64gc -mabi=lp64d -g",
            EM_RISCV,
            ("-march=rv64gc", "-mabi=lp64d"),
            id="riscv64-default-abi-forwarded",
        ),
        pytest.param(
            "GNU C11 12.2.0 -mbig -mabi=elfv1 -mcpu=power8 -g",
            EM_PPC64,
            ("-mabi=elfv1", "-mcpu=power8"),
            id="ppc64-elfv1-forwarded-byte-order-dropped",
        ),
        pytest.param(
            "GNU C11 12.2.0 -mel -mabi=64 -march=mips64r2 -g",
            EM_MIPS,
            ("-mabi=64", "-march=mips64r2"),
            id="mips64el-n64-forwarded-byte-order-dropped",
        ),
        # Multilib options are dropped on every machine.
        pytest.param(
            "GNU C++20 13.3.0 -m32 -mx32 -m16 -m64 -mavx2 -mlittle -g",
            EM_X86_64,
            ("-mavx2",),
            id="multilib-and-byte-order-dropped",
        ),
        # An unknown machine (not ELF) keeps -mabi=: only a known AArch64
        # image has the spelling clang rejects.
        pytest.param(
            "GNU C11 13.2.0 -mabi=lp64 -g",
            None,
            ("-mabi=lp64",),
            id="unknown-machine-keeps-mabi",
        ),
    ],
)
def test_target_options_by_machine(
    producer: str, e_machine: str | None, expected: tuple[str, ...]
) -> None:
    dialect = parse_producer_dialect(producer, None, e_machine=e_machine)
    assert dialect.machine_flags == expected
