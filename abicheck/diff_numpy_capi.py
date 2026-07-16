# Copyright 2026 Nikolay Petrov
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

"""NumPy C-API compatibility-envelope detectors (G26).

Two independent checks over :class:`~abicheck.numpy_capi.NumPyCapiSurface`:

* :func:`diff_numpy_capi_surfaces` — a two-snapshot *delta*: did the module
  start/stop consuming the NumPy C-API, or did its compiled-in minimum
  NumPy target rise? Wired into :func:`abicheck.checker.compare` (needs
  only the two snapshots already being compared).
* :func:`check_numpy_metadata_contract` — a single-artifact *self-
  consistency* check: does the binary's own NumPy C-API target exceed what
  the wheel/package's declared ``numpy`` requirement promises? This needs
  wheel-level metadata (``package.parse_wheel_numpy_requirement``) that
  isn't available inside a per-library ``compare()`` call, so — like G10's
  ``package.parse_manylinux_glibc_floor`` — it is a standalone function for
  programmatic use, not auto-wired into the CLI compare path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .checker_policy import ChangeKind
from .checker_types import Change
from .diff_helpers import make_change

if TYPE_CHECKING:
    from packaging.specifiers import SpecifierSet

    from .numpy_capi import NumPyCapiSurface


def _target_tuple(version: str | None) -> tuple[int, ...]:
    """Parse a dotted "X.Y" target-version string into a comparable tuple.

    Returns ``()`` (sorts lowest) for ``None``/malformed input — mirrors
    ``diff_versioning``'s "malformed/missing leaves the finding uncomputed
    rather than crashing" convention.
    """
    if not version:
        return ()
    try:
        return tuple(int(p) for p in version.split("."))
    except ValueError:
        return ()


def _declared_floor(specifiers: SpecifierSet) -> tuple[int, ...] | None:
    """The largest lower-bound version among a SpecifierSet's >=/>/==/~= clauses.

    ``None`` when the set declares no lower bound at all (empty, or only
    upper-bound/exclusion clauses) — "any version is nominally allowed",
    distinct from "no numpy requirement declared at all" (also ``None`` at
    the caller, via ``declared_numpy_requirement`` being falsy in the first
    place — both cases correctly fail to cover any real target version).

    An exclusive ``>`` bound (e.g. ``numpy>2.0``) is treated the same as
    ``>=`` at this function's coarse major.minor granularity: excluding it
    would make ``numpy>2.0`` look like "no floor declared", falsely flagging
    a metadata gap and an ABI-major incompatibility for a requirement that
    in fact excludes every NumPy 1.x runtime (Codex review).
    """
    best: tuple[int, ...] | None = None
    for spec in specifiers:
        if spec.operator not in (">=", ">", "==", "~="):
            continue
        v = _target_tuple(spec.version.rstrip(".*"))
        if v and (best is None or v > best):
            best = v
    return best


def diff_numpy_capi_surfaces(
    old: NumPyCapiSurface | None, new: NumPyCapiSurface | None
) -> list[Change]:
    """Diff two snapshots' NumPy C-API consumption (G26).

    Fires on consumption gained/lost and on the compiled-in target-version
    floor rising — never on the target *dropping* (an extension declaring
    it now works on an older NumPy floor than before is a compatibility
    improvement, not a regression).

    *old*/*new* being ``None`` means no NumPy C-API evidence was captured on
    that side at all — a snapshot predating this field's introduction, or a
    binary that couldn't be scanned — which is not the same as
    :attr:`~abicheck.numpy_capi.NumPyCapiSurface` confirming non-consumption
    (``consumes_array_api=False, consumes_ufunc_api=False``). Comparing
    against missing evidence would risk a false ADDED/REMOVED finding (e.g. a
    library that already consumed the NumPy C-API before this evidence
    existed, re-dumped only on the "new" side, looks identical to a genuine
    new dependency), so this returns no findings whenever either side is
    ``None`` (Codex review).
    """
    changes: list[Change] = []
    if old is None or new is None:
        return changes

    old_consumes = old.consumes_array_api or old.consumes_ufunc_api
    new_consumes = new.consumes_array_api or new.consumes_ufunc_api

    if not old_consumes and new_consumes:
        apis = ", ".join(
            n
            for n, flag in (
                ("_ARRAY_API", new.consumes_array_api),
                ("_UFUNC_API", new.consumes_ufunc_api),
            )
            if flag
        )
        changes.append(
            make_change(
                ChangeKind.NUMPY_CAPI_CONSUMPTION_ADDED,
                symbol="<numpy-capi>",
                detail=apis,
            )
        )
        return changes
    if old_consumes and not new_consumes:
        changes.append(
            make_change(
                ChangeKind.NUMPY_CAPI_CONSUMPTION_REMOVED,
                symbol="<numpy-capi>",
            )
        )
        return changes
    if not old_consumes and not new_consumes:
        return changes

    old_target = _target_tuple(old.capi_target_version)
    new_target = _target_tuple(new.capi_target_version)
    if new_target and old_target and new_target > old_target:
        changes.append(
            make_change(
                ChangeKind.NUMPY_TARGET_FLOOR_RAISED,
                symbol="<numpy-capi>",
                old=old.capi_target_version,
                new=new.capi_target_version,
            )
        )
    return changes


def check_numpy_metadata_contract(
    surface: NumPyCapiSurface | None, declared_numpy_requirement: str | None
) -> list[Change]:
    """Check a binary's own NumPy C-API target against a declared numpy
    requirement (e.g. a wheel's ``Requires-Dist: numpy...``) (G26).

    *declared_numpy_requirement* is a PEP 508 specifier-set string (e.g.
    ``">=1.23.5,<3"``, as returned by
    :func:`abicheck.package.parse_wheel_numpy_requirement`) or ``None``/``""``
    when no unconditional ``numpy`` requirement is declared at all.

    Returns ``[]`` when *surface* is ``None`` (no NumPy C-API consumption —
    nothing to check), when its target version wasn't recoverable (degraded
    binary-evidence coverage, not "no floor"), or when the declared
    requirement's lower bound already covers the binary's target.
    """
    if surface is None or not surface.capi_target_version:
        return []
    target = surface.capi_target_version
    target_tuple = _target_tuple(target)

    from packaging.specifiers import InvalidSpecifier, SpecifierSet

    try:
        specifiers = SpecifierSet(declared_numpy_requirement or "")
    except InvalidSpecifier:
        specifiers = None

    declared_floor = _declared_floor(specifiers) if specifiers is not None else None
    if declared_floor is not None and declared_floor >= target_tuple:
        return []  # declared lower bound already covers the binary's own target

    changes = [
        make_change(
            ChangeKind.NUMPY_METADATA_UNDERSTATES_REQUIRED_VERSION,
            symbol="<numpy-capi>",
            old=declared_numpy_requirement or "(none declared)",
            new=target,
        )
    ]
    # The 1.x/2.0 ABI boundary is a hard import crash (NumPy 2.0 changed the
    # ABI), not merely a missing/stale metadata promise — a separate,
    # BREAKING finding when the declared floor still allows a NumPy 1.x
    # runtime but the binary's own target requires >= 2.0.
    if target_tuple and target_tuple[0] >= 2:
        if declared_floor is None or declared_floor[0] < 2:
            changes.append(
                make_change(
                    ChangeKind.NUMPY_ABI_MAJOR_INCOMPATIBLE,
                    symbol="<numpy-capi>",
                    old=declared_numpy_requirement or "(none declared)",
                    new=target,
                )
            )
    return changes
