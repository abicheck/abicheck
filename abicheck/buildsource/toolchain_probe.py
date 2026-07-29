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

"""Toolchain-identity enforcement (G34 Phase A).

Validates that a ``profiles.<id>.compile``/``consumer_compile`` overlay's
declared ``compiler_family``/``compiler_version``/``target`` actually match
the real executable its ``binding`` resolves to, via a *trusted*
toolchain-bindings file (:mod:`.toolchain_bindings`) — the same trust
boundary that module documents: an auto-discovered ``.abicheck.yml`` may
declare a logical binding id and a family/version/target constraint, but
never a raw executable path, so the real probe only ever runs against a
path the bindings file (an explicit ``--config``/CI-managed source)
actually names. A resolved binding whose probe fails outright (wrong
format, not executable, times out) is reported as a probe error rather than
silently falling through to a basename-only family guess — an unusable
tool is not the same as a tool with a matching identity. Likewise, an
executable whose actual family can't be determined at all is a failure
whenever ``compiler_family``/``target`` is declared, not a silent pass —
otherwise the check becomes a no-op for any unrecognized binary.

``target`` is checked differently per family, since ``-dumpmachine``
means something different for each. For a **GCC-family** binding (fixed
single-target per install), it's compared via a coarse architecture/
OS-family/environment comparison (not a full triple string match, since
vendor spelling legitimately differs between a ``--target=`` value and a
``-dumpmachine`` probe for the same real toolchain) against the probed
``-dumpmachine`` output, which genuinely constrains what that binary can
compile for. For a **Clang-family** binding (inherently multi-target via
its own ``--target=`` flag, which the profile's compose logic already
passes explicitly), ``-dumpmachine`` only reports its *host default* — not
a constraint — so instead this actually invokes Clang with the declared
``--target=`` on a trivial empty translation unit
(:func:`_clang_accepts_target`) and checks whether it's accepted, catching
a genuinely bogus/misspelled target without rejecting a valid
cross-compilation profile.

Reuses :mod:`abicheck.dumper_toolchain`'s existing, cached, bounded
``--version``-capture plumbing (:func:`~abicheck.dumper_toolchain._tool_identity_metadata`)
rather than re-implementing subprocess handling here — this module only adds
the version-constraint grammar and its own family-detection
(:func:`_probe_compiler_family`), deliberately **not**
:func:`~abicheck.dumper_toolchain._compiler_family_from_toolchain`: that
helper's own docstring says it is a best-effort guess for
``profile_fingerprint`` stability, low-stakes because a wrong guess there
only affects cache-key text — not appropriate for what is now a hard
validation gate a wrong guess can make reject (or wrongly accept) a real
profile.

**Deliberately out of scope (documented limitation, not a bug):** MSVC
(``compiler_family: msvc``/a ``cl``/``cl.exe`` binding). ``cl.exe`` has no
``--version`` flag — the fixed probe this module reuses always runs exactly
that flag — so a resolved MSVC binding cannot be identity-checked with the
plumbing available here without a genuinely different, unverified probe
this project has no testable MSVC environment to validate against. A
declared MSVC family/binding is silently skipped rather than guessed at.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from ..dumper_toolchain import _tool_identity_metadata
from .toolchain_bindings import BindingsFile

#: Schema spelling -> internal label :func:`_probe_compiler_family` returns.
#: A profile may spell the GNU family ``"gcc"``/``"g++"`` (the schema's own
#: vocabulary, matching ``compile.binding`` ids like ``"gcc14"``); the probe
#: returns the internal ADR-050 label ``"gnu"`` for that same family.
_FAMILY_ALIASES: dict[str, str] = {
    "gcc": "gnu",
    "g++": "gnu",
    "gnu": "gnu",
    "clang": "clang",
    "clang++": "clang",
    "icx": "icx",
    "icpx": "icx",
}

#: Family spellings this module deliberately never probes — see module
#: docstring. Checked against the *declared* ``compiler_family`` value.
_UNPROBED_FAMILIES: frozenset[str] = frozenset({"msvc", "cl", "cl.exe"})

#: Common alternate spellings for a target triple's leading architecture
#: component, so ``target: amd64-...``/``arm64-...`` (Windows-flavored
#: spellings) don't false-positive against a GNU-triple probe's
#: ``x86_64``/``aarch64``.
_ARCH_ALIASES: dict[str, str] = {
    "amd64": "x86_64",
    "x64": "x86_64",
    "arm64": "aarch64",
}


def _normalize_arch(arch: str) -> str:
    arch = arch.strip().lower()
    return _ARCH_ALIASES.get(arch, arch)


#: Coarse OS-family markers checked anywhere in a target triple, since a
#: triple's vendor/OS/environment components are spelled inconsistently
#: across toolchains (``x86_64-w64-mingw32`` vs. ``x86_64-pc-windows-msvc``)
#: but a real OS mismatch (Windows vs. Linux, sharing the same architecture)
#: is exactly the case a leading-architecture-only check misses (Codex
#: review, fresh evidence: a declared ``x86_64-w64-mingw32`` target bound to
#: a real ``x86_64-linux-gnu`` gcc passed silently).
_OS_FAMILY_MARKERS: tuple[tuple[str, str], ...] = (
    ("mingw", "windows"),
    ("windows", "windows"),
    ("win32", "windows"),
    ("darwin", "darwin"),
    ("apple", "darwin"),
    # Checked before the plain "linux" marker: an Android triple is
    # conventionally spelled "<arch>-linux-android" (e.g.
    # "aarch64-linux-android"), which contains "linux" as a substring —
    # Android and desktop/server Linux are different enough ABI
    # environments to distinguish here.
    ("android", "android"),
    ("linux", "linux"),
    ("freebsd", "freebsd"),
    ("netbsd", "netbsd"),
    ("openbsd", "openbsd"),
)


def _os_family(triple: str) -> str | None:
    lowered = triple.lower()
    for marker, family in _OS_FAMILY_MARKERS:
        if marker in lowered:
            return family
    return None


#: Base libc/environment markers, since two triples can share the same
#: architecture and OS while targeting an incompatible ABI environment
#: (``x86_64-linux-musl`` vs. ``x86_64-linux-gnu`` both reduce to OS family
#: ``"linux"`` — Codex review, fresh evidence).
_ENV_BASE_MARKERS: tuple[str, ...] = ("gnu", "musl", "msvc")


def _env_family(triple: str) -> str | None:
    """Coarse ABI-environment label for a target triple's trailing
    component, e.g. ``"gnu"``, ``"gnueabihf"``, ``"musl"``.

    Returns the triple's last ``-``-separated component **verbatim**
    (lowercased) whenever it contains one of the base libc/environment
    markers above -- not just the bare marker itself. Earlier revisions
    enumerated each known ABI-variant suffix as its own marker
    (``gnueabihf``/``gnueabi``/``gnux32``/``gnuabi64``/``gnuabin32``/
    ``gnu_ilp32``, each added in its own review round after the previous
    enumeration was shown incomplete: ``arm-linux-gnueabi`` vs.
    ``arm-linux-gnueabihf``; ``x86_64-linux-gnu`` vs.
    ``x86_64-linux-gnux32``; ``mips64el-linux-gnuabi64`` vs.
    ``mips64el-linux-gnuabin32``; ``aarch64-linux-gnu`` vs.
    ``aarch64-linux-gnu_ilp32``) -- but that whack-a-mole approach can
    never enumerate every real GNU ABI suffix in existence, confirmed by a
    yet further review round finding ``powerpc-linux-gnuspe`` (PowerPC SPE)
    still collapsing to the same generic ``"gnu"`` bucket as
    ``powerpc-linux-gnu``. Returning the whole trailing component instead
    of a fixed marker preserves any suffix automatically -- known or not
    -- while still returning ``None`` (unverifiable, not a match) for a
    component that names no recognized libc/environment family at all
    (e.g. ``arm-none-eabi``).
    """
    parts = [p for p in triple.strip().split("-") if p]
    if not parts:
        return None
    last = parts[-1].lower()
    if any(marker in last for marker in _ENV_BASE_MARKERS):
        return last
    return None


@lru_cache(maxsize=64)
def _clang_accepts_target(selected_path: str, digest: str, target: str) -> bool | None:
    """Whether a Clang-family *selected_path* actually accepts ``--target=``
    *target* for real compilation — not just echoes it back verbatim.

    Unlike ``-dumpmachine`` (which a Clang binary accepts for any
    ``--target=`` value, valid or not, and simply prints back), an actual
    empty-translation-unit compile with ``--target=`` fails outright for an
    unknown/misspelled triple (confirmed empirically: ``clang
    --target=not-a-real-target -x c -fsyntax-only -`` exits nonzero, while a
    real cross-compilation target exits 0) — the check this module needs to
    actually validate a declared Clang cross-compilation target rather than
    exempting it outright (Codex review, fresh evidence: a misspelled
    ``target: not-a-real-target`` previously passed unconditionally).

    Classifies purely on **exit status**, not on any particular diagnostic
    wording. An earlier version matched the literal substring "unknown
    target triple" in stderr, which real Clang 17 (``"version 'target' in
    target triple 'not-a-real-target' is invalid"``) and Apple/macOS clang
    (yet another wording, confirmed by a real CI failure on macOS-latest)
    both fail to match — silently treating a genuinely invalid target as
    inconclusive on any Clang that doesn't happen to phrase the diagnostic
    exactly that way. This invocation is deliberately minimal and
    well-formed (a trivial empty translation unit, no other flags) — the
    only reasonably expected failure mode for a *successful* subprocess
    launch is the compiler rejecting the declared ``--target=``, so any
    nonzero exit is classified as a proven mismatch rather than
    inconclusive. *digest* (the resolved executable's content hash, from
    :func:`~abicheck.dumper_toolchain._tool_identity_metadata`) keys the
    cache per exact binary revision, mirroring that module's own caching
    convention. Returns ``None`` — skip the comparison, never guess a
    mismatch — only when the probe itself can't even run (missing
    executable, timeout, permission error): that failure to launch is not
    the same as a proven target mismatch.
    """
    try:
        process = subprocess.run(
            [selected_path, f"--target={target}", "-x", "c", "-fsyntax-only", "-"],
            input="",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=10,
            text=True,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return process.returncode == 0


#: A dotted version number (2-4 components) is preferred over a bare one:
#: a cross-compiler binding's own name (e.g. ``x86_64-linux-gnu-gcc-13``,
#: a real, common Debian/Ubuntu naming convention) embeds bare digit groups
#: from its target triple (``86``, ``64``) that a real compiler's
#: ``--version`` banner echoes back verbatim as its first token — a bare
#: search would find one of *those* before the actual, always-dotted
#: version number that follows later in the same banner.
_VERSION_KEYWORD_RE = re.compile(r"\bversion\s+(\d+(?:\.\d+){1,3})", re.IGNORECASE)
_DOTTED_VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+){0,2}")
_BARE_VERSION_RE = re.compile(r"\d+")
_CONSTRAINT_CLAUSE_RE = re.compile(r"^(==|!=|>=|<=|>|<)?\s*(\d+(?:\.\d+){0,3})$")


def _paren_spans(text: str) -> list[tuple[int, int]]:
    """Return ``(start, end)`` spans of every ``(...)`` region in *text*
    (end exclusive, non-nested -- a banner never nests parentheses in the
    shapes this module handles)."""
    spans = []
    stack: list[int] = []
    for i, ch in enumerate(text):
        if ch == "(":
            stack.append(i)
        elif ch == ")" and stack:
            spans.append((stack.pop(), i + 1))
    return spans


def _extract_version_token(text: str) -> str | None:
    """Extract the real compiler version from a raw ``--version`` banner.

    Restricted to the banner's first line: GCC/Clang always print the
    actual version there, and every line after it is copyright/warranty
    boilerplate that could otherwise contribute a spurious digit match.

    Tries four strategies, most-specific first:

    1. The dotted number immediately following the literal word
       ``"version"`` (case-insensitive) -- both plain Clang
       (``"clang version 18.1.3"``) and Apple/Xcode Clang
       (``"Apple clang version 16.0.0 (clang-1600.0.26.4)"``) spell the
       real version this way, immediately followed by an *unrelated*
       parenthetical build identifier that a bare "last dotted token"
       search would wrongly prefer (``"1600.0.26.4"`` instead of
       ``"16.0.0"``, rejecting a valid ``>=16,<17`` constraint -- Codex
       review, fresh evidence).
    2. Failing that (GCC's and Intel's own banners have no ``"version"``
       keyword), the *last* dotted match on the line that falls **outside**
       any ``(...)`` span: a build/package descriptor's own dotted digits,
       wherever they fall relative to the real version, are always
       parenthesized in every banner shape this module has seen -- GCC's
       "(Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0" parenthesizes the package
       descriptor *before* the real version, while Intel's oneAPI banner
       "2026.1.0 (2026.1.0.20260617)" parenthesizes the build identifier
       *after* it (Codex review, fresh evidence: the real Intel banner in
       ``tests/fixtures/g32/dpcpp/compiler_invocation.log`` made the
       unfiltered last-dotted-match heuristic pick the build identifier
       ``"2026.1.0.20260617"`` over the real ``"2026.1.0"``, rejecting a
       valid ``==2026.1.0``/``<=2026.1.0`` constraint) -- excluding
       parenthesized matches handles both directions uniformly.
    3. Failing that (no dotted match outside parentheses either), the last
       dotted match anywhere on the line, mirroring the earlier
       cross-compiler-prefix fix (``x86_64-pc-solaris2.11-gcc (GCC)
       13.2.0``, where the OS-version digits happen to fall outside
       parentheses too, but a hypothetical banner with no unparenthesized
       version at all should still extract something rather than nothing).
    4. Failing that, the last bare digit run on the line.
    """
    first_line = text.splitlines()[0] if text else ""
    keyword_match = _VERSION_KEYWORD_RE.search(first_line)
    if keyword_match is not None:
        return keyword_match.group(1)
    paren_spans = _paren_spans(first_line)
    dotted_iter = list(_DOTTED_VERSION_RE.finditer(first_line))
    outside_paren = [
        m.group()
        for m in dotted_iter
        if not any(ps <= m.start() < pe for ps, pe in paren_spans)
    ]
    if outside_paren:
        return outside_paren[-1]
    if dotted_iter:
        return dotted_iter[-1].group()
    bare_matches: list[str] = _BARE_VERSION_RE.findall(first_line)
    return bare_matches[-1] if bare_matches else None


#: Intel's oneAPI DPC++/C++ compiler is a clang-based driver under a
#: different name (``icx``/``icpx``, and its older ``dpcpp``/``dpcpp-cl``
#: aliases) -- the same alias set :mod:`abicheck.dumper_clang`'s
#: ``_CLANG_FAMILY_ALIAS_NAMES`` already models for AST-parsing purposes.
#: Checked via the resolved path's *stem* (not the substring-matched
#: ``name`` used for plain Clang below), since none of these names contain
#: "clang" and an exact-name check needs the extension stripped first
#: (``icpx.exe`` on Windows). Its ``--version`` banner
#: ("Intel(R) oneAPI DPC++/C++ Compiler ...") also never contains the
#: literal phrase "clang version", so without this the driver was
#: previously either indeterminate or (on the rare path where a symlink's
#: basename happened to mention "clang") misclassified as plain Clang --
#: CompilerFamily.ICX is already a recognized, distinct family in
#: :mod:`abicheck.build_mode` (Codex review, fresh evidence: a declared
#: ``compiler_family: icx`` profile was rejected unconditionally).
_INTEL_ONEAPI_ALIAS_NAMES = frozenset({"icx", "icpx", "dpcpp", "dpcpp-cl"})


def _probe_compiler_family(metadata: dict[str, str]) -> str | None:
    """Best-effort compiler family for *this* validation gate.

    Deliberately more conservative than
    :func:`~abicheck.dumper_toolchain._compiler_family_from_toolchain`
    (see module docstring for why that helper isn't reused here): checks
    both the *selected* path's basename and the resolved *realpath*'s
    basename, since a generic driver alias/symlink (``cc``, ``c++``, or a
    Windows-style ``gcc`` that is actually Clang under the hood) can name
    something generic while resolving to the real compiler binary. Falls
    back to a signature phrase in the probed ``--version`` banner text
    (GCC always prints "Free Software Foundation, Inc."; Clang always
    prints "clang version"). Returns ``None`` — skip the comparison,
    never guess — when nothing here is conclusive.
    """
    stems = [
        Path(candidate).stem.lower()
        for candidate in (metadata.get("selected", ""), metadata.get("realpath", ""))
        if candidate
    ]
    names = [
        Path(candidate).name.lower()
        for candidate in (metadata.get("selected", ""), metadata.get("realpath", ""))
        if candidate
    ]
    version_text = metadata.get("version", "").lower()

    if (
        any(stem in _INTEL_ONEAPI_ALIAS_NAMES for stem in stems)
        or "oneapi" in version_text
    ):
        return "icx"
    if any("clang" in name for name in names) or "clang version" in version_text:
        return "clang"
    if any(name in ("cl", "cl.exe") for name in names):
        return "msvc"
    if any("gcc" in name or "g++" in name for name in names):
        return "gnu"
    if "free software foundation" in version_text:
        return "gnu"
    return None


class ToolchainProbeError(ValueError):
    """A declared ``compiler_version`` constraint is not parseable."""


def _parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _pad(
    a: tuple[int, ...], b: tuple[int, ...]
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)), b + (0,) * (n - len(b))


def parse_version_constraints(spec: str) -> list[tuple[str, tuple[int, ...]]]:
    """Parse a comma-separated version-constraint spec (e.g. ``">=14.2,<15"``).

    Each clause is an optional comparison operator (``==``/``!=``/``>=``/
    ``<=``/``>``/``<``, default ``==`` when omitted) followed by a dotted
    version number (1-4 components). Raises :class:`ToolchainProbeError` for
    an unparseable clause, or if *spec* contains no clause at all (e.g.
    ``","`` or blank/whitespace) — a comma-only or empty spec would
    otherwise parse to zero constraints, and :func:`version_satisfies`'s
    all-clauses-must-hold loop would then vacuously report every probed
    compiler as satisfying it (Codex review, fresh evidence).
    """
    constraints: list[tuple[str, tuple[int, ...]]] = []
    for clause in spec.split(","):
        clause = clause.strip()
        if not clause:
            continue
        match = _CONSTRAINT_CLAUSE_RE.match(clause)
        if not match:
            raise ToolchainProbeError(
                f"invalid version constraint clause {clause!r} in {spec!r}"
            )
        op = match.group(1) or "=="
        constraints.append((op, _parse_version(match.group(2))))
    if not constraints:
        raise ToolchainProbeError(f"no version constraint clauses found in {spec!r}")
    return constraints


def version_satisfies(actual: str, constraint_spec: str) -> bool:
    """Return whether dotted version string *actual* satisfies *constraint_spec*.

    Every comma-separated clause must hold (AND semantics). Comparisons pad
    the shorter of the two dotted tuples with trailing zeros so ``"14"``
    satisfies ``">=14.0"``. Raises :class:`ToolchainProbeError` if
    *constraint_spec* doesn't parse, or if *actual* has no extractable
    version number.
    """
    token = _extract_version_token(actual)
    if token is None:
        raise ToolchainProbeError(f"no version number found in {actual!r}")
    actual_version = _parse_version(token)
    for op, wanted in parse_version_constraints(constraint_spec):
        a, w = _pad(actual_version, wanted)
        if op == "==" and a != w:
            return False
        if op == "!=" and a == w:
            return False
        if op == ">=" and a < w:
            return False
        if op == "<=" and a > w:
            return False
        if op == ">" and a <= w:
            return False
        if op == "<" and a >= w:
            return False
    return True


class _HasCompileOverlays(Protocol):
    """Structural subset of ``project_targets.ProfileSpec`` this module needs."""

    id: str
    compile: Any
    consumer_compile: Any


def _check_one_overlay(
    profile_id: str, overlay_key: str, compile_spec: Any, bindings_file: BindingsFile
) -> list[str]:
    binding_id = getattr(compile_spec, "binding", "") if compile_spec else ""
    declared_family = (
        getattr(compile_spec, "compiler_family", "") if compile_spec else ""
    )
    declared_version = (
        getattr(compile_spec, "compiler_version", "") if compile_spec else ""
    )
    declared_target = getattr(compile_spec, "target", "") if compile_spec else ""
    if not binding_id or not (declared_family or declared_version or declared_target):
        return []
    if declared_family.strip().lower() in _UNPROBED_FAMILIES:
        return []
    path = bindings_file.bindings.get(binding_id)
    if path is None:
        # Already reported by check_profile_bindings_resolve; avoid duplicating.
        return []
    if Path(path).stem.lower() in _UNPROBED_FAMILIES:
        # Same documented MSVC exemption as the declared_family check above,
        # but keyed on the *resolved* binding path instead: a profile that
        # declares only target:/compiler_version: (no compiler_family:) but
        # whose binding resolves to a real cl.exe still reached the probe
        # below, which fails outright since cl.exe has no --version flag --
        # reported as "could not be probed", contradicting this module's own
        # documented "a declared MSVC family/binding is silently skipped"
        # (CodeRabbit review, fresh evidence).
        return []

    where = f"profiles.{profile_id}.{overlay_key}"
    metadata = _tool_identity_metadata(path)
    version_text = metadata.get("version", "")
    if "error" in metadata or version_text.startswith("unavailable:"):
        # A regular file that exists but can't actually run (wrong format,
        # not executable, timed out) reports its failure inside `version`,
        # not `error` -- dumper_toolchain._tool_version_output() swallows
        # OSError/TimeoutExpired internally rather than raising them, so
        # _tool_identity_metadata()'s own try/except never sees it. Left
        # unchecked, a stale binding merely *named* like a real compiler
        # (e.g. "gcc") would still pass family matching on basename alone,
        # reporting successful validation for a tool that cannot execute at
        # all (Codex review, fresh evidence).
        reason = metadata.get("error") or version_text
        return [
            f"{where}: toolchain binding {binding_id!r} (resolved to {path!r}) "
            f"could not be probed: {reason}"
        ]

    errors: list[str] = []
    # Resolved once, needed by both the family check and the target check
    # (target's own semantics depend on whether the actual compiler is
    # Clang -- see below). An indeterminate family is itself a failure
    # whenever family or target is declared: silently passing an
    # unidentifiable executable defeats the point of a hard validation
    # gate (Codex review, fresh evidence -- a family-only profile
    # previously accepted literally any unrecognized binary).
    actual_family = (
        _probe_compiler_family(metadata)
        if (declared_family or declared_target)
        else None
    )
    if declared_family:
        if actual_family is None:
            errors.append(
                f"{where}.compiler_family declares {declared_family!r} but the "
                f"actual family of toolchain binding {binding_id!r} ({path!r}) "
                f"could not be determined from its resolved path or --version "
                f"output"
            )
        else:
            wanted_family = _FAMILY_ALIASES.get(
                declared_family.strip().lower(), declared_family.strip().lower()
            )
            if actual_family != wanted_family:
                errors.append(
                    f"{where}.compiler_family declares {declared_family!r} but "
                    f"toolchain binding {binding_id!r} ({path!r}) resolved to "
                    f"family {actual_family!r}"
                )
    if declared_version:
        try:
            satisfied = version_satisfies(metadata.get("version", ""), declared_version)
        except ToolchainProbeError as exc:
            errors.append(f"{where}.compiler_version: {exc}")
        else:
            if not satisfied:
                errors.append(
                    f"{where}.compiler_version declares {declared_version!r} but "
                    f"toolchain binding {binding_id!r} ({path!r}) reported version "
                    f"output {metadata.get('version', '')!r}, which does not satisfy it"
                )
    if declared_target:
        if actual_family is None:
            errors.append(
                f"{where}.target declares {declared_target!r} but the actual "
                f"family of toolchain binding {binding_id!r} ({path!r}) could "
                f"not be determined, so its target cannot be verified"
            )
        elif actual_family in ("clang", "icx"):
            # Clang is inherently multi-target: -dumpmachine (what
            # target_triple comes from) reports only its host default, not
            # a constraint -- the profile's own compose logic passes the
            # declared target explicitly via --target=, which a single
            # Clang binary genuinely honors for cross-compilation. Unlike
            # GCC (a fixed single-target binary per install), the bare
            # dumpmachine probe has nothing to compare here (Codex review,
            # fresh evidence: a real x86_64 Clang with target:
            # aarch64-linux-gnu was falsely rejected). But an entirely
            # bogus/misspelled target must still be rejected, so probe
            # Clang directly with the declared --target= instead (a
            # second review round, fresh evidence: target:
            # not-a-real-target previously passed unconditionally).
            # Intel's oneAPI icx/icpx driver shares this same branch: it is
            # a clang-based binary accepting the identical --target= flag
            # (abicheck.dumper_clang already treats it as clang-family for
            # AST-parsing purposes), so the same multi-target exemption and
            # real-probe validation apply.
            digest = metadata.get("sha256")
            if digest:
                accepted = _clang_accepts_target(path, digest, declared_target)
                if accepted is False:
                    errors.append(
                        f"{where}.target declares {declared_target!r} but "
                        f"toolchain binding {binding_id!r} ({path!r}) does not "
                        f"recognize it as a valid --target= triple"
                    )
                elif accepted is None:
                    # _clang_accepts_target itself returns None only when
                    # the probe compile couldn't even run (timeout, OSError)
                    # -- e.g. a wrapper that answers --version fine but
                    # hangs or fails on a real invocation. Silently
                    # accepting that leaves the declared target completely
                    # unverified, the same "can't verify -> error, not a
                    # silent pass" principle this module applies to every
                    # other unprobeable case (Codex review, fresh
                    # evidence).
                    errors.append(
                        f"{where}.target declares {declared_target!r} but "
                        f"toolchain binding {binding_id!r} ({path!r}) could "
                        f"not be probed for --target= support (the "
                        f"validation compile did not complete), so the "
                        f"declared target cannot be verified"
                    )
        else:
            probed_triple = metadata.get("target_triple")
            if not probed_triple:
                errors.append(
                    f"{where}.target declares {declared_target!r} but toolchain "
                    f"binding {binding_id!r} ({path!r}) has no probed target "
                    f"triple, so the declared target cannot be verified"
                )
            else:
                declared_arch = _normalize_arch(declared_target.split("-", 1)[0])
                probed_arch = _normalize_arch(probed_triple.split("-", 1)[0])
                arch_mismatch = bool(
                    declared_arch and probed_arch and declared_arch != probed_arch
                )
                # A mismatch is flagged whenever the two sides disagree,
                # including when only one side's OS/environment marker is
                # recognized -- an unrecognized marker on either side is
                # itself unverifiable, not evidence of a match. Only "both
                # unrecognized" is a genuine skip (nothing to compare on
                # either side). Previously this only fired when BOTH sides
                # were non-None, so an unrecognized declared OS/env (e.g.
                # `x86_64-pc-solaris2.11`, `arm-none-eabi`) silently passed
                # against any real probed triple (Codex review, fresh
                # evidence).
                declared_os = _os_family(declared_target)
                probed_os = _os_family(probed_triple)
                os_mismatch = bool(
                    (declared_os is not None or probed_os is not None)
                    and declared_os != probed_os
                )
                declared_env = _env_family(declared_target)
                probed_env = _env_family(probed_triple)
                env_mismatch = bool(
                    (declared_env is not None or probed_env is not None)
                    and declared_env != probed_env
                )
                if arch_mismatch or os_mismatch or env_mismatch:
                    mismatches = []
                    if arch_mismatch:
                        mismatches.append(
                            f"architecture {declared_arch!r} vs {probed_arch!r}"
                        )
                    if os_mismatch:
                        mismatches.append(f"OS {declared_os!r} vs {probed_os!r}")
                    if env_mismatch:
                        mismatches.append(
                            f"environment {declared_env!r} vs {probed_env!r}"
                        )
                    errors.append(
                        f"{where}.target declares {declared_target!r} but "
                        f"toolchain binding {binding_id!r} ({path!r}) resolved to "
                        f"target triple {probed_triple!r} ({'; '.join(mismatches)})"
                    )
    return errors


def check_profile_toolchain_identity(
    profiles: Mapping[str, _HasCompileOverlays], bindings_file: BindingsFile
) -> list[str]:
    """Return one human-readable error string per declared
    ``profiles.<id>.compile``/``consumer_compile`` ``compiler_family``/
    ``compiler_version`` that disagrees with the real executable its
    ``binding`` resolves to via *bindings_file* — empty when every declared
    constraint holds (including when nothing declares one, or a binding
    doesn't resolve at all — that's :func:`~.toolchain_bindings.check_profile_bindings_resolve`'s
    job, not this function's, so it isn't duplicated here).

    Never raises. A profile with no ``binding`` declared, or with neither
    ``compiler_family`` nor ``compiler_version`` declared, is silently
    skipped — nothing to enforce. A declared MSVC family is silently
    skipped too (see module docstring). A probe failure (tool not
    executable, resolved path missing on this host) is reported as an
    error, since a declared identity constraint that cannot be verified is
    not the same as one that holds.
    """
    errors: list[str] = []
    for profile in profiles.values():
        for overlay_key in ("compile", "consumer_compile"):
            compile_spec = getattr(profile, overlay_key, None)
            errors.extend(
                _check_one_overlay(profile.id, overlay_key, compile_spec, bindings_file)
            )
    return errors
