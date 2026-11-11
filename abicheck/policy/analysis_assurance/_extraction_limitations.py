# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Roll up explicit extractor limitations preserved in dump provenance.

An experimental extractor can emit useful comparable facts while declaring
which contracts it did not model. Persist those diagnostics under
``dump_provenance.extraction_limitations`` so storing a snapshot cannot
turn a partial extraction into a complete assurance claim.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._unobserved_finding import Side, UnobservedFinding

if TYPE_CHECKING:
    from ...model import AbiSnapshot

EXTRACTION_LIMITATIONS_DIMENSION = "contract evidence not modelled by the extractor"


def extraction_limitation_findings(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> list[UnobservedFinding]:
    findings: list[UnobservedFinding] = []
    pairs: tuple[tuple[Side, AbiSnapshot | None], ...] = (("old", old), ("new", new))
    for side, snap in pairs:
        if snap is None or not snap.dump_provenance:
            continue
        raw = snap.dump_provenance.get("extraction_limitations", [])
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raw = [
                "invalid extraction_limitations metadata; extractor coverage is unknown"
            ]
        for reason in dict.fromkeys(raw):
            if reason:
                findings.append(
                    UnobservedFinding(
                        EXTRACTION_LIMITATIONS_DIMENSION,
                        side,
                        f"{side} extraction limitation: {reason}",
                    )
                )
    return findings
