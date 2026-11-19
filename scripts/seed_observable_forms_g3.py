"""ADR-093 G3 form codes for the header-graph and DWARF-deleted families (split from seed_observable_at.py).

The catalog value for each form is rebuilt from these entries; the rationale text
matches the taxonomy module that emits the kind.
"""

from __future__ import annotations

G3_FORMS: dict[str, tuple[tuple[tuple[str, ...], ...], str, tuple[str, ...], str]] = {
    "BH": (
        (("B", "H"),),
        "declared in the public header and present in the export table",
        (),
        "_OBS_BH",
    ),
    "D:PRESENCE": (
        (("B", "D"),),
        "DWARF presence on the old side: only a debug-info read sees it",
        (),
        "_OBS_D_PRESENCE",
    ),
    "D:DELETED": ((("B", "D"),), "deleted member: the DWARF DIE carries DW_AT_deleted, which the export table cannot show; without debug info the binary pair reports it as func_removed_elf_only", (), "_OBS_D_DELETED"),
    "BH:PE": (
        (("B", "H"),),
        "declared in the public header and present in the export table",
        ("pe",),
        "_OBS_BH_PE",
    ),
    "BH:ELFPE": (
        (("B", "H"),),
        "declared in the public header and present in the export table",
        ("elf", "pe"),
        "_OBS_BH_ELFPE",
    ),
}
