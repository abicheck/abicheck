"""Target-ISA readers: producer strings and GNU-property ISA-needed notes.

The expected feature sets are written out by hand (the psABI level lists and
GCC's implication chain), not derived by calling the implementation's own
closure helper, so a wrong implication table fails here.
"""

from __future__ import annotations

import struct

import pytest

from abicheck.extract.elf_notes import decode_gnu_property_desc
from abicheck.model.target_isa import (
    TargetIsa,
    level_of,
    parse_note_target_isa,
    parse_producer_target_isa,
    target_isa_from_sources,
)

_SSE2_TO_SSE42 = {"sse2", "sse3", "ssse3", "sse4.1", "sse4.2"}
_V3_FEATURES = {
    "sse2",
    "sse3",
    "ssse3",
    "sse4.1",
    "sse4.2",
    "popcnt",
    "cx16",
    "avx",
    "avx2",
    "bmi",
    "bmi2",
    "f16c",
    "fma",
    "lzcnt",
    "movbe",
    "xsave",
}


@pytest.mark.parametrize(
    ("producer", "arch", "features"),
    [
        # GCC always records -march; the baseline has no extension beyond SSE2.
        (
            "GNU C17 13.3.0 -mtune=generic -march=x86-64 -g -O2",
            "x86-64",
            set(),
        ),
        # -mavx2 implies avx, sse4.2, sse4.1, ssse3, sse3, sse2.
        (
            "GNU C17 13.3.0 -mavx2 -mtune=generic -march=x86-64 -g",
            "x86-64",
            {"avx2", "avx"} | _SSE2_TO_SSE42,
        ),
        # -march=x86-64-v3 with -mavx512f: v3 plus AVX-512F (which implies avx2).
        (
            "GNU C17 13.3.0 -march=x86-64-v3 -mavx512f -g -O2",
            "x86-64-v3",
            _V3_FEATURES | {"avx512f"},
        ),
        # -mavx512f requires avx2 by implication even without a level.
        (
            "GNU C++17 11.4.0 -mavx512f -mtune=generic -march=x86-64 -g",
            "x86-64",
            {"avx512f", "avx2", "avx"} | _SSE2_TO_SSE42,
        ),
        # Flags apply in order: -mno-avx drops avx and avx2 (which needs avx).
        (
            "GNU C++17 11.4.0 -mavx2 -mno-avx -march=x86-64 -g",
            "x86-64",
            _SSE2_TO_SSE42,
        ),
        # -mno-avx2 drops avx512f (which implies avx2) but keeps avx.
        (
            "GNU C17 -march=x86-64-v4 -mno-avx2 -g",
            "x86-64-v4",
            _V3_FEATURES - {"avx2"},
        ),
        # A -mno- flag applies to the level's own requirement, not just to -m flags.
        (
            "GNU C17 -march=x86-64-v2 -mno-sse4.2 -g",
            "x86-64-v2",
            {"sse2", "sse3", "ssse3", "sse4.1", "popcnt", "cx16"},
        ),
        # Named CPUs are opaque: the arch is kept, the features come from flags only.
        ("GNU C17 -march=haswell -mtune=haswell -g", "haswell", set()),
        ("GNU C17 -march=skylake-avx512 -g", "skylake-avx512", set()),
        # ARM records the target arch with an extension suffix; it is opaque here.
        ("GNU C17 -march=armv8.2-a+sve -mabi=lp64 -g", "armv8.2-a+sve", set()),
        ("GNU C17 -mcpu=cortex-a72 -g", "cortex-a72", set()),
    ],
)
def test_producer_arch_and_features(producer: str, arch: str, features: set[str]):
    got = parse_producer_target_isa(producer)
    assert got == TargetIsa(arch=arch, features=frozenset(features))


@pytest.mark.parametrize(
    "producer",
    [
        None,
        "",
        # clang records no -march and no ISA flag: the target is unknown.
        "clang version 18.1.3 (https://github.com/llvm/llvm-project)",
        # Non-ISA options never make a target: -mtune/-mabi/-mfpmath are ignored.
        "GNU C17 13.3.0 -mtune=generic -mabi=lp64 -mfpmath=sse -g -O2",
        "GNU C17 13.3.0 -g -O2 -fPIC",
    ],
)
def test_producer_without_isa_evidence_is_unknown(producer: str | None):
    assert parse_producer_target_isa(producer) is None


def test_producer_describe_prefers_recorded_arch():
    assert (
        parse_producer_target_isa("GNU C17 -march=x86-64-v3 -g").describe()
        == "x86-64-v3"
    )


def test_level_of_picks_strongest_satisfied_level():
    assert level_of(frozenset(_V3_FEATURES)) == "x86-64-v3"
    assert level_of(frozenset({"sse2"})) == "x86-64"
    assert level_of(frozenset(_V3_FEATURES | {"avx512f"})) == "x86-64-v3"


def _note_desc(*properties: tuple[int, int]) -> bytes:
    """A NT_GNU_PROPERTY_TYPE_0 description: (pr_type, uint32 data) pairs laid
    out the ELFCLASS64 way (each property padded to 8 bytes)."""
    out = b""
    for pr_type, data in properties:
        out += struct.pack("<II", pr_type, 4) + struct.pack("<I", data) + b"\0" * 4
    return out


_ISA_NEEDED = 0xC0008002


@pytest.mark.parametrize(
    ("bits", "level", "features"),
    [
        (0x1, "x86-64", set()),
        (
            0x2,
            "x86-64-v2",
            {"sse3", "ssse3", "sse4.1", "sse4.2", "popcnt", "cx16", "sse2"},
        ),
        (0x4, "x86-64-v3", _V3_FEATURES),
    ],
)
def test_note_isa_needed_level_decodes_to_required_features(
    bits: int, level: str, features: set[str]
):
    desc = _note_desc((_ISA_NEEDED, bits))
    tokens = decode_gnu_property_desc(desc, little_endian=True, align=8)
    got = parse_note_target_isa(tokens)
    assert got == TargetIsa(arch=None, features=frozenset(features))
    assert got is not None and level_of(got.features) == level


def test_note_with_v2_and_v3_bits_requires_the_stronger_level():
    tokens = decode_gnu_property_desc(
        _note_desc((_ISA_NEEDED, 0x2 | 0x4)), little_endian=True, align=8
    )
    assert parse_note_target_isa(tokens) == TargetIsa(
        arch=None, features=frozenset(_V3_FEATURES)
    )


def test_note_without_isa_token_is_unknown():
    # IBT (x86 CET) is a GNU property but says nothing about the ISA target.
    tokens = decode_gnu_property_desc(
        _note_desc((0xC0000002, 0x1)), little_endian=True, align=8
    )
    assert "IBT" in tokens
    assert parse_note_target_isa(tokens) is None


def test_producer_and_note_merge_into_the_union_requirement():
    producer = parse_producer_target_isa("GNU C17 -mavx2 -march=x86-64 -g")
    note = parse_note_target_isa(["x86-64-v2"])
    merged = target_isa_from_sources(producer, note)
    assert merged is not None
    assert merged.arch == "x86-64"
    assert {"avx2", "avx", "sse4.2", "popcnt", "cx16"} <= merged.features


def test_merge_with_one_side_absent_returns_the_other():
    producer = parse_producer_target_isa("GNU C17 -march=x86-64-v3 -g")
    assert target_isa_from_sources(producer, None) == producer
    assert target_isa_from_sources(None, None) is None
