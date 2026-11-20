"""Binary facts an independent tool reads off two built libraries (not abicheck).

The rebuild operators' ground truth can depend on a fact of the built
artifact, which the expectation cannot know from the configuration alone: an
``-O2`` rebuild drops ``__stack_chk_fail`` only when no frame in the library
needs a canary. These facts are read with the platform's binutils (``nm``) and
are the execution-oracle counterpart of a ``BinaryLicence``
(``model/expect.py``): a finding the verdict is allowed to carry only when this
oracle confirms the fact behind it.

The oracle never fails a cell. When a fact cannot be read (an unsupported
format, a missing or failing ``nm``) :func:`binary_facts` returns no facts at
all, and every licence then keeps the strict ceiling. abiforge does not import
abicheck here: the checker under test cannot influence the oracle.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

#: The protector's runtime entry point: an ``-fstack-protector`` frame calls it.
STACK_CHK_FAIL = "__stack_chk_fail"

_ELF_MAGIC = b"\x7fELF"
_MACHO_MAGICS = (
    b"\xfe\xed\xfa\xce",  # 32-bit, big-endian
    b"\xce\xfa\xed\xfe",  # 32-bit, little-endian
    b"\xfe\xed\xfa\xcf",  # 64-bit, big-endian
    b"\xcf\xfa\xed\xfe",  # 64-bit, little-endian
    b"\xca\xfe\xba\xbe",  # fat (universal)
)
_PE_MAGIC = b"MZ"


def library_format(lib: Path) -> str:
    """``"elf"``, ``"macho"``, ``"pe"`` or ``"unknown"``, from the file's first bytes."""
    with lib.open("rb") as fh:
        head = fh.read(4)
    if head.startswith(_ELF_MAGIC):
        return "elf"
    if head in _MACHO_MAGICS:
        return "macho"
    if head.startswith(_PE_MAGIC):
        return "pe"
    return "unknown"


def _nm_argv(fmt: str, lib: Path) -> list[str] | None:
    if fmt == "elf":
        # The dynamic table: what the loader resolves at run time.
        return ["nm", "-D", "--undefined-only", "-P", str(lib)]
    if fmt == "macho":
        # Mach-O has no -D; nm -u lists the undefined symbols.
        return ["nm", "-u", str(lib)]
    return None  # PE: import names need a PE reader, not nm; not read here


def undefined_symbols(lib: Path) -> frozenset[str] | None:
    """The undefined (imported) symbol names of *lib*, or ``None`` when unavailable.

    Versions are stripped. Mach-O C symbols carry one extra leading ``_``,
    which is removed so the names match the ELF spelling. ``None`` covers an
    unsupported format, a missing ``nm``, and a non-zero exit: never an empty
    set, which would read as "nothing imported".
    """
    fmt = library_format(lib)
    argv = _nm_argv(fmt, lib)
    if argv is None:
        return None
    try:
        out = subprocess.run(argv, check=True, capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    names: set[str] = set()
    for line in out.splitlines():
        fields = line.split()
        if not fields:
            continue
        # ELF -P: "name U" (name first); Mach-O nm -u: "U name" (name last).
        name = (fields[-1] if fmt == "macho" else fields[0]).split("@", 1)[0]
        if fmt == "macho" and name.startswith("_"):
            name = name[1:]
        names.add(name)
    return frozenset(names)


def binary_facts(old: Path, new: Path) -> dict[str, bool]:
    """The named facts one rebuild pair shows; ``{}`` when they cannot be read.

    An empty result means "no fact", so every ``BinaryLicence`` keeps its strict
    ceiling. A fact is present only when both sides were read.
    """
    old_undef = undefined_symbols(old)
    new_undef = undefined_symbols(new)
    if old_undef is None or new_undef is None:
        return {}
    return {
        # The old build calls the canary check and the new one no longer does.
        "stack_chk_fail_dropped": STACK_CHK_FAIL in old_undef
        and STACK_CHK_FAIL not in new_undef,
    }


def exported_functions(lib: Path) -> dict[str, str] | None:
    """*lib*'s defined exported functions as ``{symbol: demangled}``, or None.

    ELF only (``nm -D --defined-only``, text symbols ``T``/``W`` and GNU
    indirect ``i``); demangling is ``c++filt``'s, so a C symbol maps to
    itself. ``None`` covers another format, a missing tool and a failure:
    never an empty mapping, which would read as "nothing exported".
    """
    if library_format(lib) != "elf":
        return None
    try:
        out = subprocess.run(
            ["nm", "-D", "--defined-only", "-P", str(lib)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    syms: list[str] = []
    for line in out.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1] in ("T", "W", "i"):
            syms.append(fields[0].split("@", 1)[0])
    if not syms:
        return {}
    try:
        dem = subprocess.run(
            ["c++filt"],
            input="\n".join(syms) + "\n",
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        return None
    if len(dem) != len(syms):
        return None
    return dict(zip(syms, dem, strict=True))
