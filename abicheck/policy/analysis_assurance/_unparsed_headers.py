# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Assurance gap for public headers the L2 parse dropped.

A multi-header L2 parse keeps going when an individual header cannot
compile: clang's ``#error`` retry and the castxml unparseable-header
fallback both drop the failing header and parse the rest, recording the
dropped paths on the snapshot under two keys (``model.header_parse_coverage``
and ``model.header_exclusion_record``); both are read here. The
declarations only those headers provide were then never observed, so the
run may not claim the header surface it was asked to check: absence of a
finding there is not evidence of compatibility. A coverage gap, never a
verdict, so the rollup reads ``partial``.

Not a coverage-warning provider: ``confidence`` already emits the
per-side reduced-evidence warning for the same record.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...model.header_exclusion_record import excluded_headers_from_toolchain
from ...model.header_parse_coverage import header_parse_excluded
from ._unobserved_finding import Side, UnobservedFinding

if TYPE_CHECKING:
    from ...model import AbiSnapshot

__all__ = ["UNPARSED_HEADERS_DIMENSION", "unparsed_header_findings"]

UNPARSED_HEADERS_DIMENSION = "public headers (dropped from the L2 parse)"


def _dropped(snap: AbiSnapshot) -> tuple[str, ...]:
    """Both frontends' drop records, in record order, de-duplicated:
    clang's ``#error`` retry and castxml's unparseable-header fallback write
    the same fact under two ``ast_toolchain`` keys."""
    castxml = excluded_headers_from_toolchain(getattr(snap, "ast_toolchain", None))
    return tuple(dict.fromkeys((*header_parse_excluded(snap), *castxml)))


def unparsed_header_findings(
    old: AbiSnapshot | None, new: AbiSnapshot
) -> list[UnobservedFinding]:
    """One finding per side whose L2 header parse dropped a header."""
    sides: tuple[tuple[Side, AbiSnapshot | None], ...] = (("old", old), ("new", new))
    findings: list[UnobservedFinding] = []
    for label, snap in sides:
        if snap is None:
            continue
        dropped = _dropped(snap)
        if not dropped:
            continue
        findings.append(
            UnobservedFinding(
                UNPARSED_HEADERS_DIMENSION,
                label,
                f"{UNPARSED_HEADERS_DIMENSION}: the {label} side's L2 header "
                f"parse dropped {len(dropped)} header(s) it could not compile "
                f"({', '.join(dropped)}); declarations only they provide were "
                "not observed, so header-level changes there are not assessed",
            )
        )
    return findings
