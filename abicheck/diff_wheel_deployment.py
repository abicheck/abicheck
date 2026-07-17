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
glibc-dependency check, both G27). This module adds: the macOS half (a
``macosx_X_Y_<arch>`` platform tag promises a *maximum* deployment target,
``MACOSX_DEPLOYMENT_TARGET``, a wheel's binaries may require); a
cross-platform check (the wheel tag's claimed architecture against the
binary's own recorded machine/cpu_type); and two RPATH/RUNPATH-hygiene
checks scoped to a wheel-verification context — see
docs/development/plans/g27-wheel-deployment-verification.md.

Windows UCRT/runtime-requirement checking and CPU-ISA-baseline detection are
still planned (see the plan's "Out of scope" and the registry entry's
``next_steps``) — not implemented here. The RPATH/RUNPATH checks here are
deliberately narrow (ELF/Linux only, and gated on strong internal-evidence
signals rather than an allowlist of "known-safe" system SONAMEs): a full
per-manylinux/musllinux-tag allowed-dependency policy (mirroring
``auditwheel``'s own versioned policy JSON) would be needed to check the
*general* "is this DT_NEEDED entry permitted by the wheel's platform tag"
question without a real risk of false positives on legitimately-present
system libraries this module doesn't enumerate — see "Out of scope".
"""

from __future__ import annotations

from .binary_utils import strip_vendor_hash
from .checker_policy import ChangeKind
from .checker_types import Change
from .diff_helpers import make_change
from .diff_versioning import _parse_dotted_numeric_version, _version_le
from .elf_metadata import ElfMetadata
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

    Also skipped for a fat/universal Mach-O (more than one entry in
    :attr:`MachoMetadata.cpu_types`): ``min_os_version`` is only captured
    for the *one* slice ``parse_macho_metadata`` selected for the host
    running abicheck, not per slice — a universal binary's arm64 slice
    commonly has a genuinely higher real minimum (11.0, since Apple Silicon
    didn't exist before macOS 11) than its x86_64 slice, so attributing the
    single captured value to whichever slice the wheel tag claims isn't
    reliable (Codex review #583, the deployment-target counterpart to the
    same fat-binary ambiguity already handled for
    :func:`check_wheel_tag_architecture_mismatch` and
    :func:`abicheck.package.parse_macos_deployment_target_floor`).
    """
    if macho is None or not runtime_floors:
        return []
    if len(getattr(macho, "cpu_types", None) or []) > 1:
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


#: Declared-claim key for :func:`check_wheel_tag_architecture_mismatch`, in
#: the same ``runtime_floors``/``EnvironmentMatrix`` mapping G10/G27's other
#: platform-baseline checks read. Populated from
#: ``package.parse_wheel_architecture_claim()``.
_WHEEL_ARCH_KEY = "WHEEL_ARCH"

#: Wheel-tag architecture claim (as returned by
#: ``package.parse_wheel_architecture_claim()``) -> the ELF ``e_machine``
#: value(s) that satisfy it. ``ppc64``/``ppc64le`` share one ``e_machine``
#: enum value; every claim's expected byte order is checked separately via
#: :data:`_ARCH_CLAIM_TO_ELF_EI_DATA` below, since ``e_machine`` alone
#: proves neither the ppc64/ppc64le distinction nor, e.g., that a claimed
#: ``x86_64`` binary is actually little-endian (Codex review #583).
_ARCH_CLAIM_TO_ELF_MACHINE: dict[str, frozenset[str]] = {
    "x86_64": frozenset({"EM_X86_64"}),
    "aarch64": frozenset({"EM_AARCH64"}),
    "i686": frozenset({"EM_386"}),
    "armv7l": frozenset({"EM_ARM"}),
    "ppc64le": frozenset({"EM_PPC64"}),
    "ppc64": frozenset({"EM_PPC64"}),
    "s390x": frozenset({"EM_S390"}),
}

#: Every wheel-tag architecture claim's expected ELF ``EI_DATA`` byte order
#: (``"LSB"``/``"MSB"``, :attr:`ElfMetadata.ei_data`) — not just the
#: ``ppc64``/``ppc64le`` pair whose ``e_machine`` values collide.
#: ``e_machine`` alone doesn't prove endianness for *any* claim: a claimed
#: ``x86_64`` ELF captured with ``ei_data="MSB"`` is equally impossible (x86
#: is always little-endian) and would otherwise pass since ``EM_X86_64``
#: matched, the same false-negative shape as the ppc64 case (Codex review
#: #583, follow-up). ``aarch64``/``i686``/``armv7l`` (the trailing ``l``
#: itself denotes little-endian — a big-endian ARM uses a distinct
#: ``armv7b``-style name) are little-endian; ``s390x`` (IBM Z) is always
#: big-endian.
_ARCH_CLAIM_TO_ELF_EI_DATA: dict[str, str] = {
    "x86_64": "LSB",
    "aarch64": "LSB",
    "i686": "LSB",
    "armv7l": "LSB",
    "ppc64le": "LSB",
    "ppc64": "MSB",
    "s390x": "MSB",
}

#: Wheel-tag architecture claim -> the Mach-O ``cpu_type`` value(s) that
#: satisfy it. ``ARM64E`` (the pointer-authenticating arm64e ABI variant) is
#: still an ``arm64`` claim as far as the wheel tag is concerned.
_ARCH_CLAIM_TO_MACHO_CPU_TYPE: dict[str, frozenset[str]] = {
    "x86_64": frozenset({"X86_64"}),
    "arm64": frozenset({"ARM64", "ARM64E"}),
}


def check_wheel_tag_architecture_mismatch(
    elf: ElfMetadata | None,
    macho: MachoMetadata | None,
    runtime_floors: dict[str, str] | None,
) -> list[Change]:
    """Check a binary's own recorded architecture against the wheel tag's
    claimed architecture (G27).

    A wheel's platform tag names exactly one CPU architecture for the
    single-architecture Linux/macOS tags (e.g. ``manylinux_2_17_x86_64``,
    ``macosx_11_0_arm64`` — see
    ``package.parse_wheel_architecture_claim()`` for exactly which tags this
    is safe to derive from). The contained binary's own ELF ``e_machine`` /
    Mach-O ``cpu_type`` is ground truth; a mismatch means the wheel cannot
    even be loaded on the architecture it claims to support — a hard
    failure, not a version-floor risk. This is the wheel-tag-claim
    counterpart to G13's ``elf_machine_changed``/``macho_cpu_type_changed``,
    which compare two arbitrary binaries against each other rather than a
    binary against its own wheel's filename promise.

    *runtime_floors* is read via the ``WHEEL_ARCH`` key (case-insensitive,
    same normalization as the other G10/G27 checks) — a
    :func:`abicheck.package.parse_wheel_architecture_claim` value (e.g.
    ``"x86_64"``, ``"aarch64"``, ``"arm64"``). Returns ``[]`` when no claim
    is declared, the claim isn't a recognized architecture token, neither
    *elf* nor *macho* carries a recorded machine/cpu_type, or the recorded
    value satisfies the claim.

    For a fat/universal Mach-O, :attr:`MachoMetadata.cpu_type` is only the
    *one* slice ``parse_macho_metadata`` selected for the host running
    abicheck (arm64 preferred on Apple Silicon, x86_64 otherwise) — not
    necessarily the slice the wheel tag actually claims. A single-arch wheel
    tag whose binary happens to still be a fat/universal Mach-O (unusual,
    but not impossible if a thinning step was skipped) would otherwise
    false-positive here purely based on which host happened to run the
    parse. This checks every slice in :attr:`MachoMetadata.cpu_types` (all
    architectures the fat binary actually carries, always populated
    alongside ``cpu_type``) rather than only the selected one, falling back
    to ``cpu_type`` alone for a legacy snapshot predating that field
    (Codex review #583).

    A matching ``e_machine`` alone isn't sufficient to confirm any claim's
    byte order: ``ppc64``/``ppc64le`` share one ``e_machine`` value, but even
    an architecture with an unambiguous ``e_machine`` (e.g. ``x86_64``, which
    is always little-endian) could otherwise pass a claim it doesn't
    actually satisfy if the captured evidence carries the opposite
    endianness (a strong signal of a corrupted or misidentified snapshot).
    The ELF ``EI_DATA`` byte order (:attr:`ElfMetadata.ei_data`) is checked
    against every claim's expected value in
    :data:`_ARCH_CLAIM_TO_ELF_EI_DATA` (Codex review #583).
    """
    if not runtime_floors:
        return []
    floors = {k.upper(): v for k, v in runtime_floors.items()}
    claimed = floors.get(_WHEEL_ARCH_KEY)
    if not claimed:
        return []
    claimed = claimed.lower()
    if elf is not None:
        elf_machine = getattr(elf, "machine", "")
        if elf_machine:
            expected = _ARCH_CLAIM_TO_ELF_MACHINE.get(claimed)
            if expected is None:
                return []
            if elf_machine not in expected:
                return [
                    make_change(
                        ChangeKind.WHEEL_TAG_ARCHITECTURE_MISMATCH,
                        symbol="<platform-baseline>",
                        name=getattr(elf, "soname", "") or "<binary>",
                        detail="architecture",
                        old=claimed,
                        new=elf_machine,
                    )
                ]
            expected_ei_data = _ARCH_CLAIM_TO_ELF_EI_DATA.get(claimed)
            elf_ei_data = getattr(elf, "ei_data", "")
            if (
                expected_ei_data is not None
                and elf_ei_data
                and elf_ei_data != expected_ei_data
            ):
                return [
                    make_change(
                        ChangeKind.WHEEL_TAG_ARCHITECTURE_MISMATCH,
                        symbol="<platform-baseline>",
                        name=getattr(elf, "soname", "") or "<binary>",
                        detail="architecture",
                        old=claimed,
                        new=f"{elf_machine} ({elf_ei_data})",
                    )
                ]
            return []
    if macho is not None:
        cpu_type = getattr(macho, "cpu_type", "")
        if cpu_type:
            expected = _ARCH_CLAIM_TO_MACHO_CPU_TYPE.get(claimed)
            if expected is None:
                return []
            slices: list[str] = getattr(macho, "cpu_types", None) or [cpu_type]
            if any(s.upper() in expected for s in slices):
                return []
            return [
                make_change(
                    ChangeKind.WHEEL_TAG_ARCHITECTURE_MISMATCH,
                    symbol="<platform-baseline>",
                    name=macho.install_name or "<binary>",
                    detail="architecture",
                    old=claimed,
                    new=", ".join(slices),
                )
            ]
    return []


def _elf_rpath_entries(elf: ElfMetadata) -> list[str]:
    """The colon-joined ``DT_RPATH``/``DT_RUNPATH`` tag content, split into
    individual path entries (empty entries dropped)."""
    combined = ":".join(
        p for p in (getattr(elf, "rpath", ""), getattr(elf, "runpath", "")) if p
    )
    return [entry for entry in combined.split(":") if entry]


def _has_origin_relative_entry(entries: list[str]) -> bool:
    """Whether any RPATH/RUNPATH entry is ``$ORIGIN``-relative (the portable
    convention ``auditwheel``/``delocate`` rewrite to, so a wheel's binaries
    find their bundled dependencies relative to their own install location
    rather than a build-machine-specific absolute path)."""
    return any(entry.startswith(("$ORIGIN", "${ORIGIN}")) for entry in entries)


#: Explicit opt-in key for :func:`check_wheel_rpath_not_portable`/
#: :func:`check_wheel_closure_dependency_violation`. Deliberately a
#: *dedicated* marker rather than "any declared runtime_floors key": unlike
#: WHEEL_ARCH/MUSLLINUX/MACOS_DEPLOYMENT_TARGET, the pre-existing GLIBC/
#: GLIBCXX/CXXABI keys are a general-purpose ADR-020b symbol-version-floor
#: declaration usable for *any* deployment scenario — an ordinary,
#: non-wheel shared library with a perfectly normal absolute RPATH and a
#: declared `runtime_floors: {GLIBC: "2.28"}` for unrelated reasons must
#: not suddenly get wheel-packaging findings it never asked for (Codex
#: review #583).
_WHEEL_CONTEXT_KEY = "WHEEL_CONTEXT"


def check_wheel_rpath_not_portable(
    elf: ElfMetadata | None, runtime_floors: dict[str, str] | None
) -> list[Change]:
    """Flag a non-``$ORIGIN``-relative (absolute) RPATH/RUNPATH entry (G27).

    A wheel's binaries are installed to an unpredictable, per-user site-
    packages path — any RPATH/RUNPATH entry that isn't ``$ORIGIN``-relative
    is almost always a build-machine artifact (the build sysroot, a CI
    runner's checkout path, a developer's local prefix) that will not exist
    on the install target at all. This is exactly what ``auditwheel``/
    ``delocate`` rewrite RPATH/RUNPATH to fix; a wheel that skipped that
    repair step (or a hand-rolled build) ships with a search path that
    resolves nothing on a clean install — the classic "works in CI,
    `ImportError: lib not found` on the user's machine" wheel-packaging bug.

    Gated on the dedicated ``runtime_floors["WHEEL_CONTEXT"]`` key (any
    truthy value), *not* on any declared floor being present: an absolute
    RPATH is completely normal for an ordinary system-installed shared
    library declaring, say, a `GLIBC` floor for unrelated deployment-floor
    reasons — that must not suddenly get a wheel-portability finding it
    never opted into (Codex review #583). Returns ``[]`` when
    ``WHEEL_CONTEXT`` isn't declared, *elf* is absent, or every RPATH/
    RUNPATH entry is ``$ORIGIN``-relative (or there are none at all).
    """
    if not runtime_floors or elf is None:
        return []
    floors = {k.upper(): v for k, v in runtime_floors.items()}
    if not floors.get(_WHEEL_CONTEXT_KEY):
        return []
    entries = _elf_rpath_entries(elf)
    if not entries:
        return []
    absolute = [e for e in entries if not e.startswith(("$ORIGIN", "${ORIGIN}"))]
    if not absolute:
        return []
    return [
        make_change(
            ChangeKind.WHEEL_RPATH_NOT_PORTABLE,
            symbol="<platform-baseline>",
            name=getattr(elf, "soname", "") or "<binary>",
            detail="RPATH/RUNPATH",
            old="$ORIGIN-relative",
            new=":".join(absolute),
        )
    ]


def check_wheel_closure_dependency_violation(
    elf: ElfMetadata | None, runtime_floors: dict[str, str] | None
) -> list[Change]:
    """Flag a vendored dependency with no mechanism to ever be found (G27).

    ``auditwheel``/``delocate`` vendor external dependencies *into* the
    wheel and rename them with a content-hash suffix (recognized here via
    the same :func:`abicheck.binary_utils.strip_vendor_hash` pattern G9's
    vendored-library pairing uses) so a rebuild doesn't collide with the
    system's own copy. A DT_NEEDED entry matching that hash-suffixed naming
    convention is a strong signal the binary is *meant* to load a bundled
    dependency — but if the binary carries no ``$ORIGIN``-relative RPATH/
    RUNPATH entry at all, there is no mechanism for the dynamic loader to
    ever find it: the vendored library is not actually part of the wheel's
    resolvable dependency closure, regardless of whether the file itself was
    physically included.

    This is deliberately narrower than a general "dependency outside the
    permitted manylinux/musllinux closure" check (which would need a real
    per-tag allowed-SONAME policy, out of scope here — see this module's
    docstring): it only fires on the *internal* inconsistency of "this looks
    like your own vendored library, but nothing points at it," not on
    whether an ordinary DT_NEEDED entry is a permitted system dependency.

    Gated on the same dedicated ``runtime_floors["WHEEL_CONTEXT"]`` key
    :func:`check_wheel_rpath_not_portable` uses — *not* on any declared
    floor being present, since GLIBC/GLIBCXX/CXXABI are a general-purpose
    ADR-020b mechanism unrelated to wheel packaging (Codex review #583).
    Returns ``[]`` when ``WHEEL_CONTEXT`` isn't declared, *elf* is absent,
    no DT_NEEDED entry matches the vendored-hash naming convention, or an
    ``$ORIGIN``-relative RPATH/RUNPATH entry is present (even one — this
    check does not attempt to verify that entry actually resolves to the
    specific vendored library named, only that *some* bundling mechanism
    exists at all).
    """
    if not runtime_floors or elf is None:
        return []
    floors = {k.upper(): v for k, v in runtime_floors.items()}
    if not floors.get(_WHEEL_CONTEXT_KEY):
        return []
    needed: list[str] = getattr(elf, "needed", None) or []
    vendored = [lib for lib in needed if strip_vendor_hash(lib) != lib]
    if not vendored:
        return []
    if _has_origin_relative_entry(_elf_rpath_entries(elf)):
        return []
    return [
        make_change(
            ChangeKind.WHEEL_CLOSURE_DEPENDENCY_VIOLATION,
            symbol="<platform-baseline>",
            name=getattr(elf, "soname", "") or "<binary>",
            detail="wheel closure",
            old="$ORIGIN-relative RPATH/RUNPATH expected",
            new=", ".join(sorted(vendored)),
        )
    ]
