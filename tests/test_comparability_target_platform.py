# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The comparability contract records the parse's target platform (ADR-050 D1).

Known gap "The comparability contract never records the target platform":
``dumper_contract._attach_extraction_contract`` never passed
``target_triple``/``pointer_width``/``endianness``, so ``-m32``/``--target=``
on one side only was judged comparable. These tests pin both halves of the
fix: the fields are recorded from the frontend, and the gate treats an
unrecorded side (every legacy baseline) as unknown, never a mismatch.
"""

from __future__ import annotations

import itertools
import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.comparability import (
    check_contracts_comparable,
    compute_extraction_contract,
)
from abicheck.dumper_contract import _attach_extraction_contract
from abicheck.elf_metadata import ElfMetadata
from abicheck.errors import ProfileMismatchError
from abicheck.extract import target_platform_probe
from abicheck.extract.target_platform import (
    contract_platform_fields,
    effective_triple,
    platform_from_macros,
    target_platform_metadata,
)
from abicheck.model import AbiSnapshot

_X64 = ("x86_64-linux-gnu", 64, "little")
_I686 = ("i686-linux-gnu", 32, "little")
_ELF = {
    "x64": ElfMetadata(machine="EM_X86_64", elf_class=64, ei_data="ELFDATA2LSB"),
    "i386": ElfMetadata(machine="EM_386", elf_class=32, ei_data="ELFDATA2LSB"),
}


def _side(platform, elf_key: str) -> AbiSnapshot:
    triple, width, endian = platform if platform is not None else (None,) * 3
    contract = compute_extraction_contract(
        l2_frontend_ran=True,
        target_triple=triple,
        pointer_width=width,
        endianness=endian,
    )
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        contract=contract,
        elf=_ELF[elf_key],
    )


# {recorded, unrecorded} per side x {same, different} x {binary differs, same}.
_MATRIX = list(
    itertools.product(
        (True, False),  # old recorded
        (True, False),  # new recorded
        (False, True),  # recorded platforms differ
        (False, True),  # binaries differ
    )
)


@pytest.mark.parametrize(
    ("old_rec", "new_rec", "platform_differs", "binary_differs"), _MATRIX
)
def test_platform_gate_matrix(old_rec, new_rec, platform_differs, binary_differs):
    old_platform = _X64
    new_platform = _I686 if platform_differs else _X64
    old = _side(old_platform if old_rec else None, "x64")
    new = _side(new_platform if new_rec else None, "i386" if binary_differs else "x64")
    # Oracle, stated independently of the gate: only two RECORDED values can
    # disagree, and a disagreement the binaries do not corroborate is a
    # misconfigured extraction. Anything unrecorded is unknown, never a
    # mismatch.
    must_refuse = old_rec and new_rec and platform_differs and not binary_differs
    if must_refuse:
        with pytest.raises(ProfileMismatchError):
            check_contracts_comparable(old, new)
    else:
        assert check_contracts_comparable(old, new) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "#define __SIZEOF_POINTER__ 8\n#define __BYTE_ORDER__ __ORDER_LITTLE_ENDIAN__\n",
            (64, "little"),
        ),
        (
            "#define __BYTE_ORDER__ __ORDER_BIG_ENDIAN__\n#define __SIZEOF_POINTER__ 4\n",
            (32, "big"),
        ),
        ("#define __SIZEOF_POINTER__ 2\n", (16, None)),
        ("#define __BYTE_ORDER__ __ORDER_PDP_ENDIAN__\n", (None, None)),
        ("", (None, None)),
        ("#define __SIZEOF_POINTER__ x\n", (None, None)),
    ],
)
def test_platform_from_macros(text, expected):
    assert platform_from_macros(text) == expected


@pytest.mark.parametrize(
    ("resolved", "args", "width", "expected"),
    [
        # gcc -dumpmachine ignores -m32: re-spelled for the probed width.
        ("x86_64-linux-gnu", ["-m32"], 32, "i686-linux-gnu"),
        ("x86_64-linux-gnu", [], 64, "x86_64-linux-gnu"),
        ("i686-linux-gnu", ["-m64"], 64, "x86_64-linux-gnu"),
        ("s390x-linux-gnu", ["-m31"], 32, "s390-linux-gnu"),
        # clang's own resolution already honours -m32: kept as is.
        ("i386-pc-linux-gnu", ["-m32"], 32, "i386-pc-linux-gnu"),
        # unknown family: kept (pointer_width still records the switch).
        ("aarch64-linux-gnu", ["-mabi=ilp32"], 32, "aarch64-linux-gnu"),
        # explicit target flag wins over everything.
        (
            "x86_64-linux-gnu",
            ["--target=armv7-linux-gnueabihf"],
            32,
            "armv7-linux-gnueabihf",
        ),
        ("x86_64-linux-gnu", ["-target", "aarch64-linux-gnu"], 64, "aarch64-linux-gnu"),
        (None, [], 64, None),
    ],
)
def test_effective_triple(resolved, args, width, expected):
    assert effective_triple(resolved, args, width) == expected


@pytest.mark.parametrize(
    "platform", [_X64, _I686, ("", None, None), ("ppc-x", 32, "big")]
)
def test_metadata_round_trips_into_contract_fields(platform):
    triple, width, endian = platform
    fields = contract_platform_fields(target_platform_metadata(triple, width, endian))
    assert fields == (triple or None, width, endian)


def _attach(tmp_path: Path, ast_toolchain: dict[str, str], from_headers: bool = True):
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=from_headers,
        ast_toolchain=ast_toolchain,
    )
    _attach_extraction_contract(
        snap,
        headers=[tmp_path / "api.h"],
        extra_includes=None,
        gcc_options=None,
        gcc_option_tokens=(),
        lang=None,
        public_headers=None,
        public_header_dirs=None,
    )
    return snap.contract


@pytest.mark.parametrize("platform", [_X64, _I686])
def test_attach_extraction_contract_records_platform(tmp_path, platform):
    contract = _attach(tmp_path, target_platform_metadata(*platform))
    assert contract is not None
    triple, width, endian = platform
    assert contract.profile_fields["target_triple"] == triple
    assert contract.profile_fields["pointer_width"] == str(width)
    assert contract.profile_fields["endianness"] == endian


def test_attach_extraction_contract_legacy_toolchain_stays_unrecorded(tmp_path):
    contract = _attach(tmp_path, {"compiler_target_triple": "x86_64-linux-gnu"})
    assert contract is not None
    for key in ("target_triple", "pointer_width", "endianness"):
        assert contract.profile_fields[key] == ""


@pytest.mark.parametrize(
    ("producer", "dialect", "args", "clang_triple", "expected"),
    [
        # castxml: emulated gcc probed with the forwarded -m32; -dumpmachine
        # (compiler_target_triple) re-spelled for the probed width.
        ("castxml", "gnu", "-m32", None, ("i686-linux-gnu", "32")),
        ("castxml", "gnu", "", None, ("x86_64-linux-gnu", "64")),
        # clang: its own flag-aware triple is recorded.
        ("clang", "gnu", "-m32", "i386-pc-linux-gnu", ("i386-pc-linux-gnu", "32")),
    ],
)
def test_recorded_target_platform_wiring(
    monkeypatch, producer, dialect, args, clang_triple, expected
):
    seen: list[tuple[str, tuple[str, ...]]] = []

    def fake_probe(cc, probe_args, cxx):
        seen.append((cc, probe_args))
        return (32 if "-m32" in probe_args else 64), "little"

    monkeypatch.setattr(target_platform_probe, "_probe_target_platform", fake_probe)
    meta = {
        "compiler_selected": "/usr/bin/gcc",
        "compiler_target_triple": "x86_64-linux-gnu",
    }
    out = target_platform_probe.recorded_target_platform(
        meta, producer, "/usr/bin/clang", dialect, args or None, (), clang_triple, False
    )
    assert (out["target_triple_effective"], out["target_pointer_width"]) == expected
    assert out["target_endianness"] == "little"
    assert seen[0][0] == ("/usr/bin/clang" if producer == "clang" else "/usr/bin/gcc")


def test_recorded_target_platform_msvc_records_nothing(monkeypatch):
    monkeypatch.setattr(
        target_platform_probe,
        "_probe_target_platform",
        lambda *a: pytest.fail("MSVC has no -dM probe"),
    )
    assert (
        target_platform_probe.recorded_target_platform(
            {"compiler_selected": "cl.exe"},
            "castxml",
            "castxml",
            "msvc",
            None,
            (),
            None,
            False,
        )
        == {}
    )


def _gcc_supports_m32() -> bool:
    gcc = shutil.which("gcc")
    if gcc is None:
        return False
    r = subprocess.run(
        [gcc, "-m32", "-E", "-dM", "-x", "c", "-"],
        input="",
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode == 0


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("castxml") is None, reason="castxml not installed")
def test_real_dump_m32_on_one_side_is_refused(tmp_path):
    """End to end through ``dumper.dump``: the same x86-64 binary, headers
    parsed with ``-m32`` on one side only -- the misconfiguration the gate's
    platform-identity rule documents -- is now refused, while two identical
    dumps stay comparable."""
    if not _gcc_supports_m32():
        pytest.skip("gcc cannot preprocess for -m32 here")
    from abicheck.dumper import dump

    header = tmp_path / "foo.h"
    header.write_text("struct S { long a; void *p; };\nint f(struct S *);\n")
    src = tmp_path / "foo.c"
    src.write_text('#include "foo.h"\nint f(struct S *s) { return s != 0; }\n')
    lib = tmp_path / "libfoo.so"
    subprocess.run(["gcc", "-shared", "-fPIC", "-o", str(lib), str(src)], check=True)
    native = dump(lib, [header])
    m32 = dump(lib, [header], gcc_options="-m32")
    assert native.contract is not None and m32.contract is not None
    assert native.contract.profile_fields["pointer_width"] == "64"
    assert m32.contract.profile_fields["pointer_width"] == "32"
    assert check_contracts_comparable(native, native) is None
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(native, m32)
