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

"""``compare``'s Phase 7 ``debug.pdb_path`` resolution (one-comparison-
product.md §4.1's CONFIG row) -- split out of ``cli_compare_helpers.py``
purely to keep that file under its architecture ``no_growth`` baseline
(AGENTS.md: "move responsibility instead of raising the baseline"),
mirroring ``dump_debug_config.py``'s identical split for ``dump``.

``--pdb-path`` is gone from ``compare``'s CLI entirely -- ``debug.pdb_path``
is its only source now, exactly as it has been for ``dump`` since Phase 7c
(ADR-037 D8.1: the two commands share one debug context and must not
drift). Unlike ``dump``, ``compare`` has two operands: the flag's per-side
``old=``/``new=`` scoping has no config spelling and is not reinvented as
one. Only PE binaries ever consult a PDB (``service_dump_native_pe.py``'s
own ``_extract_pdb_debug``) -- a stored JSON snapshot, an ELF/Mach-O
binary, or a non-PE live extraction never reads ``pdb_path`` at all -- so a
configured value only risks sharing one file across two *different*
binaries when **both** operands are live PE inputs. Rejected outright in
that one case (Codex review, PR #1180, "Preserve per-side PDB selection");
accepted and applied normally otherwise, including the common
stored-baseline-vs-live-candidate shape (Codex review, fresh evidence,
"Allow PDB config when only one operand is live") where only one side can
possibly consume it. ``--debug-root old=``/``new=`` is the safe two-live-
PE-binaries alternative: ``debug_resolver``'s ``pdb_in_root`` already
searches a debug root for a PDB matched by that side's own binary stem.
"""

from __future__ import annotations

from pathlib import Path

import click

from ...workflows.extraction import detect_binary_format


def resolve_and_reject_shared_pdb_path(
    cfg_pdb_path: str | None, *, old_input: Path, new_input: Path
) -> Path | None:
    """Resolve ``debug.pdb_path``, rejecting it only when both sides are PE.

    Two different PE binaries silently reading the same PDB is not a
    degraded case -- ``locate_pdb`` reports it found a PDB either way -- it
    can hide a real type/layout change behind a false clean result, exactly
    the "manufacture a clean compatibility claim from evidence that doesn't
    support it" failure AGENTS.md's weaker-evidence rule exists to prevent.
    But a PDB is only ever read while extracting a *PE* binary, so a
    non-PE side (a stored JSON snapshot, an ELF/Mach-O binary, a raw
    ``--sources`` extraction) never touches this value regardless of what
    it's set to -- there is no sharing risk unless both operands are PE.
    """
    if not cfg_pdb_path:
        return None
    pdb_path = Path(cfg_pdb_path)
    if (
        detect_binary_format(old_input) == "pe"
        and detect_binary_format(new_input) == "pe"
    ):
        raise click.UsageError(
            "debug.pdb_path names one PDB file shared by both sides of a "
            "two-operand compare: without a per-side old=/new= spelling "
            "(removed from the CLI, and not reinvented as a config key), "
            "applying it to two different PE binaries risks silently "
            "reading the same debug info for both and reporting a false "
            "clean result. Use --debug-info old=<dir>/new=<dir> instead -- "
            "it resolves each side's own PDB by that side's binary name."
        )
    return pdb_path
