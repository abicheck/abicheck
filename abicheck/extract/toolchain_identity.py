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

"""E-S1 (docs/contribute/plans/vision-api-abi-evolution.md section E /
cli-cleanup-phase-two.md Block 5): the toolchain-identity ``FactStatus`` a
compiler-probe failure must produce.

Hosts :func:`_compiler_family_from_toolchain` (relocated from
``dumper_toolchain.py``, which sits at this repo's ``architecture/
debt.yaml`` no-growth baseline -- re-exported there for import-path
stability, see that module's own comment) and its new sibling
:func:`compiler_identity_status`.

**The bug this closes:** a compiler-probe failure previously fed an
*absent* toolchain identity (``compiler_family=None``, or -- worse, for a
castxml-produced snapshot -- a guessed label from castxml's own executable
name, e.g. ``"castxml"``, identical on both sides of a compare regardless
of which host compiler each side actually tried and failed to resolve)
rather than an explicit ``FAILED`` one. That indistinguishability from "a
probe simply never attempted" let a mismatched GCC/Clang pair compare
silently whenever both sides' probe failures happened to collapse to the
same opaque, empty ``profile_fields["compiler_family"]``.
``comparability_profile._check_profile_fingerprint_comparable`` consults
:func:`compiler_identity_status` (via ``ExtractionContract.
compiler_identity_status``) directly, ahead of and independent of the
ordinary fingerprint-equality check, so a FAILED status on either side
always refuses the comparison.
"""

from __future__ import annotations

from pathlib import Path

from ..model.availability import FactStatus

__all__ = ["_compiler_family_from_toolchain", "compiler_identity_status"]


def _compiler_family_from_toolchain(ast_toolchain: dict[str, str]) -> str | None:
    """Best-effort ADR-050 ``compiler_family`` label from the resolved host
    compiler binary (low-stakes: used for ``profile_fingerprint`` stability,
    not semantic parsing, so a reasonable guess is fine — Codex review,
    PR #624).

    Reads ``compiler_selected`` first, not the bare ``selected`` key: for a
    castxml-produced snapshot, ``selected`` names the castxml binary itself
    (e.g. ``/usr/bin/castxml``), never the host compiler whose family/ABI
    dialect actually matters here; ``compiler_selected`` is the resolved
    host cc (see ``dumper._header_ast_parser``'s ``_stamp_parser``). For a
    clang-produced snapshot the two keys already carry the same value
    (clang is both frontend and compiler), so the fallback is harmless.

    Returns ``None`` outright when ``ast_toolchain["compiler_error"]`` is
    set (E-S1), rather than falling back to the bare ``selected`` key —
    see this module's own docstring for why guessing a family from the AST
    frontend's own executable in that case is exactly the bug this closes.
    """
    if ast_toolchain.get("compiler_error"):
        return None
    path = ast_toolchain.get("compiler_selected") or ast_toolchain.get("selected") or ""
    name = Path(path).name.lower() if path else ""
    if not name:
        return None
    if "clang" in name:
        return "clang"
    if name in ("cl", "cl.exe"):
        return "msvc"
    if "gcc" in name or "g++" in name:
        return "gnu"
    return name


def compiler_identity_status(ast_toolchain: dict[str, str]) -> FactStatus | None:
    """``FactStatus`` for the resolved host-compiler identity behind an L2
    header-AST parse.

    ``FactStatus.FAILED`` when ``dumper_toolchain._stamp_ast_parser``'s own
    host-compiler resolution raised (``ast_toolchain["compiler_error"]``
    set) -- *never* silently degraded to the same "absent" shape a probe
    that was simply never attempted produces (see this module's own
    docstring). ``FactStatus.PRESENT`` when a family was actually resolved.
    ``None`` when neither applies -- no ``ast_toolchain`` was recorded at
    all (no L2 frontend ran), which is not a gap, since nothing here
    asserts one.
    """
    if not ast_toolchain:
        return None
    if ast_toolchain.get("compiler_error"):
        return FactStatus.FAILED
    if _compiler_family_from_toolchain(ast_toolchain) is not None:
        return FactStatus.PRESENT
    return None
