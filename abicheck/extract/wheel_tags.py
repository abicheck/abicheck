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

"""Wheel filename-tag and ``METADATA`` fact readers (PEP 425/440/508/600).

Every function here answers one question of the form "what does this
wheel's own name (or its ``METADATA``) *declare*?" -- a manylinux/musllinux
glibc/musl floor, a macOS deployment target, the PEP 508 marker environment
a wheel's filename pins down, and the NumPy requirement its metadata
states. Pure parsing over a filename string or an already-built zip: no
extraction, no filesystem walk, no policy.

Moved verbatim out of ``abicheck/package.py`` (ADR-065 S3): reading a
declared build/packaging fact is ``extract/``'s responsibility under
ADR-061's routing table, and ``package.py`` -- an ``architecture/debt.yaml``
``no_growth`` module -- needed the room for the component inventory S3 adds
there. ``package.py`` re-exports every public name below, so
``from abicheck.package import parse_manylinux_glibc_floor`` still resolves
(the private ``_``-prefixed helpers are re-exported too: ``tests/
test_package.py`` imports several of them by name).
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

__all__ = [
    "parse_macos_deployment_target_floor",
    "parse_manylinux_glibc_floor",
    "parse_musllinux_floor",
    "parse_numpy_requirement_from_metadata",
    "parse_wheel_architecture_claim",
    "parse_wheel_numpy_requirement",
]

# manylinux platform-tag -> the glibc version it promises as a ceiling (PEP 600's ``manylinux_<glibc_major>_<glibc_minor>`` plus the three frozen legacy aliases PEP 600 defines as exact synonyms). G10: a wheel's filename tag is a promise about the *maximum* glibc symbol version its binaries may require — see docs/contribute/plans/g10-glibc-floor-check.md.
_MANYLINUX_LEGACY_FLOORS: dict[str, tuple[int, int]] = {
    "manylinux1": (2, 5),
    "manylinux2010": (2, 12),
    "manylinux2014": (2, 17),
}
_MANYLINUX_TAG_RE = re.compile(
    r"manylinux(?:_(?P<major>\d+)_(?P<minor>\d+)|(?P<legacy>1|2010|2014))(?=_|$)"
)


def parse_manylinux_glibc_floor(name: str) -> str | None:
    """Derive the strictest declared glibc floor from a manylinux tag string.

    *name* is typically a wheel filename (or its platform-tag segment) such
    as ``scipy-1.18.0-cp312-cp312-manylinux_2_17_x86_64.whl``, which may
    carry a compressed multi-tag platform segment (PEP 600), e.g.
    ``...manylinux_2_17_x86_64.manylinux2014_x86_64.whl`` when a wheel
    declares compatibility with more than one baseline. A multi-tag wheel
    is claiming to work on *every* listed baseline, so the strictest (lowest)
    glibc version among them is the one an actual binary must not exceed.

    When *name* ends in ``.whl``, only its platform-tag segment (the last
    ``-``-delimited component before the extension, per the PEP 427 wheel
    filename spec ``{distribution}-{version}(-{build})?-{python}-{abi}-
    {platform}.whl``) is scanned — not the whole filename. Otherwise a
    ``manylinux``-prefixed *distribution* name (e.g.
    ``manylinux_2_17_helper-1.0-cp312-cp312-linux_x86_64.whl``, whose
    platform tag makes no manylinux promise at all) would be misread as a
    manylinux tag.

    Returns a dotted ``"X.Y"`` string suitable for
    ``EnvironmentMatrix.runtime_floors["GLIBC"]``, or ``None`` if *name*
    carries no recognizable manylinux tag.
    """
    tag_segment = _wheel_platform_tag_segment(name)
    best: tuple[int, int] | None = None
    for m in _MANYLINUX_TAG_RE.finditer(tag_segment):
        if m.group("legacy"):
            version = _MANYLINUX_LEGACY_FLOORS[f"manylinux{m.group('legacy')}"]
        else:
            version = (int(m.group("major")), int(m.group("minor")))
        if best is None or version < best:
            best = version
    return f"{best[0]}.{best[1]}" if best is not None else None


def _wheel_platform_tag_segment(name: str) -> str:
    """The platform-tag segment of a wheel filename.

    When *name* ends in ``.whl``, only its platform-tag segment (the last
    ``-``-delimited component before the extension, per the PEP 427 wheel
    filename spec ``{distribution}-{version}(-{build})?-{python}-{abi}-
    {platform}.whl``) is returned — not the whole filename. Otherwise a
    platform-tag-prefixed *distribution* name (e.g.
    ``manylinux_2_17_helper-1.0-cp312-cp312-linux_x86_64.whl``, whose
    platform tag makes no manylinux promise at all) would be misread as
    carrying that tag. Shared by :func:`parse_manylinux_glibc_floor`,
    :func:`parse_musllinux_floor`, and
    :func:`parse_macos_deployment_target_floor` — see the first for the full
    rationale. Returns *name* unchanged when it isn't a ``.whl`` filename.
    """
    if name.lower().endswith(".whl"):
        stem = name[: -len(".whl")]
        return stem.rsplit("-", 1)[-1]
    return name


#: PEP 656 musllinux platform-tag -> the musl libc major.minor version it names, e.g. ``musllinux_1_2_x86_64``. Unlike manylinux/glibc, musl carries no symbol-versioning scheme, so there is no binary-evidence floor to check this version *against* — its presence is a yes/no "this wheel targets musl" signal for check_musllinux_glibc_dependency (G27). See docs/contribute/plans/g27-wheel-deployment-verification.md.
_MUSLLINUX_TAG_RE = re.compile(r"musllinux_(?P<major>\d+)_(?P<minor>\d+)(?=_|$)")


def parse_musllinux_floor(name: str) -> str | None:
    """Derive the strictest declared musl libc version from a musllinux tag
    string (G27).

    *name* follows the same wheel-filename/platform-tag-segment handling as
    :func:`parse_manylinux_glibc_floor` (via :func:`_wheel_platform_tag_segment`),
    including strictest-of-multi-tag resolution for a compressed PEP 600-style
    multi-platform segment.

    Returns a dotted ``"X.Y"`` string suitable for
    ``EnvironmentMatrix.runtime_floors["MUSLLINUX"]`` (read as a presence
    check, not a numeric floor, by
    :func:`abicheck.diff_versioning.check_musllinux_glibc_dependency`), or
    ``None`` if *name* carries no recognizable musllinux tag.
    """
    tag_segment = _wheel_platform_tag_segment(name)
    best: tuple[int, int] | None = None
    for m in _MUSLLINUX_TAG_RE.finditer(tag_segment):
        version = (int(m.group("major")), int(m.group("minor")))
        if best is None or version < best:
            best = version
    return f"{best[0]}.{best[1]}" if best is not None else None


#: PEP 425 macOS platform-tag -> the deployment target it promises as a ceiling, e.g. ``macosx_10_9_x86_64`` or ``macosx_11_0_universal2``. The arch component may contain digits and underscores (``x86_64``, ``universal2``); the trailing ``(?=[._]|$)`` boundary keeps a multi-tag compressed segment's dot separator from being swallowed into the match.
_MACOSX_TAG_RE = re.compile(
    r"macosx_(?P<major>\d+)_(?P<minor>\d+)_(?P<arch>[a-zA-Z0-9_]+?)(?=[.]|$)"
)

#: macOS wheel arch tokens that bundle more than one real CPU architecture under a single string (PEP 425's macOS "fat"/universal-binary tags). Even a *lone* tag using one of these can't be safely reduced to a single deployment-target floor: a real ``universal2`` wheel's arm64 slice commonly has a genuinely higher minimum OS (11.0 — Apple Silicon didn't exist before macOS 11) than its x86_64 slice's declared 10.9 floor, even though the tag names only one literal arch token (Codex review #583).
_MACOS_MULTI_SLICE_ARCH_TOKENS = frozenset(
    {"universal2", "universal", "intel", "fat", "fat3", "fat64"}
)


def parse_macos_deployment_target_floor(name: str) -> str | None:
    """Derive the declared macOS deployment target from a
    ``macosx_X_Y_<arch>`` wheel platform tag (G27, the macOS half of
    :func:`parse_manylinux_glibc_floor`'s manylinux/glibc idea).

    *name* follows the same wheel-filename/platform-tag-segment handling as
    :func:`parse_manylinux_glibc_floor`. Unlike the glibc floor, resolution
    is **not** simply "strictest across every tag in the segment": the
    deployment target is architecture-specific.

    - Multiple tags naming *different* single-CPU architectures with
      *different* targets (e.g. a fat wheel's
      ``macosx_10_9_x86_64.macosx_11_0_arm64``) cannot be collapsed into one
      number without losing that per-arch distinction — the arm64 slice's
      own Mach-O minimum OS is legitimately 11.0, and reducing the pair to
      the x86_64 slice's lower 10.9 would falsely flag that valid arm64
      slice as exceeding the floor (Codex review #583).
    - A single tag using one of :data:`_MACOS_MULTI_SLICE_ARCH_TOKENS` (e.g.
      ``universal2``) is *itself* multi-architecture even though it has only
      one literal arch token in the tag string — the same ambiguity applies
      (Codex review #583, follow-up).

    Both cases return ``None`` (no single floor derivable) rather than
    guess. A single-CPU-architecture tag, or a compressed multi-tag segment
    where every listed single-CPU architecture agrees on the same target
    (redundant aliasing, the manylinux-legacy-tag analog), still resolves to
    that shared value.

    Returns a dotted ``"X.Y"`` string suitable for
    ``EnvironmentMatrix.runtime_floors["MACOS_DEPLOYMENT_TARGET"]``, or
    ``None`` if *name* carries no recognizable ``macosx_*`` tag, its tags
    disagree across architectures, or any tag names a multi-slice arch.
    """
    tag_segment = _wheel_platform_tag_segment(name)
    floor_by_arch: dict[str, tuple[int, int]] = {}
    for m in _MACOSX_TAG_RE.finditer(tag_segment):
        version = (int(m.group("major")), int(m.group("minor")))
        arch = m.group("arch")
        if arch not in floor_by_arch or version < floor_by_arch[arch]:
            floor_by_arch[arch] = version
    if any(arch in _MACOS_MULTI_SLICE_ARCH_TOKENS for arch in floor_by_arch):
        return None
    distinct_floors = set(floor_by_arch.values())
    if len(distinct_floors) != 1:
        return None
    best = next(iter(distinct_floors))
    return f"{best[0]}.{best[1]}"


#: Matches a PEP 425 Python tag: an implementation abbreviation (``cp`` CPython, ``pp`` PyPy, ``py`` generic, ``ip`` IronPython, ``jy`` Jython) followed by a single-digit major version and one-or-more-digit minor version run together, e.g. ``cp311`` -> major ``3``, minor ``11``. A bare implementation with no minor digits (``py3``) is intentionally unmatched — it doesn't pin a specific minor version, so there's nothing useful to derive.
_WHEEL_PYTHON_TAG_RE = re.compile(r"^(?:cp|pp|py|ip|jy)(\d)(\d+)$")


def _python_version_from_wheel_filename(filename: str) -> str | None:
    """Derive ``python_version`` (e.g. ``"3.11"``) from a wheel filename's
    own Python tag, for use as a PEP 508 marker-evaluation environment.

    *filename* is a wheel filename ``{distribution}-{version}(-{build})?-
    {python tag}-{abi tag}-{platform tag}.whl`` (PEP 427); the Python tag is
    always the third-from-last ``-``-delimited segment regardless of
    whether an optional build tag is present. Returns ``None`` when
    *filename* isn't a recognizable wheel name, its Python tag doesn't pin
    a specific minor version (e.g. the generic ``py3``), or the ABI tag
    (second-from-last segment) names ``abi3`` — either exactly (``cp39-
    abi3-...``) or as one component of a PEP 425 compressed multi-tag ABI
    segment (``.``-joined, e.g. ``cp39-cp39.abi3-...``, which ``packaging``
    expands to *both* an exact-build tag and a stable-ABI tag). Either way
    the wheel genuinely installs on the named Python 3.x minor *and every
    later* one (the whole point of the stable/limited API), so it names a
    *floor*, not one exact minor; pinning ``python_version="3.9"`` would
    make a marker like ``python_version >= "3.10"`` wrongly evaluate
    inactive for an interpreter the wheel actually supports (verified
    against ``packaging.utils.parse_wheel_filename``: a ``cp39-cp39.abi3-
    ...`` filename expands to both ``cp39-cp39-...`` and ``cp39-abi3-...``
    tags) (Codex review).
    """
    if not filename.lower().endswith(".whl"):
        return None
    parts = filename[: -len(".whl")].split("-")
    if len(parts) < 5:
        return None
    if "abi3" in parts[-2].lower().split("."):
        return None
    m = _WHEEL_PYTHON_TAG_RE.match(parts[-3])
    return f"{m.group(1)}.{m.group(2)}" if m else None


def _python_full_version_from_wheel_filename(filename: str) -> str | None:
    """Derive ``python_full_version`` from a wheel filename's Python tag.

    A wheel tag only ever encodes major.minor (``cp311`` says nothing
    about the micro/patch version), so this appends a synthetic ``.0`` to
    :func:`_python_version_from_wheel_filename`'s result rather than
    guessing a specific patch release. That's still correct for any marker
    comparison written at the same minor-version granularity a wheel tag
    itself uses (e.g. ``python_full_version < "3.12"``/``>= "3.12"``,
    since PEP 440 version comparison places ``3.11.0`` on the correct side
    either way) — leaving ``python_full_version`` to the host default
    while ``python_version`` is correctly derived would otherwise let a
    marker written in the ``_full_version`` spelling evaluate against the
    wrong interpreter (Codex review). Only a marker checking an exact
    micro/patch version a wheel tag can't express at all would see a
    difference, which no derivation could fix.
    """
    version = _python_version_from_wheel_filename(filename)
    return f"{version}.0" if version is not None else None


def _implementation_version_from_wheel_filename(filename: str) -> str | None:
    """Derive ``implementation_version`` from a wheel filename's Python tag.

    Only attempted for CPython (``cp``) tags: PEP 508's
    ``implementation_version`` is ``sys.implementation.version`` formatted
    the same way as ``python_full_version`` (``packaging.markers``'s
    ``default_environment()`` computes both from the *same* underlying
    version info on CPython, so they're identical there), but for any
    other implementation it's *that implementation's own* release number
    (e.g. PyPy's ``7.3.x``), which a ``pp39`` wheel tag says nothing about
    at all — ``39`` there is CPython-ABI-compatibility version, not PyPy's
    own version. Deriving a CPython-style ``"3.9.0"`` for a PyPy wheel
    would therefore be actively wrong, not just imprecise, so this returns
    ``None`` for any non-``cp`` tag rather than guessing (Codex review).
    Otherwise delegates to :func:`_python_full_version_from_wheel_filename`
    (including its ``abi3``-floor handling), since the two markers are
    the same value for CPython.
    """
    if not filename.lower().endswith(".whl"):
        return None
    parts = filename[: -len(".whl")].split("-")
    if len(parts) < 5:
        return None
    if not parts[-3].lower().startswith("cp"):
        return None
    return _python_full_version_from_wheel_filename(filename)


#: Wheel Python-tag implementation abbreviation -> PEP 508 marker value, in each of the two marker spellings (``implementation_name`` uses ``sys.implementation.name``'s lowercase spelling; ``platform_python_implementation`` uses ``platform.python_implementation()``'s capitalized spelling). The generic ``py`` abbreviation is deliberately absent from both maps -- it's an implementation-agnostic tag (a pure-Python wheel meant to run under CPython, PyPy, or anything else), so it makes no implementation promise at all to derive.
_WHEEL_IMPLEMENTATION_NAMES = {
    "cp": "cpython",
    "pp": "pypy",
    "ip": "ironpython",
    "jy": "jython",
}
_WHEEL_PYTHON_IMPLEMENTATIONS = {
    "cp": "CPython",
    "pp": "PyPy",
    "ip": "IronPython",
    "jy": "Jython",
}


def _implementation_name_from_wheel_filename(filename: str) -> str | None:
    """Derive ``implementation_name`` (e.g. ``"cpython"``/``"pypy"``) from a
    wheel filename's own Python tag, the same way as
    :func:`_python_version_from_wheel_filename`.

    A ``pp39``-tagged (PyPy) wheel scanned while abicheck itself runs under
    CPython would otherwise have an ``implementation_name``-gated marker
    evaluate against the wrong implementation (Codex review). Returns
    ``None`` for a non-wheel filename, a Python tag that doesn't pin a
    specific minor version, or the implementation-agnostic generic ``py``
    tag (see :data:`_WHEEL_IMPLEMENTATION_NAMES`).
    """
    if not filename.lower().endswith(".whl"):
        return None
    parts = filename[: -len(".whl")].split("-")
    if len(parts) < 5:
        return None
    tag = parts[-3]
    if not _WHEEL_PYTHON_TAG_RE.match(tag):
        return None
    return _WHEEL_IMPLEMENTATION_NAMES.get(tag[:2])


def _platform_python_implementation_from_wheel_filename(filename: str) -> str | None:
    """Derive ``platform_python_implementation`` (e.g.
    ``"CPython"``/``"PyPy"``) from a wheel filename's own Python tag.

    A different, equally common PEP 508 marker spelling for the same
    distinction as :func:`_implementation_name_from_wheel_filename`
    (``platform_python_implementation == "PyPy"`` vs.
    ``implementation_name == "pypy"``); deriving only one still leaves a
    marker written in the other spelling falling back to the host running
    abicheck (Codex review). Same scope caveats as that function.
    """
    if not filename.lower().endswith(".whl"):
        return None
    parts = filename[: -len(".whl")].split("-")
    if len(parts) < 5:
        return None
    tag = parts[-3]
    if not _WHEEL_PYTHON_TAG_RE.match(tag):
        return None
    return _WHEEL_PYTHON_IMPLEMENTATIONS.get(tag[:2])


def _platform_system_from_wheel_filename(filename: str) -> str | None:
    """Derive ``platform_system`` (``"Linux"``/``"Darwin"``/``"Windows"``)
    from a wheel filename's own platform tag (the last ``-``-delimited
    segment before ``.whl``), for the same reason as
    :func:`_python_version_from_wheel_filename`: a
    ``python_version``-gated marker correctly scoped to the wheel's own
    interpreter is no help if a ``platform_system``-gated one right next to
    it still falls back to the host running abicheck (Codex review).

    Deliberately does *not* attempt ``platform_machine`` — a fat/universal
    macOS wheel (``macosx_11_0_universal2``) or a compressed multi-tag
    platform segment doesn't name a single unambiguous architecture, and
    guessing wrong would be worse than falling back to the host default.
    Returns ``None`` for a non-wheel filename, the pure-Python ``any`` tag
    (no platform binding at all), or an unrecognized platform tag prefix.
    """
    if not filename.lower().endswith(".whl"):
        return None
    parts = filename[: -len(".whl")].split("-")
    if len(parts) < 5:
        return None
    tag = parts[-1].lower()
    if tag.startswith(("manylinux", "musllinux", "linux")):
        return "Linux"
    if tag.startswith("macosx"):
        return "Darwin"
    if tag.startswith("win"):
        return "Windows"
    return None


def _sys_platform_from_wheel_filename(filename: str) -> str | None:
    """Derive ``sys_platform`` (``"linux"``/``"darwin"``/``"win32"``, i.e.
    Python's own ``sys.platform`` spelling) from a wheel filename's platform
    tag, the same way as :func:`_platform_system_from_wheel_filename`.

    ``sys_platform`` and ``platform_system`` are two different, both
    commonly-used PEP 508 marker spellings for the same OS distinction
    (``sys_platform == "darwin"`` vs. ``platform_system == "Darwin"``);
    deriving only one of them still leaves a marker written in the other
    spelling falling back to the host running abicheck (Codex review). Same
    scope caveats as that function (no ``platform_machine``, ``None`` for
    ``any``/unrecognized/non-wheel names).
    """
    if not filename.lower().endswith(".whl"):
        return None
    parts = filename[: -len(".whl")].split("-")
    if len(parts) < 5:
        return None
    tag = parts[-1].lower()
    if tag.startswith(("manylinux", "musllinux", "linux")):
        return "linux"
    if tag.startswith("macosx"):
        return "darwin"
    if tag.startswith("win"):
        return "win32"
    return None


def _os_name_from_wheel_filename(filename: str) -> str | None:
    """Derive ``os_name`` (``"posix"``/``"nt"``, i.e. Python's own
    ``os.name`` spelling) from a wheel filename's platform tag, the same
    way as :func:`_platform_system_from_wheel_filename`.

    ``os_name`` is a third, less common PEP 508 marker spelling for the
    same OS distinction as ``platform_system``/``sys_platform``
    (``os_name == "nt"`` vs. ``platform_system == "Windows"``); deriving
    only the other two spellings still leaves a marker written in this one
    falling back to the host running abicheck (Codex review). Same scope
    caveats as that function (no ``platform_machine``, ``None`` for
    ``any``/unrecognized/non-wheel names). Linux and macOS both map to
    ``"posix"`` since that's the shared ``os.name`` value on any POSIX
    platform, not a Linux-specific one.
    """
    if not filename.lower().endswith(".whl"):
        return None
    parts = filename[: -len(".whl")].split("-")
    if len(parts) < 5:
        return None
    tag = parts[-1].lower()
    if tag.startswith(("manylinux", "musllinux", "linux", "macosx")):
        return "posix"
    if tag.startswith("win"):
        return "nt"
    return None


#: Single-architecture suffixes a Linux wheel platform tag can end in. Order doesn't matter for correctness (each is ``$``-anchored, so e.g. ``ppc64`` can't spuriously match a ``...ppc64le`` tag), but longer/more-specific names are listed first for readability. ``riscv64``/``loongarch64`` are valid ``manylinux``/``musllinux`` single-arch suffixes too (``packaging``'s own ``_manylinux._ALLOWED_ARCHS`` includes both) — omitting them silently skipped ``wheel_tag_architecture_mismatch`` derivation for those wheels entirely (Codex review #583).
_WHEEL_LINUX_MACHINE_RE = re.compile(
    r"(x86_64|aarch64|i686|armv7l|ppc64le|ppc64|s390x|riscv64|loongarch64)$"
)


def _platform_machine_from_wheel_filename(filename: str) -> str | None:
    """Derive ``platform_machine`` from a wheel filename's platform tag, for
    the *single-architecture* Linux and macOS tags where it's unambiguous.

    Unlike :func:`_platform_system_from_wheel_filename`/
    :func:`_sys_platform_from_wheel_filename`, most wheel platform tags
    *do* name exactly one architecture (``manylinux_2_17_aarch64`` vs.
    ``..._x86_64`` are genuinely different, non-interchangeable wheels), so
    this is worth deriving where it's safe (Codex review). Still returns
    ``None`` for anything that isn't safe to guess: a fat/universal macOS
    wheel (``macosx_11_0_universal2``/``_universal``/``_intel``, which
    supports more than one architecture at once), a PEP 600 compressed
    multi-tag platform segment (``.``-joined, e.g.
    ``macosx_10_9_x86_64.macosx_11_0_arm64``) whose components don't all
    agree on the same single architecture (checking any one arch's slice
    of such a wheel would otherwise silently pick up another arch's
    marker evaluation — Codex review), Windows tags (``win32``/
    ``win_amd64``/``win_arm64`` don't map onto a single well-standardized
    ``platform.machine()`` string the way Linux/macOS tags do), the
    pure-Python ``any`` tag, or an unrecognized platform tag prefix.
    """
    if not filename.lower().endswith(".whl"):
        return None
    parts = filename[: -len(".whl")].split("-")
    if len(parts) < 5:
        return None
    machines: set[str] = set()
    for component in parts[-1].lower().split("."):
        if component.startswith(("manylinux", "musllinux", "linux")):
            m = _WHEEL_LINUX_MACHINE_RE.search(component)
            if m is None:
                return None
            machines.add(m.group(1))
        elif component.startswith("macosx"):
            if component.endswith("_x86_64"):
                machines.add("x86_64")
            elif component.endswith("_arm64"):
                machines.add("arm64")
            else:
                return None  # universal2/universal/intel: more than one arch
        else:
            return None
    return machines.pop() if len(machines) == 1 else None


def parse_wheel_architecture_claim(filename: str) -> str | None:
    """Public wrapper around :func:`_platform_machine_from_wheel_filename`
    for G27's wheel-tag-vs-binary architecture-mismatch check (the
    "wheel tag vs. the binary's actual recorded architecture" item from the
    SciPy/Scientific-Python Roadmap §3, tied to the wheel tag's *own* claim
    — as opposed to G13's existing ``elf_machine_changed``/
    ``macho_cpu_type_changed``, which compare two arbitrary binaries against
    each other, not a binary against its own wheel's filename promise).

    See that function's docstring for exactly which tags this safely
    derives a single architecture from: single-architecture Linux
    (``manylinux``/``musllinux``) and macOS tags only. Returns ``None`` for
    Windows tags, fat/universal macOS tags, ambiguous compressed multi-tag
    segments, or an unrecognized platform tag — a caller declaring
    ``runtime_floors["WHEEL_ARCH"]`` from this value therefore never
    declares a wrong-but-confident claim for those cases; the
    architecture-mismatch check simply doesn't run.
    """
    return _platform_machine_from_wheel_filename(filename)


# G26: a wheel's *.dist-info/METADATA declares its runtime dependencies — the "declared" side of the NumPy C-API compatibility-envelope check (the binary-evidence "required" side comes from numpy_capi.py). Mirrors parse_manylinux_glibc_floor's role for G10: a pure function callers wire in programmatically (see diff_numpy_capi.check_numpy_metadata_contract).

#: A real METADATA file is ordinarily a few KB even with a long dependency list; this bounds how much a single wheel's METADATA member is allowed to decompress to, so a malicious wheel can't zip-bomb this scan (a small compressed member declaring a tiny size that in fact decompresses to gigabytes) (CodeRabbit review).
_MAX_METADATA_SIZE = 1_048_576


def parse_wheel_numpy_requirement(
    wheel_path: Path, environment: dict[str, str] | None = None
) -> str | None:
    """Extract the declared ``numpy`` version-specifier range from a wheel's ``*.dist-info/METADATA`` (``Requires-Dist: numpy...``).

    Returns the specifier text (e.g. ``">=1.23.5,<3"``, or ``""`` for a bare ``Requires-Dist: numpy`` with no version constraint) for the numpy requirement(s) active for *environment*, or ``None`` when the wheel is unreadable, carries no ``.dist-info/METADATA`` member, or declares no such numpy dependency at all — including when the only ``numpy`` entry is gated behind an optional extra (e.g. ``numpy; extra == "test"``, only installed via ``pip install pkg[test]``, not a real runtime requirement) or an ordinary marker that doesn't hold for *environment*.

    When *environment* is omitted, ``python_version``, ``python_full_version``, ``implementation_version``, ``implementation_name``, ``platform_python_implementation``, ``platform_system``, ``sys_platform``, ``os_name``, and (for single-architecture Linux/macOS tags) ``platform_machine`` are derived from the wheel's *own* filename tags (e.g. ``cp39`` -> ``python_version="3.9"``/``python_full_version="3.9.0"``/``implementation_version="3.9.0"``/``implementation_name="cpython"``/``platform_python_implementation="CPython"``, a ``macosx_11_0_arm64`` platform tag -> ``platform_system="Darwin"``/``sys_platform="darwin"``/``os_name="posix"``/``platform_machine="arm64"``) rather than defaulting to the interpreter running abicheck — evaluating a marker gated on any of these against the wrong interpreter/implementation/OS/architecture could hide a real under-declared floor on a wheel built for a different Python, implementation, platform, or CPU than the one running the scan (Codex review; both implementation-marker and all three OS-marker spellings are covered since real-world metadata uses any of them). ``implementation_version`` is derived for CPython (``cp``) tags only — see :func:`_implementation_version_from_wheel_filename` for why guessing it for any other implementation (e.g. PyPy) would be actively wrong rather than merely imprecise, unlike every other marker derived here. Falls back to the interpreter's own environment for whichever of these the filename doesn't pin down (e.g. a bare directory-derived METADATA path, the pure-Python ``any`` platform tag, a fat/universal macOS wheel, or a Windows tag, whose ``platform_machine`` isn't derived at all).

    Known residual gap: a wheel tag naming a *range* rather than one exact value — either the Python-version axis (the ``abi3`` stable-ABI tag, ``cp39-abi3-...``, installable on Python 3.9 and every later 3.x minor; or a PEP 425 compressed multi-tag Python segment, ``cp310.cp311-...``, installable on either minor) or the architecture axis (a fat/universal macOS wheel, ``macosx_11_0_universal2``, or a PEP 600 compressed multi-tag platform segment spanning more than one architecture) — deliberately leaves the corresponding marker key(s) (``python_version``/``python_full_version``, or ``platform_machine``) undetermined rather than pinning a single (wrong) value (see :func:`_python_version_from_wheel_filename` and :func:`_platform_machine_from_wheel_filename`), which means those keys fall back to whatever interpreter/host abicheck is running on rather than being evaluated across the wheel's whole supported range — a real metadata gap that only affects some Python versions, or only one architecture slice, the wheel supports (e.g. a split requirement like ``numpy>=1.23; platform_machine == "x86_64"`` / ``numpy>=2; platform_machine == "arm64"``) could go undetected depending on the scanning host (Codex review). Correctly checking the full range/every architecture would mean evaluating markers at every value a wheel's metadata references along that axis and combining the results, which is meaningfully more than a wheel-tag-derivation fix; left as a known limitation of this G26-partial feature rather than attempted here.
    """
    try:
        with zipfile.ZipFile(wheel_path) as zf:
            metadata_info = next(
                (
                    info
                    for info in zf.infolist()
                    if info.filename.endswith(".dist-info/METADATA")
                ),
                None,
            )
            if metadata_info is None:
                return None
            # A METADATA file is ordinarily a few KB even with a long
            # dependency list; an attacker-controlled wheel could otherwise
            # declare a small compressed member that decompresses to
            # gigabytes (a zip bomb). Reject an oversized declared size
            # up front, then bound the actual read too rather than trusting
            # the declared size alone (CodeRabbit review).
            if metadata_info.file_size > _MAX_METADATA_SIZE:
                return None
            with zf.open(metadata_info) as f:
                raw = f.read(_MAX_METADATA_SIZE + 1)
            if len(raw) > _MAX_METADATA_SIZE:
                return None
            text = raw.decode("utf-8", errors="replace")
    except (OSError, zipfile.BadZipFile):
        return None
    if environment is None:
        derivers = (
            ("python_version", _python_version_from_wheel_filename),
            ("python_full_version", _python_full_version_from_wheel_filename),
            (
                "implementation_version",
                _implementation_version_from_wheel_filename,
            ),
            ("implementation_name", _implementation_name_from_wheel_filename),
            (
                "platform_python_implementation",
                _platform_python_implementation_from_wheel_filename,
            ),
            ("platform_system", _platform_system_from_wheel_filename),
            ("sys_platform", _sys_platform_from_wheel_filename),
            ("os_name", _os_name_from_wheel_filename),
            ("platform_machine", _platform_machine_from_wheel_filename),
        )
        derived = {
            key: value
            for key, derive in derivers
            if (value := derive(wheel_path.name)) is not None
        }
        environment = derived or None
    return parse_numpy_requirement_from_metadata(text, environment)


def parse_numpy_requirement_from_metadata(
    metadata_text: str, environment: dict[str, str] | None = None
) -> str | None:
    """Extract the declared ``numpy`` specifier from raw METADATA text.

    Split out from :func:`parse_wheel_numpy_requirement` so callers who
    already have the METADATA content (e.g. from a directory-based compare,
    no wheel zip involved) don't need to fabricate one. See that function's
    docstring for the return-value contract.

    *environment* is a PEP 508 marker environment override (``python_version``,
    ``platform_system``, etc.) used to decide which ``Requires-Dist: numpy``
    line is actually active; keys omitted from it fall back to the real
    environment (the interpreter running abicheck) — the same merge behavior
    as :meth:`packaging.markers.Marker.evaluate`, which this delegates to
    directly, including its built-in default of evaluating with an empty
    ``extra`` (a plain, non-extras install). That default is why an
    optional-extra-only requirement (``numpy; extra == "test"``) correctly
    evaluates inactive without any special-casing here — but so does an
    ordinary requirement that merely *mentions* ``extra`` alongside other
    conditions (``numpy; extra != "docs"``, ``numpy; extra == "test" or
    python_version >= "3.12"``), which a blanket "marker text contains the
    word extra" skip would incorrectly discard even though it's actually a
    real base-install requirement (CodeRabbit / Codex review). A wheel can
    legitimately declare more than one base numpy requirement split by
    markers, and more than one can be simultaneously active for a given
    environment (e.g. ``numpy>=1.23; python_version >= "3.9"`` and the
    stricter ``numpy>=2; python_version >= "3.12"`` are both true on Python
    3.12) — an installer enforces the *intersection* of every active
    constraint, not just the first one found, so returning only the first
    active line could under-report the real floor (Codex review).
    """
    from email import message_from_string
    from email.policy import default as _email_default_policy

    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.specifiers import SpecifierSet

    # Core Metadata is an RFC 5322-style header block: a long Requires-Dist value may be folded across physical lines with leading whitespace on the continuation lines. A plain `line.startswith("Requires-Dist:")` scan only sees the first physical line and mangles a folded specifier or marker, so parse it as real headers instead. The default (compat32) email policy preserves the raw fold (embedded newline + leading whitespace) in the returned value; ``policy.default`` is the one that actually joins a folded header into a single logical line (Codex review).
    headers = message_from_string(metadata_text, policy=_email_default_policy)
    # Marker.evaluate() only auto-defaults "extra" to "" on packaging>=22; this project's pinned floor is packaging>=21.0, whose evaluate() has no such default and raises UndefinedEnvironmentName on a bare `extra == "test"` marker instead of treating it as inactive. Seed it ourselves so behavior is identical across the whole supported range; a caller-supplied *environment* can still override it explicitly (Codex review).
    eval_environment = {"extra": "", **(environment or {})}
    found = False
    combined = SpecifierSet()
    for raw in headers.get_all("Requires-Dist") or ():
        try:
            req = Requirement(str(raw))
        except InvalidRequirement:
            continue
        if req.name.lower() != "numpy":
            continue
        if req.marker is not None and not req.marker.evaluate(eval_environment):
            continue  # marker inactive for this environment (extras included)
        found = True
        combined &= req.specifier
    return str(combined) if found else None
