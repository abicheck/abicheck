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

"""Wheel tag / deployment-claim ChangeKind registry entries (G27).

Split out of ``change_registry.py`` to keep that module under the
AI-readiness 2000-line hard cap (same reason ``change_registry_numpy.py``
exists). These entries are spliced into the single ``REGISTRY`` at import
time — declaring a kind here is exactly equivalent to declaring it in
``change_registry.py``. See ``abicheck/diff_versioning.py``
(``check_musllinux_glibc_dependency``, and the GLIBCXX/CXXABI extension of
``check_platform_baseline_floor``) and ``abicheck/diff_wheel_deployment.py``
(``check_macos_deployment_target_floor``) for the evidence extraction and
detectors that emit these kinds, and
``docs/development/plans/g27-wheel-deployment-verification.md`` for the full
design.
"""

from __future__ import annotations

from .change_registry_types import ChangeKindMeta, Verdict

_B = Verdict.BREAKING
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta

WHEEL_DEPLOYMENT_EXTENSION_ENTRIES: list[ChangeKindMeta] = [
    _E(
        "musllinux_glibc_dependency_detected",
        _B,
        impact="The binary is claimed musllinux-compatible (PEP 656 — "
        "runs on musl libc, e.g. Alpine) but requires a GLIBC_*-versioned "
        "symbol, or carries an implied glibc-loader requirement (DT_RELR): "
        "glibc's own libc.so.6/loader symbol-versioning namespace doesn't "
        "exist on a musl system at all. (GLIBCXX_*/CXXABI_* alone are not "
        "flagged here — a musl system's libstdc++ can legitimately carry "
        "its own such verneed entries; see "
        "diff_versioning.check_musllinux_glibc_dependency's docstring.) "
        "This is not a version mismatch abicheck can rate as a deployment "
        "risk, it is a dependency that doesn't exist on the target — the "
        "dynamic loader fails to resolve the glibc-flavoured shared object "
        "outright. Rebuild against a musl toolchain (e.g. the musllinux "
        "manylinux-equivalent Docker images) rather than relinking a glibc "
        "build under the musllinux tag.",
        description_template="musllinux-tagged binary requires glibc: {new} (required by: {name})",
    ),
    _E(
        "macos_deployment_target_raised",
        _R,
        impact="The binary's own Mach-O minimum-OS load command "
        "(LC_VERSION_MIN_MACOSX/LC_BUILD_VERSION) exceeds the macOS "
        "deployment target promised by the wheel's platform tag (e.g. "
        "macosx_10_9_x86_64) or an explicit --env-matrix declaration — "
        "the macOS counterpart of G10's manylinux glibc-floor check. "
        "Existing installs on the tag's promised deployment target can "
        "refuse to load the binary (dyld enforces the minimum-OS load "
        "command at load time), or exhibit undefined behavior calling "
        "into SDK symbols introduced after the promised floor.",
        description_template="macOS deployment target exceeded: binary requires {new}, declared target promises at most {old} (required by: {name})",
    ),
    _E(
        "wheel_tag_architecture_mismatch",
        _B,
        impact="The wheel's platform tag names exactly one CPU architecture "
        "(e.g. manylinux_2_17_x86_64, macosx_11_0_arm64), but the contained "
        "binary's own ELF e_machine / Mach-O cpu_type records a different "
        "one. This is not a deployment-envelope risk — the wheel simply "
        "cannot be loaded on the architecture it claims to support at all. "
        "Typically a packaging/CI mistake (wrong cross-compilation target, "
        "mismatched build matrix leg, or a stale artifact reused under the "
        "wrong tag).",
        description_template="Wheel tag claims architecture {old}, binary is {new} (required by: {name})",
    ),
]
