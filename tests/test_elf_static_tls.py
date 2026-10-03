"""Static-TLS evidence from dynamic relocations (`extract/elf_static_tls.py`).

Bug class: ``has_static_tls`` read only the linker's ``DF_STATIC_TLS``
summary, which AArch64 GNU ld does not write, so an initial-exec library was
reported as dlopen-safe there (case171 ``NO_CHANGE`` on AArch64). The
invariant these tests state is architecture-independent: a shared object needs
static TLS exactly when it carries a TP-relative dynamic relocation, and the
TLS access model decides which, whatever the linker chose to summarize.

Two oracles, neither of them the module's own table:

* the relocation numbers are resolved *by name* from the system's glibc
  ``elf.h`` (the ABI documents), and the dynamic-TLS relocations of the same
  architectures must never be in the set;
* real builds of every ``-ftls-model`` are judged by the model's documented
  semantics (initial-exec / local-exec need a static slot, global-/local-
  dynamic and TLS descriptors do not), on the host compiler and, when one is
  installed, an AArch64 cross compiler -- the target whose linker omits the
  flag.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from abicheck.elf_metadata import parse_elf_metadata
from abicheck.extract.elf_static_tls import (
    STATIC_TLS_RELOCATION_TYPES,
    has_static_tls_relocation,
)

ELF_H = Path("/usr/include/elf.h")

#: Per machine: the relocations that resolve a static-TLS offset, by ABI name.
_STATIC_BY_NAME: dict[str, tuple[str, ...]] = {
    "EM_X86_64": ("R_X86_64_TPOFF64", "R_X86_64_TPOFF32"),
    "EM_386": ("R_386_TLS_TPOFF", "R_386_TLS_TPOFF32"),
    "EM_AARCH64": ("R_AARCH64_TLS_TPREL",),
    "EM_ARM": ("R_ARM_TLS_TPOFF32",),
    "EM_PPC64": ("R_PPC64_TPREL64",),
    "EM_PPC": ("R_PPC_TPREL32",),
    "EM_S390": ("R_390_TLS_TPOFF",),
    "EM_RISCV": ("R_RISCV_TLS_TPREL32", "R_RISCV_TLS_TPREL64"),
    "EM_LOONGARCH": ("R_LARCH_TLS_TPREL32", "R_LARCH_TLS_TPREL64"),
}

#: Per machine: relocation-name prefixes whose dynamic-TLS members
#: (module id / DTP offset / descriptor) must never count as static.
_ELF_H_PREFIX = {
    "EM_X86_64": "R_X86_64_",
    "EM_386": "R_386_",
    "EM_AARCH64": "R_AARCH64_",
    "EM_ARM": "R_ARM_",
    "EM_PPC64": "R_PPC64_",
    "EM_PPC": "R_PPC_",
    "EM_S390": "R_390_",
    "EM_RISCV": "R_RISCV_",
    "EM_LOONGARCH": "R_LARCH_",
}
_DYNAMIC_TLS_RE = re.compile(r"(DTPMOD|DTPOFF|DTPREL|TLSDESC)")


def _elf_h_relocations() -> dict[str, int]:
    if not ELF_H.is_file():
        pytest.skip("glibc elf.h not available as an oracle")
    out: dict[str, int] = {}
    for m in re.finditer(
        r"^#define\s+(R_[A-Z0-9_]+)\s+(\d+)\b", ELF_H.read_text(), re.M
    ):
        out[m.group(1)] = int(m.group(2))
    return out


def test_table_covers_exactly_the_documented_architectures() -> None:
    assert set(STATIC_TLS_RELOCATION_TYPES) == set(_STATIC_BY_NAME)


@pytest.mark.parametrize("machine", sorted(_STATIC_BY_NAME))
def test_table_matches_glibc_elf_h_by_name(machine: str) -> None:
    relocs = _elf_h_relocations()
    expected = frozenset(relocs[name] for name in _STATIC_BY_NAME[machine])
    assert STATIC_TLS_RELOCATION_TYPES[machine] == expected


@pytest.mark.parametrize("machine", sorted(_ELF_H_PREFIX))
def test_no_dynamic_tls_relocation_counts_as_static(machine: str) -> None:
    relocs = _elf_h_relocations()
    prefix = _ELF_H_PREFIX[machine]
    dynamic = {
        value
        for name, value in relocs.items()
        if name.startswith(prefix) and _DYNAMIC_TLS_RE.search(name)
    }
    assert dynamic, f"oracle found no dynamic-TLS relocations for {machine}"
    assert not (STATIC_TLS_RELOCATION_TYPES[machine] & dynamic)


# -- Section-selection contract, over a duck-typed ELFFile ------------------


def _fake_elf(e_type: str, machine: str, sections: list[tuple[str, str, list[int]]]):
    """sections: (sh_type, linked sh_type or "" for no link, reloc types)."""
    table = [SimpleNamespace(header={"sh_type": "SHT_NULL"})]
    for sh_type, linked, types in sections:
        link = 0
        if linked:
            table.append(SimpleNamespace(header={"sh_type": linked}))
            link = len(table) - 1
        table.append(
            SimpleNamespace(
                header={"sh_type": sh_type, "sh_link": link},
                iter_relocations=lambda types=types: [
                    {"r_info_type": t} for t in types
                ],
            )
        )
    return SimpleNamespace(
        header={"e_type": e_type, "e_machine": machine},
        iter_sections=lambda: iter(table),
        get_section=lambda i: table[i],
    )


@pytest.mark.parametrize("machine", sorted(STATIC_TLS_RELOCATION_TYPES))
@pytest.mark.parametrize("sh_type", ["SHT_RELA", "SHT_REL"])
@pytest.mark.parametrize("e_type", ["ET_DYN", "ET_REL", "ET_EXEC"])
@pytest.mark.parametrize("linked", ["SHT_DYNSYM", "SHT_SYMTAB", ""])
def test_section_selection(
    machine: str, sh_type: str, e_type: str, linked: str
) -> None:
    static_type = min(STATIC_TLS_RELOCATION_TYPES[machine])
    elf = _fake_elf(e_type, machine, [(sh_type, linked, [static_type])])
    expected = e_type == "ET_DYN" and linked != "SHT_SYMTAB"
    assert has_static_tls_relocation(elf) is expected


def test_unknown_machine_and_unrelated_relocations_are_not_evidence() -> None:
    assert not has_static_tls_relocation(
        _fake_elf("ET_DYN", "EM_UNKNOWN", [("SHT_RELA", "SHT_DYNSYM", [18])])
    )
    every_other = [
        t for t in range(1, 2048) if t not in STATIC_TLS_RELOCATION_TYPES["EM_X86_64"]
    ]
    assert not has_static_tls_relocation(
        _fake_elf("ET_DYN", "EM_X86_64", [("SHT_RELA", "SHT_DYNSYM", every_other)])
    )


# -- Real builds: every TLS access model, host and AArch64 -------------------

_SRC = "__thread int counter;\nint bump(void) { return ++counter; }\n"
#: The model's documented semantics: which models need a static TLS slot.
_NEEDS_STATIC = {
    "global-dynamic": False,
    "local-dynamic": False,
    "initial-exec": True,
    "local-exec": True,
}
_COMPILERS = [cc for cc in ("gcc", "aarch64-linux-gnu-gcc") if shutil.which(cc)]


def _has_any_tls_dynamic_evidence(so: Path) -> bool:
    """Independent of the module: readelf's view of TLS relocations / DF_STATIC_TLS."""
    relocs = subprocess.run(
        ["readelf", "-rW", str(so)], capture_output=True, text=True
    ).stdout
    dynamic = subprocess.run(
        ["readelf", "-dW", str(so)], capture_output=True, text=True
    ).stdout
    return bool(re.search(r"R_\w*(TLS|TPOFF|TPREL)", relocs)) or "STATIC_TLS" in dynamic


@pytest.mark.integration
@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="builds and reads ELF shared objects; the host toolchain emits PE/Mach-O elsewhere",
)
@pytest.mark.skipif(
    not shutil.which("readelf"), reason="readelf required as the oracle"
)
@pytest.mark.skipif(not _COMPILERS, reason="no C compiler")
@pytest.mark.parametrize("cc", _COMPILERS)
def test_every_tls_model_is_classified_by_its_semantics(
    cc: str, tmp_path: Path
) -> None:
    built: dict[str, bool] = {}
    for model, needs_static in _NEEDS_STATIC.items():
        so = tmp_path / f"lib_{model}.so"
        src = tmp_path / "tls.c"
        src.write_text(_SRC)
        r = subprocess.run(
            [
                cc,
                "-shared",
                "-fPIC",
                "-O1",
                f"-ftls-model={model}",
                str(src),
                "-o",
                str(so),
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            # local-exec in a shared object is a link error on some targets;
            # that is the toolchain refusing the model, not a classification.
            continue
        if so.read_bytes()[:4] != b"\x7fELF":
            # A host toolchain whose shared objects are not ELF (MinGW emits
            # PE) has no ELF TLS relocations to classify.
            pytest.skip(f"{cc} does not produce ELF shared objects")
        built[model] = needs_static
        meta = parse_elf_metadata(so)
        assert meta.has_tls_symbols
        if model == "local-exec" and not _has_any_tls_dynamic_evidence(so):
            # AArch64 GNU ld links local-exec into a shared object with the TP
            # offset fixed at link time: no TLS dynamic relocation and no
            # DF_STATIC_TLS, so nothing in the binary records the requirement
            # (docs/contribute/known-gaps.md). The fact must then stay False --
            # no evidence, not a fabricated positive.
            assert meta.has_static_tls is False, (cc, model)
            continue
        assert meta.has_static_tls is needs_static, (cc, model)
    # The two models the bug is about must both have been exercised.
    assert {"global-dynamic", "initial-exec"} <= set(built), built
