"""Binary fixture preconditions for catalog cases.

A catalog case's premise sometimes depends on a property the *toolchain* must
actually put into the built binary — e.g. case179 needs v1 to carry the x86
``GNU_PROPERTY_X86_FEATURE_1_AND`` IBT+SHSTK bits, which a linker silently ANDs
away when the host's crt objects lack the note. When the built fixture does not
exhibit its premise, comparing it proves nothing about the detector, so the
validator SKIPs with ``fixture precondition unmet`` instead of FAILing.

A ``ground_truth.json`` verdict entry declares::

    "fixture_preconditions": [
      {"side": "v1", "property": "elf_gnu_property", "includes": ["IBT", "SHSTK"]},
      {"side": "v2", "property": "elf_gnu_property", "excludes": ["IBT", "SHSTK"]}
    ]

``property`` names a reader in :data:`PROPERTY_READERS` returning the set of
tokens the built binary exhibits; add a reader there for a new platform
property (RELRO, stack canary, exec-stack, ...).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from abicheck.extract.elf_notes import decode_gnu_property_desc, iter_gnu_property_descs

SKIP_PREFIX = "SKIP:fixture precondition unmet"


def _elf_gnu_property(path: Path) -> frozenset[str]:
    """Tokens decoded from the ELF ``.note.gnu.property`` (IBT/SHSTK/BTI/PAC/ISA)."""
    from elftools.elf.elffile import ELFFile

    with path.open("rb") as fh:
        elf = ELFFile(fh)
        align = 8 if elf.elfclass == 64 else 4
        tokens: set[str] = set()
        for desc in iter_gnu_property_descs(elf):
            tokens |= decode_gnu_property_desc(desc, elf.little_endian, align)
        return frozenset(tokens)


PROPERTY_READERS: dict[str, Callable[[Path], frozenset[str]]] = {
    "elf_gnu_property": _elf_gnu_property,
}


def unmet(entry: dict, v1: Path | None, v2: Path | None) -> str | None:
    """Return a ``SKIP:`` reason for the first unmet precondition, else ``None``.

    An unreadable binary or unknown property is reported as unmet (SKIP) —
    never as a FAIL, because the fixture's premise is then unestablished.
    """
    binaries = {"v1": v1, "v2": v2}
    for spec in entry.get("fixture_preconditions", ()):
        side, prop = spec.get("side"), spec.get("property")
        path, reader = binaries.get(side), PROPERTY_READERS.get(prop)
        label = f"{side} {prop}"
        if path is None or reader is None:
            return f"{SKIP_PREFIX}: {label}: unknown side/property or no binary"
        try:
            tokens = reader(path)
        except Exception as exc:  # noqa: BLE001 — any read failure is "unestablished"
            return f"{SKIP_PREFIX}: {label}: unreadable ({type(exc).__name__}: {exc})"
        missing = set(spec.get("includes", ())) - tokens
        present = set(spec.get("excludes", ())) & tokens
        if missing or present:
            return (
                f"{SKIP_PREFIX}: {label} has {sorted(tokens)}; "
                f"missing {sorted(missing)}, unexpected {sorted(present)}"
            )
    return None
