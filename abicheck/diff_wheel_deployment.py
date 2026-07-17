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

"""Wheel tag / deployment-claim vs. binary-evidence checks (G27).

A wheel's platform tag makes explicit promises about where its binaries will
run. ``diff_versioning.py`` already covers the Linux half (manylinux glibc
floor — G10 — generalized to GLIBCXX/CXXABI, plus the musllinux
glibc-dependency check, both G27). This module adds the macOS half: a
``macosx_X_Y_<arch>`` platform tag promises a *maximum* deployment target
(``MACOSX_DEPLOYMENT_TARGET``) a wheel's binaries may require — see
docs/development/plans/g27-wheel-deployment-verification.md.

Windows UCRT/runtime-requirement checking, wheel-tag architecture-mismatch
detection, and CPU-ISA-baseline detection are still planned (see the plan's
"Out of scope" and the registry entry's ``next_steps``) — not implemented
here.
"""

from __future__ import annotations

from .checker_policy import ChangeKind
from .checker_types import Change
from .diff_helpers import make_change
from .diff_versioning import _parse_dotted_numeric_version, _version_le
from .macho_metadata import MachoMetadata

#: Declared-floor key for :func:`check_macos_deployment_target_floor`, in the
#: same ``runtime_floors``/``EnvironmentMatrix`` mapping G10/G27's other
#: platform-baseline checks read (ADR-020b ``--env-matrix``).
_MACOS_DEPLOYMENT_TARGET_KEY = "MACOS_DEPLOYMENT_TARGET"


def check_macos_deployment_target_floor(
    macho: MachoMetadata | None, runtime_floors: dict[str, str] | None
) -> list[Change]:
    """Check a Mach-O binary's own minimum OS version against a declared
    macOS deployment-target promise (e.g. a wheel's ``macosx_10_9_x86_64``
    platform tag) (G27).

    Mirrors :func:`diff_versioning.check_platform_baseline_floor`: this
    fires on a single artifact's own ``LC_VERSION_MIN_MACOSX``/
    ``LC_BUILD_VERSION`` minimum-OS load command (captured as
    :attr:`MachoMetadata.min_os_version`) regardless of whether it moved
    between an old and new snapshot — a binary that has *always* required
    macOS 12.3 while shipped under a ``macosx_10_14_x86_64`` tag is broken
    on day one for every 10.14–12.2 install, with no old→new delta for a
    two-snapshot diff to key on.

    *runtime_floors* is read via the ``MACOS_DEPLOYMENT_TARGET`` key
    (case-insensitive, same normalization as the GLIBC-family checks) — a
    dotted ``"X.Y"`` (or ``"X.Y.Z"``) macOS version string. Returns ``[]``
    when *macho* is absent, no floor is declared, the floor is malformed, or
    the binary's own minimum OS is at or below the declared floor.

    Version comparison is padded (via :func:`diff_versioning._version_le`,
    the same helper the ``GLIBC`` floor check uses): a bare ``"11"`` floor
    and a ``"11.0"`` load-command minimum name the same version and must
    compare equal, not have the raw tuple comparison ``(11, 0) > (11,)``
    treat the more-precise value as exceeding the floor (Codex review #583).

    Known limitation: macOS's pre-Big-Sur "compatibility version" scheme
    (e.g. ``10.16`` reported by some tools for what is actually macOS 11)
    is not reconciled against the modern ``11.x``+ scheme — both are
    compared as plain dotted-numeric tuples, which orders ``10.16`` above
    ``10.9`` correctly but not necessarily against an ``11.0`` floor the way
    a human would expect. Real-world wheel tags and Mach-O load commands
    overwhelmingly agree on one scheme or the other in practice.
    """
    if macho is None or not runtime_floors:
        return []
    floors = {k.upper(): v for k, v in runtime_floors.items()}
    floor_raw = floors.get(_MACOS_DEPLOYMENT_TARGET_KEY)
    if not floor_raw:
        return []
    floor_tuple = _parse_dotted_numeric_version(floor_raw)
    if floor_tuple is None:
        return []
    required_raw = macho.min_os_version
    if not required_raw:
        return []
    required_tuple = _parse_dotted_numeric_version(required_raw)
    if required_tuple is None or _version_le(required_tuple, floor_tuple):
        return []
    return [
        make_change(
            ChangeKind.MACOS_DEPLOYMENT_TARGET_RAISED,
            symbol="<platform-baseline>",
            name=macho.install_name or "<binary>",
            detail="macOS deployment target",
            old=floor_raw,
            new=required_raw,
        )
    ]
