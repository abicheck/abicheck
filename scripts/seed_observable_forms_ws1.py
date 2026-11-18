"""ADR-093 WS-1 form codes for the domain and Linux batches (split from seed_observable_at.py).

The catalog value for each form is rebuilt from these entries; the rationale text
is verbatim from the matching taxonomy module (build.py, platform_1.py).
"""

from __future__ import annotations

WS1_FORMS: dict[str, tuple[tuple[tuple[str, ...], ...], str, tuple[str, ...], str]] = {
    # WS-1 domain families (ADR-093, build/numpy/wheel/bundle): the package-scope
    # rationale and the DWARF-only sibling-signature rationale, verbatim from
    # abicheck/model/change_catalog/build.py.
    "B:SCOPE": (
        (("B",),),
        "requires package/set scope: the wheel archive, bundle artifact set or instantiation manifest is the comparison scope, orthogonal to per-artifact evidence; the fact itself is read from the binary (B)",
        (),
        "_OBS_B_SCOPE",
    ),
    "B:ELF:SCOPE": (
        (("B",),),
        "requires package/set scope: the wheel archive, bundle artifact set or instantiation manifest is the comparison scope, orthogonal to per-artifact evidence; the fact itself is read from the binary (B)",
        ("elf",),
        "_OBS_B_ELF_SCOPE",
    ),
    # WS-1 Linux batch (ADR-093): the namesake dynamic-table rationale, verbatim from
    # abicheck/model/change_catalog/platform_1.py.
    "BH:ELF": (
        (("B", "H"),),
        "declared in the public header on both sides; the bare-name export is visible only in the dynamic table (B), so binary-only or DWARF-only runs see a weaker finding",
        ("elf",),
        "_OBS_BH_ELF",
    ),
    "BD:DWARF": (
        (("B", "D"),),
        "the signature or layout of a sibling's type is read from DWARF; the stripped binary alone reports the weaker unverified or no finding",
        (),
        "_OBS_BD_DWARF",
    ),
}
