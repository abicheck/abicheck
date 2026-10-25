"""The caller's own flags win over what a binary's producer records.

A caller's ``-mno-avx2`` must not be undone by the producer's ``-mavx2``, and
a caller's ``-ansi`` is an explicit dialect choice, so no ``-std=`` is
inferred after it. These run in-process with the two external readers
replaced, as in ``test_binary_dialect_wiring.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.extract import binary_dialect
from abicheck.extract.binary_dialect import (
    BinaryDialect,
    binary_dialect_option_tokens,
    header_parse_option_tokens,
)
from abicheck.extract.build_mode_capture import ElfBuildSignals

CXX20_AVX2 = "GNU C++20 13.3.0 -mavx2 -mtune=generic -march=x86-64 -g"
IMAGE = Path("libxv.so")


@pytest.fixture
def cxx20_image(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """A C++20 producer, and a counter of export-table reads."""
    import abicheck.extract.build_mode_capture as capture
    import abicheck.extract.elf_symbol_classify as classify

    monkeypatch.setattr(
        capture,
        "read_elf_build_signals",
        lambda path, *, read_dwarf, debug_path: ElfBuildSignals(producer=CXX20_AVX2),
    )
    reads: list[int] = []

    def fake_exports(path):  # noqa: ANN001
        reads.append(1)
        return set(), set()

    monkeypatch.setattr(classify, "_pyelftools_exported_symbols", fake_exports)
    return reads


def test_caller_negated_flag_is_not_undone_by_the_producer() -> None:
    dialect = BinaryDialect(machine_flags=("-mavx2", "-mtune=generic"))
    tokens = binary_dialect_option_tokens(
        dialect,
        explicit_tokens=("-mno-avx2",),
        explicit_std=False,
        cxx_mode=False,
    )
    assert "-mavx2" not in tokens
    assert tokens == ("-mtune=generic",)


def test_caller_positive_flag_is_not_undone_by_a_producer_negation() -> None:
    dialect = BinaryDialect(machine_flags=("-mno-avx2",))
    tokens = binary_dialect_option_tokens(
        dialect,
        explicit_tokens=("-mavx2",),
        explicit_std=False,
        cxx_mode=False,
    )
    assert tokens == ()


def test_caller_ansi_is_an_explicit_standard(cxx20_image: list[int]) -> None:
    tokens = header_parse_option_tokens(
        IMAGE,
        debug_path=None,
        lang="c++",
        headers=[],
        gcc_options="-ansi",
        gcc_option_tokens=(),
    )
    assert not any(token.startswith("-std=") for token in tokens)
    assert "-x" not in tokens
    # The producer's ISA flags still apply: only the standard is the caller's.
    assert "-mavx2" in tokens
    assert cxx20_image == []


def test_without_ansi_the_cxx20_standard_is_still_inferred(
    cxx20_image: list[int],
) -> None:
    tokens = header_parse_option_tokens(
        IMAGE,
        debug_path=None,
        lang="c++",
        headers=[],
        gcc_options=None,
        gcc_option_tokens=(),
    )
    assert "-std=gnu++20" in tokens
    assert cxx20_image == [1]


@pytest.mark.parametrize(
    ("token", "key"),
    [
        ("-mavx2", "-mavx2"),
        ("-mno-avx2", "-mavx2"),
        ("-march=native", "-march"),
        ("-mno-red-zone", "-mred-zone"),
    ],
)
def test_option_key_settles_a_feature_in_both_senses(token: str, key: str) -> None:
    assert binary_dialect._option_key(token) == key
