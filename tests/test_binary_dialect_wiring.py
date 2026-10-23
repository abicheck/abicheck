"""In-process tests of the wiring around :mod:`abicheck.extract.binary_dialect`.

The end-to-end behaviour is covered by ``test_binary_dialect_integration.py``,
which runs the CLI in a subprocess and so does not count toward in-process
coverage. These tests call the helpers directly, with the two external
readers (the image's DWARF producer and its export table) replaced by fixed
values, so every branch of the wiring runs without a compiler.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from abicheck.compile_context import CompileContext
from abicheck.extract import binary_dialect
from abicheck.extract.binary_dialect import (
    BinaryDialect,
    binary_dialect_compile,
    header_parse_option_tokens,
    read_elf_binary_dialect,
    record_implied_options,
)
from abicheck.extract.build_mode_capture import ElfBuildSignals

DW_LANG_C_PLUS_PLUS_20 = 0x2B
CXX20_PRODUCER = "GNU C++20 13.3.0 -mavx2 -mtune=generic -march=x86-64 -g"
IMAGE = Path("libxv.so")


@pytest.fixture
def signals(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """The producer the image reports, settable per test; ``None`` = no DWARF."""
    state: dict[str, object] = {"value": None, "calls": []}

    def fake_read(path, *, read_dwarf, debug_path):  # noqa: ANN001
        state["calls"].append((path, read_dwarf, debug_path))
        return state["value"]

    import abicheck.extract.build_mode_capture as capture

    monkeypatch.setattr(capture, "read_elf_build_signals", fake_read)
    return state


@pytest.fixture
def exports(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count export-table reads; the table itself is empty."""
    reads: list[int] = []

    def fake_exports(path):  # noqa: ANN001
        reads.append(1)
        return set(), set()

    import abicheck.extract.elf_symbol_classify as classify

    monkeypatch.setattr(classify, "_pyelftools_exported_symbols", fake_exports)
    return reads


def test_read_elf_binary_dialect_without_dwarf_is_empty(signals) -> None:
    signals["value"] = None
    assert read_elf_binary_dialect(IMAGE, Path("libxv.debug")) == BinaryDialect()
    # The detached debug file is handed to the reader, which decides whether it
    # is needed; DWARF reading is always enabled for a dialect read.
    assert signals["calls"] == [(IMAGE, True, Path("libxv.debug"))]


def test_read_elf_binary_dialect_reads_the_producer(signals) -> None:
    signals["value"] = ElfBuildSignals(
        producer=CXX20_PRODUCER, dwarf_language=DW_LANG_C_PLUS_PLUS_20
    )
    dialect = read_elf_binary_dialect(IMAGE)
    assert dialect.is_cxx is True
    assert dialect.cxx_year == 20
    assert dialect.source == "producer"
    assert dialect.machine_flags == ("-mavx2", "-mtune=generic", "-march=x86-64")


def test_header_parse_tokens_add_std_and_isa_for_a_cxx_parse(signals, exports) -> None:
    signals["value"] = ElfBuildSignals(producer=CXX20_PRODUCER)
    tokens = header_parse_option_tokens(
        IMAGE,
        debug_path=None,
        lang="c++",
        headers=[],
        gcc_options=None,
        gcc_option_tokens=(),
    )
    assert tokens == (
        "-x",
        "c++",
        "-std=gnu++20",
        "-mavx2",
        "-mtune=generic",
        "-march=x86-64",
    )
    assert exports == [1]


def test_header_parse_tokens_skip_std_for_a_c_parse(signals, exports) -> None:
    # The binary is C++20, but the caller forced C: the dialect must not turn a
    # C parse into C++, and the export table need not be read at all.
    signals["value"] = ElfBuildSignals(producer=CXX20_PRODUCER)
    tokens = header_parse_option_tokens(
        IMAGE,
        debug_path=None,
        lang="c",
        headers=[],
        gcc_options=None,
        gcc_option_tokens=(),
    )
    assert "-x" not in tokens
    assert "-std=gnu++20" not in tokens
    assert "-mavx2" in tokens


def test_header_parse_tokens_respect_a_user_standard(signals, exports) -> None:
    signals["value"] = ElfBuildSignals(producer=CXX20_PRODUCER)
    tokens = header_parse_option_tokens(
        IMAGE,
        debug_path=None,
        lang="c++",
        headers=[],
        gcc_options="-std=gnu++17",
        gcc_option_tokens=(),
    )
    assert not any(token.startswith("-std=") for token in tokens)
    assert exports == []


def test_header_parse_tokens_for_a_stripped_image_are_empty(signals, exports) -> None:
    signals["value"] = None
    assert (
        header_parse_option_tokens(
            IMAGE,
            debug_path=None,
            lang=None,
            headers=[],
            gcc_options=None,
            gcc_option_tokens=(),
        )
        == ()
    )
    assert exports == []


def test_binary_dialect_compile_appends_implied_tokens(signals, exports) -> None:
    signals["value"] = ElfBuildSignals(producer=CXX20_PRODUCER)
    base = CompileContext(gcc_option_tokens=("-I", "/opt/include"))
    compile, implied = binary_dialect_compile(
        base, IMAGE, lang="c++", lang_explicit=True, headers=[]
    )
    assert implied == (
        "-x",
        "c++",
        "-std=gnu++20",
        "-mavx2",
        "-mtune=generic",
        "-march=x86-64",
    )
    assert compile is not base
    assert compile.gcc_option_tokens == ("-I", "/opt/include", *implied)


def test_binary_dialect_compile_keeps_identity_when_nothing_is_implied(
    signals, exports
) -> None:
    signals["value"] = None
    base = CompileContext()
    compile, implied = binary_dialect_compile(
        base, IMAGE, lang="c++", lang_explicit=True, headers=[]
    )
    assert compile is base
    assert implied == ()


def test_binary_dialect_compile_builds_a_default_context_when_none_is_given(
    signals, exports
) -> None:
    signals["value"] = ElfBuildSignals(producer=CXX20_PRODUCER)
    compile, implied = binary_dialect_compile(
        None, IMAGE, lang="c++", lang_explicit=True, headers=[]
    )
    assert compile is not None
    assert compile.gcc_option_tokens == implied
    assert "-std=gnu++20" in implied


def test_record_implied_options_marks_an_inferred_standard_as_not_pinned() -> None:
    snap = SimpleNamespace(
        ast_toolchain={"language_standard_explicit": "1", "producer": "gcc"}
    )
    out = record_implied_options(
        snap,
        ("-x", "c++", "-std=gnu++20", "-mavx2"),  # type: ignore[arg-type]
    )
    assert out is snap
    assert snap.ast_toolchain == {
        "language_standard_explicit": "0",
        "producer": "gcc",
        "implied_options": "-x c++ -std=gnu++20 -mavx2",
    }


def test_record_implied_options_leaves_an_explicit_standard_pinned() -> None:
    snap = SimpleNamespace(ast_toolchain={"language_standard_explicit": "1"})
    record_implied_options(snap, ("-mavx2",))  # type: ignore[arg-type]
    assert snap.ast_toolchain == {
        "language_standard_explicit": "1",
        "implied_options": "-mavx2",
    }


def test_record_implied_options_is_a_no_op_without_implied_flags() -> None:
    snap = SimpleNamespace(ast_toolchain={"language_standard_explicit": "0"})
    record_implied_options(snap, ())  # type: ignore[arg-type]
    assert snap.ast_toolchain == {"language_standard_explicit": "0"}


def test_module_exports_the_wiring_helpers() -> None:
    for name in (
        "binary_dialect_compile",
        "header_parse_option_tokens",
        "read_elf_binary_dialect",
        "record_implied_options",
    ):
        assert name in binary_dialect.__all__
