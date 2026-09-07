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

"""ADR-061 gap E (closure package 5): the one piece of ``AbiSnapshot`` load
that is evidence *derivation*, not decoding.

``storage``'s own ``may_import: [model]`` cannot admit a call into
``extract`` (``python_ext.detect_python_extension()`` infers a fact from
exported-symbol/import evidence — real extraction logic, not a fact
lookup), so this backfill lives here, in ``workflows`` (``may_import``
includes both ``storage`` and ``extract``), as a post-load step
``serialization.snapshot_from_dict`` calls *after* the pure storage decode
has built the ``AbiSnapshot`` — the same "decide explicitly where
evidence-derived backfill runs" resolution the gap's completion test asks
for. ``serialization.py`` itself stays unclassified (a `public_root_surfaces`
compatibility facade, same treatment `policy_file.py` gets), so it can call
into `workflows` here without an architecture-check violation.

Every direct `snapshot_from_dict()` caller in this repository (`bundle_facts.py`,
`bundle_facts_serialization.py`, `probe_harness.py`, `workflows/input_resolution.py`,
and `serialization.load_snapshot` itself) was audited before this move: none
of them ever needed the backfill skipped, so this stays a mandatory step of
`snapshot_from_dict`, not an opt-in the caller chooses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..model import AbiSnapshot


def backfill_python_ext_from_evidence(snap: AbiSnapshot, d: dict[str, Any]) -> None:
    """Derive ``snap.python_ext`` for a legacy document that never recorded it.

    ``d`` is the already-unwrapped, flat snapshot document
    (``serialization.snapshot_from_dict``'s own input shape, post
    sectioned-document unwrapping) that produced ``snap``. Mutates
    ``snap.python_ext`` in place; a no-op whenever the document already
    carries a ``python_ext`` key (including an explicit ``null`` — "checked,
    not an extension") or carries no binary metadata to derive from.

    G14: derive the CPython extension surface for snapshots that predate the
    key (or a `dump` path that didn't attach it), so a saved abi3 baseline is
    still checked at compare time. Skip when the key was present (the dumper
    already answered, including an explicit "not an extension" null).

    Mach-O caveat: the ``imported_symbols`` table is itself new in G14. A
    legacy Mach-O ``.abi.json`` written before it existed has no import data;
    ``_macho_from_dict`` defaults the absent key to ``[]``. Deriving an
    extension from that empty set would be actively misleading: `scan --abi3`
    would audit *zero* CPython imports and certify the module clean, and
    `compare` would treat every import re-captured from the new binary as
    newly gained. So when a Mach-O snapshot never recorded its imports, leave
    ``python_ext`` as ``None`` (unknown) — `--abi3` then honestly reports the
    artifact must be re-dumped rather than silently passing.
    """
    python_ext_key_absent = "python_ext" not in d
    macho_data = d.get("macho")
    macho_imports_uncaptured = (
        isinstance(macho_data, dict) and "imported_symbols" not in macho_data
    )
    if (
        snap.python_ext is None
        and python_ext_key_absent
        and not macho_imports_uncaptured
    ):
        if snap.elf is not None or snap.pe is not None or snap.macho is not None:
            from ..python_ext import detect_python_extension

            snap.python_ext = detect_python_extension(snap)
