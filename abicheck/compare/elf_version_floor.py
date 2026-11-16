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

"""Runtime-floor findings from ELF ``GLIBC_``/``GLIBCXX_``/``CXXABI_`` version nodes.

Split out of ``diff_platform_elf_symbols`` (its 800-line cap). A version node is
a deployment floor: the highest parseable node per prefix is the oldest runtime
the binary loads against. ``RUNTIME_FLOOR_RAISED`` reports a raise; the baseline
exemption for a newly needed provider reads ``model/glibc_baseline``.
"""

from __future__ import annotations

from typing import Any

from ..diff_helpers import make_change
from ..diff_versioning import _UNPARSEABLE_VERSION, _parse_abi_version_tag
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.glibc_baseline import arch_baseline

__all__ = [
    "_effective_old_floor",
    "_new_provider_floor_changes",
    "_runtime_floor_changes",
]


def _max_parseable_tag(vers: set[str], prefix: str) -> tuple[tuple[int, ...], str]:
    """Return ``(max_version_tuple, its_tag)`` among parseable *prefix* tags.

    Non-numeric tags (``GLIBC_PRIVATE``, ``GLIBC_ABI_DT_RELR``) are excluded —
    they are markers, not deployment floors. ``((0,), "")`` when none parse.
    """
    best: tuple[int, ...] = (0,)
    best_tag = ""
    for v in vers:
        if not v.startswith(prefix + "_"):
            continue
        t = _parse_abi_version_tag(v)
        if t == _UNPARSEABLE_VERSION:
            continue
        if t > best:
            best, best_tag = t, v
    return best, best_tag


def _floor_evidence_symbols(
    new_elf: Any, lib: str, prefix: str, old_max: tuple[int, ...]
) -> list[str]:
    """Imported symbols whose required *prefix* version exceeds *old_max*.

    These are the imports that pulled the deployment floor up — the actionable
    part of the finding (``__libc_start_main`` alone means a pure relink
    artifact; a real API symbol means new runtime functionality is used).
    """
    names: set[str] = set()
    for imp in getattr(new_elf, "imports", []) or []:
        ver = getattr(imp, "version", "") or ""
        if not ver.startswith(prefix + "_"):
            continue
        soname = getattr(imp, "version_soname", "") or ""
        if soname and soname != lib:
            continue
        t = _parse_abi_version_tag(ver)
        if t == _UNPARSEABLE_VERSION or t <= old_max:
            continue
        names.add(f"{getattr(imp, 'name', '')}@{ver}")
    return sorted(names)


def _binary_wide_max(elf: Any, prefix: str) -> tuple[tuple[int, ...], str]:
    """The highest parseable *prefix* version across every provider *elf* needs.

    The deployment floor is binary-wide: glibc's ``GLIBC_`` nodes name one
    runtime whichever library they come from. ``((0,), "")`` when none parse.
    """
    best: tuple[tuple[int, ...], str] = ((0,), "")
    for lib_vers in (getattr(elf, "versions_required", None) or {}).values():
        floor, tag = _max_parseable_tag(set(lib_vers), prefix)
        if floor > best[0]:
            best = (floor, tag)
    return best


def _effective_old_floor(
    old_elf: Any, new_elf: Any, prefix: str
) -> tuple[tuple[int, ...], str]:
    """The floor a newly needed provider is measured against.

    The binary-wide maximum of *prefix*, but never below the architecture's
    ``GLIBC_`` baseline (model/glibc_baseline.py): every glibc on that
    architecture provides the baseline node, so it is no floor. When the
    baseline is the higher value, its tag names the baseline and why.
    """
    floor, tag = _binary_wide_max(old_elf, prefix)
    if prefix == "GLIBC":
        baseline = arch_baseline(getattr(new_elf, "machine", None))
        if baseline is not None and baseline[0] > floor:
            return baseline[0], f"{baseline[1]} (architecture baseline)"
    return floor, tag


def _new_provider_floor_changes(
    lib: str, new_vers: set[str], old_elf: Any, new_elf: Any
) -> list[Change]:
    """RUNTIME_FLOOR_RAISED for a newly needed provider.

    A provider the old binary did not need can still raise the binary-wide
    floor (``libc`` needed ``GLIBC_2.2.5`` before, a new provider needs
    ``GLIBC_2.25``). The comparison is against the binary-wide maximum, so a
    new provider whose nodes stay at or below the existing floor reports
    nothing; the old tag is empty when the floor rises from no requirement.
    """
    changes: list[Change] = []
    prefixes = {v.rsplit("_", 1)[0] for v in new_vers if "_" in v}
    for prefix in sorted(prefixes):
        old_floor, old_tag = _effective_old_floor(old_elf, new_elf, prefix)
        new_floor, new_tag = _binary_wide_max(new_elf, prefix)
        if new_floor <= old_floor:
            continue
        evidence = _floor_evidence_symbols(new_elf, lib, prefix, old_floor)
        sample = ", ".join(evidence[:5])
        if len(evidence) > 5:
            sample += f" (+{len(evidence) - 5} more)"
        changes.append(
            make_change(
                ChangeKind.RUNTIME_FLOOR_RAISED,
                symbol=f"{lib}:{prefix}",
                name=sample or "(no import evidence captured)",
                detail=lib,
                old=old_tag,
                new=new_tag,
                affected_symbols=evidence or None,
            )
        )
    return changes


def _runtime_floor_changes(
    lib: str, old_vers: set[str], new_vers: set[str], new_elf: Any
) -> list[Change]:
    """Synthesize per-prefix RUNTIME_FLOOR_RAISED headline findings.

    The per-symbol ``SYMBOL_VERSION_REQUIRED_ADDED`` findings enumerate every
    new version node; this rolls them up to the root cause a maintainer acts
    on: "the minimum runtime this binary loads against rose from X to Y" —
    one finding per (provider lib, version-tag prefix), carrying the imported
    symbols that raised the floor as evidence. Emitted alongside (not instead
    of) the per-node findings, mirroring the dual-ABI-flip collapse pattern.
    """
    changes: list[Change] = []
    prefixes = {v.rsplit("_", 1)[0] for v in (old_vers | new_vers) if "_" in v}
    for prefix in sorted(prefixes):
        old_max, old_tag = _max_parseable_tag(old_vers, prefix)
        new_max, new_tag = _max_parseable_tag(new_vers, prefix)
        # Only a *raise* of an existing floor reports. A prefix appearing from
        # nothing is already covered by the per-node findings, and a lowered
        # floor broadens compatibility.
        if old_max == (0,) or new_max <= old_max:
            continue
        evidence = _floor_evidence_symbols(new_elf, lib, prefix, old_max)
        sample = ", ".join(evidence[:5])
        if len(evidence) > 5:
            sample += f" (+{len(evidence) - 5} more)"
        changes.append(
            make_change(
                ChangeKind.RUNTIME_FLOOR_RAISED,
                symbol=f"{lib}:{prefix}",
                name=sample or "(no import evidence captured)",
                detail=lib,
                old=old_tag,
                new=new_tag,
                affected_symbols=evidence or None,
            )
        )
    return changes
