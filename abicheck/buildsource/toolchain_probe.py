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
tool is not the same as a tool with a matching identity. ``target`` is
checked coarsely (leading architecture component only, with a small
alias table for ``amd64``/``arm64``-style spellings) rather than a full
triple string match, since vendor/OS component spelling legitimately
differs between a `--target=` value and a `-dumpmachine` probe for the
same real toolchain.

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

import re
from collections.abc import Mapping
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


#: A dotted version number (2-4 components) is preferred over a bare one:
#: a cross-compiler binding's own name (e.g. ``x86_64-linux-gnu-gcc-13``,
#: a real, common Debian/Ubuntu naming convention) embeds bare digit groups
#: from its target triple (``86``, ``64``) that a real compiler's
#: ``--version`` banner echoes back verbatim as its first token — a bare
#: search would find one of *those* before the actual, always-dotted
#: version number that follows later in the same banner.
_DOTTED_VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+){0,2}")
_BARE_VERSION_RE = re.compile(r"\d+")
_CONSTRAINT_CLAUSE_RE = re.compile(r"^(==|!=|>=|<=|>|<)?\s*(\d+(?:\.\d+){0,3})$")


def _extract_version_token(text: str) -> str | None:
    match = _DOTTED_VERSION_RE.search(text)
    if match is not None:
        return match.group()
    match = _BARE_VERSION_RE.search(text)
    return match.group() if match is not None else None


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
    names = [
        Path(candidate).name.lower()
        for candidate in (metadata.get("selected", ""), metadata.get("realpath", ""))
        if candidate
    ]
    version_text = metadata.get("version", "").lower()

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
    if declared_family:
        actual_family = _probe_compiler_family(metadata)
        wanted_family = _FAMILY_ALIASES.get(
            declared_family.strip().lower(), declared_family.strip().lower()
        )
        if actual_family is not None and actual_family != wanted_family:
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
        probed_triple = metadata.get("target_triple")
        if probed_triple:
            declared_arch = _normalize_arch(declared_target.split("-", 1)[0])
            probed_arch = _normalize_arch(probed_triple.split("-", 1)[0])
            if declared_arch and probed_arch and declared_arch != probed_arch:
                errors.append(
                    f"{where}.target declares {declared_target!r} (architecture "
                    f"{declared_arch!r}) but toolchain binding {binding_id!r} "
                    f"({path!r}) resolved to target triple {probed_triple!r} "
                    f"(architecture {probed_arch!r})"
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
