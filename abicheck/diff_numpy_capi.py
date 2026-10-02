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
* :func:`check_numpy_metadata_contract` (removed) — a single-artifact *self-
  consistency* check: does the binary's own NumPy C-API target exceed what
  the wheel/package's declared ``numpy`` requirement promises? This needs
  wheel-level metadata (``package.parse_wheel_numpy_requirement``) that
  isn't available inside a per-library ``compare()`` call, so — like G10's
  ``package.parse_manylinux_glibc_floor`` — it is a standalone function for
  programmatic use, not auto-wired into the CLI compare path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .checker_types import Change
from .diff_helpers import make_change
from .model.change_catalog.kinds import ChangeKind

if TYPE_CHECKING:
    from .model.python_facts import NumPyCapiSurface


def _target_tuple(version: str | None) -> tuple[int, ...]:
    """Parse a version string into a comparable release-segment tuple.

    Delegates to :class:`packaging.version.Version` rather than a plain
    ``int(p) for p in version.split(".")`` split so a PEP 440 prerelease
    lower bound (e.g. ``numpy>=2.0rc1``) still yields its release segment
    ``(2, 0)`` instead of raising on the non-numeric ``"0rc1"`` component --
    a wheel declaring only a prerelease floor already excludes every NumPy
    1.x runtime just as surely as a final-release floor does, so treating
    it as "no floor declared" would falsely flag both an understated-
    metadata RISK finding and a BREAKING ABI-major-incompatible finding for
    metadata that's actually already correct (Codex review). Returns ``()``
    (sorts lowest) for ``None``/genuinely malformed input — mirrors
    ``diff_versioning``'s "malformed/missing leaves the finding uncomputed
    rather than crashing" convention.
    """
    if not version:
        return ()
    from packaging.version import InvalidVersion, Version

    try:
        release: tuple[int, ...] = Version(version).release
    except InvalidVersion:
        return ()
    return release


def _version_greater_than(a: tuple[int, ...], b: tuple[int, ...]) -> bool:
    """``a > b`` as dotted versions, with the same zero-padding as
    :func:`_version_at_least`."""
    length = max(len(a), len(b))
    return a + (0,) * (length - len(a)) > b + (0,) * (length - len(b))


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
    if new_target and old_target and _version_greater_than(new_target, old_target):
        changes.append(
            make_change(
                ChangeKind.NUMPY_TARGET_FLOOR_RAISED,
                symbol="<numpy-capi>",
                old=old.capi_target_version,
                new=new.capi_target_version,
            )
        )
    return changes
