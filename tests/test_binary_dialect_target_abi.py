"""Target-ABI and byte-order handling of the options inferred from a binary.

Bug class ``extraction.header_dialect_not_recovered_from_binary``: the header
parse of an ELF image reuses the ``-m`` options its DWARF producer records,
but an option that pins the target ABI or byte order is not portable between
GCC and castxml's clang. AArch64 GCC records ``-mabi=lp64`` in every producer
and clang rejects it, which failed every gcc-built AArch64 header parse.

The invariants, each tested across machines with hand-written expectations:

* ``-mabi=`` is dropped only for an AArch64 image, decided by the ELF
  ``e_machine`` rather than the producer text; elsewhere it is forwarded,
  because a non-default ABI selects different header declarations;
* a caller's own ``-mabi=`` always wins over the producer's;
* byte-order options are never forwarded; instead a parse whose frontend
  byte order differs from the image's ``EI_DATA`` is refused with a named
  reason, unless the caller aimed the parse at a target.
"""

from __future__ import annotations

import itertools
import struct
import sys
from pathlib import Path

import pytest

from abicheck.compile_context import CompileContext
from abicheck.errors import HeaderToolchainError
from abicheck.extract import binary_dialect
from abicheck.extract.binary_dialect import (
    binary_dialect_compile,
    check_header_parse_byte_order,
    header_parse_option_tokens,
    read_elf_target,
)
from abicheck.extract.build_mode_capture import ElfBuildSignals

IMAGE = Path("libtarget.so")
OTHER_ORDER = "big" if sys.byteorder == "little" else "little"

# ELF e_machine numbers, from the System V gABI / psABI supplements.
_EM_NUMBERS = {"EM_X86_64": 62, "EM_AARCH64": 183, "EM_PPC64": 21, "EM_RISCV": 243}


def _elf_header(path: Path, *, machine: str, order: str) -> Path:
    """A 64-bit ET_DYN ELF header with no sections, in *order* byte order."""
    fmt = "<" if order == "little" else ">"
    ident = b"\x7fELF" + bytes([2, 1 if order == "little" else 2, 1]) + bytes(9)
    header = ident + struct.pack(
        fmt + "HHIQQQIHHHHHH",
        3,  # e_type ET_DYN
        _EM_NUMBERS[machine],
        1,  # e_version
        0,  # e_entry
        0,  # e_phoff
        0,  # e_shoff
        0,  # e_flags
        64,  # e_ehsize
        56,  # e_phentsize
        0,  # e_phnum
        64,  # e_shentsize
        0,  # e_shnum
        0,  # e_shstrndx
    )
    path.write_bytes(header)
    return path


@pytest.mark.parametrize(
    ("machine", "order"),
    [
        ("EM_X86_64", "little"),
        ("EM_AARCH64", "little"),
        ("EM_AARCH64", "big"),
        ("EM_PPC64", "big"),
        ("EM_RISCV", "little"),
    ],
)
def test_read_elf_target_reports_machine_and_byte_order(
    tmp_path: Path, machine: str, order: str
) -> None:
    image = _elf_header(tmp_path / "lib.so", machine=machine, order=order)
    assert read_elf_target(image) == (machine, order)


def test_read_elf_target_of_a_non_elf_file_is_unknown(tmp_path: Path) -> None:
    path = tmp_path / "not.so"
    path.write_bytes(b"not an elf image")
    assert read_elf_target(path) == (None, None)


@pytest.fixture
def image(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Fix the producer and the ELF identity the helpers read for IMAGE."""
    state: dict[str, object] = {"producer": None, "machine": None, "order": None}

    def fake_signals(path, *, read_dwarf, debug_path):  # noqa: ANN001
        return ElfBuildSignals(producer=state["producer"])  # type: ignore[arg-type]

    import abicheck.extract.build_mode_capture as capture
    import abicheck.extract.elf_symbol_classify as classify

    monkeypatch.setattr(capture, "read_elf_build_signals", fake_signals)
    monkeypatch.setattr(
        binary_dialect,
        "read_elf_target",
        lambda path: (state["machine"], state["order"]),
    )
    monkeypatch.setattr(
        classify, "_pyelftools_exported_symbols", lambda path: (set(), set())
    )
    return state


@pytest.mark.parametrize(
    ("producer", "machine", "caller_tokens", "expected"),
    [
        pytest.param(
            "GNU C17 13.3.0 -mlittle-endian -mabi=lp64 -march=armv8-a -g",
            "EM_AARCH64",
            (),
            ("-march=armv8-a",),
            id="aarch64-mabi-dropped",
        ),
        pytest.param(
            "GNU C11 13.2.0 -march=rv64imac -mabi=lp64 -g",
            "EM_RISCV",
            (),
            ("-march=rv64imac", "-mabi=lp64"),
            id="riscv-soft-float-abi-reaches-frontend",
        ),
        pytest.param(
            "GNU C11 12.2.0 -mbig -mabi=elfv1 -mcpu=power8 -g",
            "EM_PPC64",
            (),
            ("-mabi=elfv1", "-mcpu=power8"),
            id="ppc64-elfv1-reaches-frontend",
        ),
        pytest.param(
            "GNU C11 13.2.0 -march=rv64imac -mabi=lp64 -g",
            "EM_RISCV",
            ("-mabi=lp64d",),
            ("-march=rv64imac",),
            id="caller-mabi-wins",
        ),
        pytest.param(
            "GNU C11 13.2.0 -march=rv64imac -mabi=lp64 -g",
            "EM_RISCV",
            ("-march=rv64gc",),
            ("-mabi=lp64",),
            id="caller-march-wins-mabi-still-inferred",
        ),
    ],
)
def test_header_parse_tokens_by_machine(
    image: dict[str, object],
    producer: str,
    machine: str,
    caller_tokens: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    image.update(producer=producer, machine=machine)
    tokens = header_parse_option_tokens(
        IMAGE,
        debug_path=None,
        lang="c",
        headers=[],
        gcc_options=None,
        gcc_option_tokens=caller_tokens,
    )
    assert tokens == expected


# Exhaustive over the small domain: image byte order (or unknown) x frontend
# byte order x whether the caller aimed the parse at a target. The oracle is
# written out: refuse exactly when the orders are known, differ, and the
# caller chose no target.
_CASES = list(
    itertools.product(("little", "big", None), ("little", "big"), (False, True))
)


@pytest.mark.parametrize(("image_order", "frontend_order", "selected"), _CASES)
def test_byte_order_check_refuses_only_an_unaimed_mismatch(
    image_order: str | None, frontend_order: str, selected: bool
) -> None:
    refuse = image_order is not None and image_order != frontend_order and not selected
    if refuse:
        with pytest.raises(HeaderToolchainError, match=f"{image_order}-endian"):
            check_header_parse_byte_order(
                image_order,
                frontend_byte_order=frontend_order,
                frontend_target_selected=selected,
            )
    else:
        check_header_parse_byte_order(
            image_order,
            frontend_byte_order=frontend_order,
            frontend_target_selected=selected,
        )


def test_refusal_names_the_remedy() -> None:
    with pytest.raises(HeaderToolchainError) as info:
        check_header_parse_byte_order(
            "big", frontend_byte_order="little", frontend_target_selected=False
        )
    message = str(info.value)
    assert "big-endian" in message
    assert "little-endian" in message
    assert "compile.compiler" in message


@pytest.mark.parametrize(
    "compile_context",
    [
        pytest.param(CompileContext(), id="default-toolchain"),
        pytest.param(
            CompileContext(gcc_option_tokens=("-mavx2",)), id="unrelated-flag"
        ),
    ],
)
def test_compile_refuses_a_foreign_byte_order_image(
    tmp_path: Path, compile_context: CompileContext
) -> None:
    lib = _elf_header(tmp_path / "lib.so", machine="EM_PPC64", order=OTHER_ORDER)
    with pytest.raises(HeaderToolchainError, match=f"{OTHER_ORDER}-endian"):
        binary_dialect_compile(
            compile_context, lib, lang="c", lang_explicit=False, headers=[]
        )


@pytest.mark.parametrize(
    "compile_context",
    [
        pytest.param(
            CompileContext(gcc_prefix="powerpc64-linux-gnu-"), id="cross-prefix"
        ),
        pytest.param(
            CompileContext(gcc_path="/opt/cross/bin/powerpc64-linux-gnu-gcc"),
            id="cross-path",
        ),
        pytest.param(
            CompileContext(gcc_option_tokens=("--target=powerpc64-linux-gnu",)),
            id="target-triple",
        ),
        pytest.param(
            CompileContext(gcc_options="-mbig-endian"), id="caller-byte-order-flag"
        ),
    ],
)
def test_compile_accepts_a_foreign_byte_order_image_aimed_at_its_target(
    tmp_path: Path, compile_context: CompileContext
) -> None:
    lib = _elf_header(tmp_path / "lib.so", machine="EM_PPC64", order=OTHER_ORDER)
    compiled, implied = binary_dialect_compile(
        compile_context, lib, lang="c", lang_explicit=False, headers=[]
    )
    # No DWARF in the header-only image: nothing is implied, and the caller's
    # context comes back unchanged.
    assert implied == ()
    assert compiled is compile_context


def test_compile_accepts_a_host_byte_order_image(tmp_path: Path) -> None:
    lib = _elf_header(tmp_path / "lib.so", machine="EM_X86_64", order=sys.byteorder)
    compiled, implied = binary_dialect_compile(
        None, lib, lang="c", lang_explicit=False, headers=[]
    )
    assert (compiled, implied) == (None, ())
