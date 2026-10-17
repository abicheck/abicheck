"""Generic-name rules for the architecture gate.

Generic module names (``utils.py``) and generic ledger owners (the shared
``abicheck maintainers`` placeholder) name no stable responsibility. A pure
leaf with no I/O, split out of ``check_architecture.py`` to keep that gate
under its own grandfathered file-size record.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

GENERIC_NAMES = frozenset(
    {"helpers.py", "utils.py", "common.py", "misc.py", "base.py", "extra.py", "more.py"}
)
GENERIC_SUFFIXES = ("_helpers", "_utils", "_lib")

#: Owner values that name no responsibility area. A ledger entry names the area
#: its path belongs to (a ``modules.yaml`` layer, ``buildsource``,
#: ``legacy-root`` or ``tests``), never this shared placeholder.
GENERIC_LEDGER_OWNERS = frozenset({"abicheck maintainers"})


def generic_owner_messages(
    debt: Mapping[str, Any], dispositions: Mapping[str, Any]
) -> list[str]:
    """Return one message per ledger entry whose owner is a generic placeholder."""
    ledgers = (
        ("architecture/debt.yaml files", debt.get("files")),
        (
            "architecture/debt.yaml dependency_direction_exceptions",
            debt.get("dependency_direction_exceptions"),
        ),
        ("architecture/dispositions.yaml modules", dispositions.get("modules")),
    )
    messages: list[str] = []
    for label, records in ledgers:
        if not isinstance(records, list):
            continue
        for index, record in enumerate(records):
            owner = record.get("owner") if isinstance(record, dict) else None
            if (
                isinstance(owner, str)
                and owner.strip().casefold() in GENERIC_LEDGER_OWNERS
            ):
                messages.append(
                    f"{label}[{index}].owner: {owner!r} is a generic placeholder; "
                    "name the responsibility area the entry's path belongs to"
                )
    return messages
