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

"""Resolve a *declarative* library set against an already-built installation.

``actions/baseline``'s ``libraries`` input is concrete: every entry names one
artifact file and lists its headers literally. Producing that list from a real
installation tree -- globbing the installed headers, excluding the one that
belongs to a *different* component, picking the single real shared object out
of a directory that also holds its SONAME symlinks, and checking the result is
actually an ELF shared object of the expected machine -- is the work every
integrator was otherwise hand-rolling in shell (see ``docs/use/`` and the
PVXS integration this module was extracted from).

This module is that work, expressed once, with no project-specific knowledge
in it: it takes a *spec* using the same entry vocabulary as ``libraries``
(``name``/``artifact``/``header``/``include``/``stage_binary``) where the path
fields are glob patterns, plus ``header_exclude``, and returns the concrete
``libraries`` list. It reads the filesystem and nothing else -- no network, no
compiler, no analysis -- so every decision here is testable against a small
fixture tree.

Two rules are worth stating up front because they are the ones integrators get
wrong:

* **Symlinks are resolved, not banned.** A library directory normally holds
  ``libfoo.so`` and ``libfoo.so.1`` as symlinks onto the real
  ``libfoo.so.1.2.3``. A pattern matching all three is *not* ambiguous -- they
  are one artifact spelled three ways -- so matches are de-duplicated by
  resolved identity and only genuinely distinct files are an
  :data:`SelectionError`. What is refused is a link whose target escapes the
  declared root.
* **An include root is context, not an export obligation.** ``include`` is
  passed to the parser; it never widens the set of declarations the component
  is held responsible for. Only ``header`` (minus ``header_exclude``) does
  that, which is what lets two components share one installed header tree while
  owning disjoint parts of it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

#: ``actions/baseline``'s ``libraries`` entry serializes ``header``/``include``
#: as a single space-separated string, which its ``run.sh`` word-splits to
#: build ``abicheck dump -H``/``-I`` arguments. A resolved path containing any
#: of these characters cannot survive that round trip, so it is refused here --
#: explicitly and by name -- rather than silently splitting into two wrong
#: paths further downstream.
_UNREPRESENTABLE_IN_PATH = (" ", "\t", "\n", "\r", "\x1f")

#: ELF ``e_type`` value for a shared object. A component's artifact must be one:
#: an executable or a relocatable object has no exported-library ABI to compare.
_ET_DYN = 3

_HEADER_FIELDS = ("header", "header_exclude")


class SelectionError(Exception):
    """A declared component could not be resolved to exactly one real input.

    Always names the component and the pattern that failed, because the caller
    is a CI step whose only output is this message.
    """


@dataclass(frozen=True)
class ResolvedLibrary:
    """One component, resolved to concrete paths."""

    name: str
    artifact: Path
    headers: tuple[Path, ...] = ()
    includes: tuple[Path, ...] = ()
    stage_binary: bool = False
    #: pyelftools-style machine name (``EM_X86_64``), or ``""`` when the
    #: artifact's header could not be read as ELF and ``require_elf`` was off.
    machine: str = ""

    def as_entry(self) -> dict[str, Any]:
        """This component as an ``actions/baseline`` ``libraries`` entry."""
        entry: dict[str, Any] = {"name": self.name, "artifact": str(self.artifact)}
        if self.headers:
            entry["header"] = " ".join(str(p) for p in self.headers)
        if self.includes:
            entry["include"] = " ".join(str(p) for p in self.includes)
        if self.stage_binary:
            entry["stage_binary"] = True
        return entry


@dataclass
class _Spec:
    name: str
    artifact: str
    header: list[str] = field(default_factory=list)
    header_exclude: list[str] = field(default_factory=list)
    include: list[str] = field(default_factory=list)
    stage_binary: bool = False
    require_headers: bool = True


# ── ELF identity ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ElfIdentity:
    """The two ELF header fields component selection actually depends on."""

    e_type: int
    machine: str


#: The subset of ``e_machine`` values this repository's own fixtures and CI
#: lanes exercise. An unlisted value is reported by its numeric spelling
#: (``EM_47``) rather than rejected: the *agreement* check between components
#: is what matters here, and it works on any stable spelling.
_EM_NAMES = {
    3: "EM_386",
    8: "EM_MIPS",
    20: "EM_PPC",
    21: "EM_PPC64",
    40: "EM_ARM",
    62: "EM_X86_64",
    183: "EM_AARCH64",
    243: "EM_RISCV",
}


def read_elf_identity(path: Path) -> ElfIdentity | None:
    """``e_type``/``e_machine`` of *path*, or ``None`` when it is not ELF.

    Deliberately reads the 20-byte prefix itself rather than going through
    :mod:`abicheck.elf_metadata`: that parser builds a whole
    :class:`~abicheck.model.elf_facts.ElfMetadata` (symbols, dynamic entries,
    version tables) for a question answered by the file header, and this runs
    once per candidate *match*, including the symlink aliases that are about to
    be de-duplicated away.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(20)
    except OSError:
        return None
    if len(head) < 20 or head[:4] != b"\x7fELF":
        return None
    endian: Literal["little", "big"] = "little" if head[5] == 1 else "big"
    e_type = int.from_bytes(head[16:18], endian)
    e_machine = int.from_bytes(head[18:20], endian)
    return ElfIdentity(
        e_type=e_type, machine=_EM_NAMES.get(e_machine, f"EM_{e_machine}")
    )


# ── path resolution ─────────────────────────────────────────────────────────


def _within(root: Path, candidate: Path) -> bool:
    """Is *candidate* inside *root* after both are fully resolved?

    ``os.path.commonpath`` on the resolved pair, not a string prefix test: a
    sibling directory whose name merely starts with the root's (``/build``
    vs. ``/build-old``) is not inside it.
    """
    try:
        return os.path.commonpath([str(root), str(candidate)]) == str(root)
    except ValueError:  # different drives on Windows
        return False


def _check_representable(component: str, label: str, path: Path) -> None:
    text = str(path)
    for char in _UNREPRESENTABLE_IN_PATH:
        if char in text:
            raise SelectionError(
                f"{component}: {label} path {text!r} contains {char!r}, which the "
                "baseline Action's space-separated header/include contract cannot "
                "represent. Install or stage this input under a path with no "
                "whitespace or control characters."
            )


def _expand(root: Path, pattern: str) -> list[Path]:
    """Every existing path matching *pattern*, resolved, sorted, de-duplicated.

    An absolute pattern is honoured as given (an include root frequently lives
    outside the built tree -- a dependency's installed prefix); a relative one
    is taken against *root*.
    """
    base = Path(pattern)
    if base.is_absolute():
        anchor = Path(base.anchor)
        rel = str(base.relative_to(anchor))
        matches = (
            [Path(p) for p in anchor.glob(rel)]
            if _is_glob(rel)
            else ([base] if base.exists() else [])
        )
    else:
        matches = (
            [Path(p) for p in root.glob(pattern)]
            if _is_glob(pattern)
            else ([root / pattern] if (root / pattern).exists() else [])
        )
    seen: dict[str, Path] = {}
    for match in matches:
        try:
            resolved = match.resolve()
        except OSError:
            continue
        seen.setdefault(str(resolved), resolved)
    return [seen[key] for key in sorted(seen)]


def _is_glob(pattern: str) -> bool:
    return any(char in pattern for char in "*?[")


def _resolve_artifact(
    component: str, root: Path, pattern: str, *, require_elf: bool
) -> tuple[Path, str]:
    matches = _expand(root, pattern)
    matches = [m for m in matches if m.is_file()]
    inside = [m for m in matches if _within(root, m)]
    escaped = sorted(set(matches) - set(inside))
    if escaped:
        raise SelectionError(
            f"{component}: artifact pattern {pattern!r} resolves outside the declared "
            f"root {root}: {', '.join(str(p) for p in escaped)}. A library symlink is "
            "followed, but never out of the tree it was declared in."
        )
    if not inside:
        raise SelectionError(
            f"{component}: artifact pattern {pattern!r} matched no regular file under "
            f"{root}. Was the build run, and is this the installed (not the staging) tree?"
        )
    if len(inside) > 1:
        listed = ", ".join(str(p) for p in inside)
        raise SelectionError(
            f"{component}: artifact pattern {pattern!r} is ambiguous -- it matches "
            f"{len(inside)} distinct files under {root}: {listed}. Narrow the pattern; "
            "selecting one of several real shared objects is the caller's choice, not "
            "this resolver's."
        )
    artifact = inside[0]
    _check_representable(component, "artifact", artifact)
    identity = read_elf_identity(artifact)
    if identity is None:
        if require_elf:
            raise SelectionError(
                f"{component}: {artifact} is not an ELF object. Set require-elf: false "
                "to declare a component whose artifact is a PE or Mach-O image."
            )
        return artifact, ""
    if identity.e_type != _ET_DYN:
        raise SelectionError(
            f"{component}: {artifact} is an ELF object of type {identity.e_type}, not a "
            "shared object (ET_DYN). A static archive or an executable has no exported "
            "library ABI for this component to own."
        )
    return artifact, identity.machine


def _resolve_headers(component: str, root: Path, spec: _Spec) -> tuple[Path, ...]:
    selected: list[Path] = []
    for pattern in spec.header:
        matches = [m for m in _expand(root, pattern) if m.is_file()]
        if not matches:
            raise SelectionError(
                f"{component}: header pattern {pattern!r} matched no file under {root}. "
                "A header this component is declared to own must exist: silently owning "
                "nothing would report every one of its declarations as removed."
            )
        selected.extend(matches)

    excluded: set[Path] = set()
    for pattern in spec.header_exclude:
        matches = [m for m in _expand(root, pattern) if m.is_file()]
        if not matches:
            raise SelectionError(
                f"{component}: header_exclude pattern {pattern!r} matched no file under "
                f"{root}. An exclusion that excludes nothing is a stale declaration, and "
                "leaving it silent is how a component quietly re-acquires a header that "
                "belongs to a sibling."
            )
        excluded.update(matches)

    kept = sorted({p for p in selected if p not in excluded})
    for header in kept:
        if not _within(root, header):
            raise SelectionError(
                f"{component}: header {header} resolves outside the declared root {root}."
            )
        _check_representable(component, "header", header)
    if spec.header and not kept and spec.require_headers:
        raise SelectionError(
            f"{component}: every declared header was removed by header_exclude, leaving "
            "this component owning no declarations at all. Declare it with no header "
            "patterns if that is genuinely intended."
        )
    return tuple(kept)


def _resolve_includes(
    component: str, root: Path, patterns: list[str]
) -> tuple[Path, ...]:
    resolved: list[Path] = []
    for pattern in patterns:
        matches = [m for m in _expand(root, pattern) if m.is_dir()]
        if not matches:
            raise SelectionError(
                f"{component}: include pattern {pattern!r} matched no directory. An "
                "include root that does not exist changes how every header parses, so it "
                "is refused rather than dropped."
            )
        resolved.extend(matches)
    unique = sorted(set(resolved))
    for path in unique:
        _check_representable(component, "include", path)
    return tuple(unique)


# ── spec parsing ────────────────────────────────────────────────────────────


def _as_patterns(component: str, field_name: str, value: Any) -> list[str]:
    """A pattern field, accepting one string, a list, or absence.

    A bare string is accepted because it is what ``libraries`` already uses, but
    it is taken as ONE pattern -- it is deliberately *not* space-split. Splitting
    would make a path containing a space silently become two patterns that each
    match nothing, which is the failure this resolver exists to make impossible.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return [item for item in value if item]
    raise SelectionError(
        f"{component}: {field_name} must be a string or a list of strings, got {value!r}"
    )


def _parse_spec(index: int, raw: Any) -> _Spec:
    if not isinstance(raw, dict):
        raise SelectionError(
            f"entry {index} must be an object, got {type(raw).__name__}"
        )
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise SelectionError(f"entry {index} has no usable 'name'")
    if (
        "/" in name
        or "\\" in name
        or name in (".", "..")
        or any(ord(c) < 0x20 for c in name)
    ):
        raise SelectionError(
            f"entry {index}: component name {name!r} must not contain a path separator, "
            'be "." or "..", or contain control characters -- it is used as a filename.'
        )
    artifact = raw.get("artifact")
    if not isinstance(artifact, str) or not artifact:
        raise SelectionError(
            f"{name}: 'artifact' is required and must be a non-empty string"
        )
    stage_binary = raw.get("stage_binary", False)
    if not isinstance(stage_binary, bool):
        raise SelectionError(
            f"{name}: 'stage_binary' must be a boolean, got {stage_binary!r}"
        )
    require_headers = raw.get("require_headers", True)
    if not isinstance(require_headers, bool):
        raise SelectionError(
            f"{name}: 'require_headers' must be a boolean, got {require_headers!r}"
        )
    unknown = set(raw) - {
        "name",
        "artifact",
        "include",
        "stage_binary",
        "require_headers",
        *_HEADER_FIELDS,
    }
    if unknown:
        raise SelectionError(
            f"{name}: unrecognized field(s) {', '.join(sorted(unknown))}. A misspelled "
            "'header_exclude' would silently widen this component's owned surface."
        )
    return _Spec(
        name=name,
        artifact=artifact,
        header=_as_patterns(name, "header", raw.get("header")),
        header_exclude=_as_patterns(name, "header_exclude", raw.get("header_exclude")),
        include=_as_patterns(name, "include", raw.get("include")),
        stage_binary=stage_binary,
        require_headers=require_headers,
    )


# ── entry point ─────────────────────────────────────────────────────────────


#: What a ``${NAME}`` placeholder may look like. Deliberately the shell's own
#: braced form and nothing else: no ``$NAME``, no ``$(...)``, no defaulting
#: (``${NAME:-x}``), no nesting. A component declaration is data, and the
#: moment a placeholder syntax grows an operator it is a language the caller
#: now has to reason about.
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def bind_declaration(spec: Any, bindings: Mapping[str, str]) -> Any:
    """Substitute ``${NAME}`` in *spec* from *bindings*, and nothing else.

    A component declaration that is checked into a repository cannot spell
    values its build system only decides at build time -- an EPICS project's
    ``${EPICS_HOST_ARCH}`` sits in the middle of ``lib/${EPICS_HOST_ARCH}/
    libfoo.so*``, and the include roots name a dependency's install prefix.
    Without somewhere to bind those, every such project writes its own
    substitution pass beside its own capture step, which is where this
    started.

    The rules are narrow on purpose, and each forecloses a specific way that
    pass goes wrong:

    * **Substitution is structural, not textual.** The document is walked as
      JSON and each string is rewritten in place. A ``sed``-style pass over
      the serialized text mangles a value containing ``&`` (which means "the
      whole match" in a replacement), emits invalid JSON for one containing a
      quote, eats a backslash, and fails outright on one containing the
      delimiter -- and an install prefix read out of a build configuration is
      not the caller's to constrain.
    * **The allowlist is the caller's map, and it is closed.** An unbound
      ``${NAME}`` is an error, never left as literal text and never filled
      from the environment. Leaving it would produce a path with a brace in
      it that fails later as a confusing "no such file"; reading the
      environment would make every variable in the runner an input to the
      component set.
    * **Values are literal.** They are inserted, never re-scanned, so a
      binding whose value itself contains ``${...}`` cannot expand again.
      One pass, no fixed point, no recursion to bound.

    *bindings* keys must be plain identifiers; a key that could not appear in
    a placeholder is refused rather than silently unused.
    """
    for name in bindings:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise SelectionError(
                f"binding name {name!r} is not an identifier, so no "
                "${...} placeholder could ever name it"
            )
        if not isinstance(bindings[name], str):
            raise SelectionError(
                f"binding {name!r} must be a string, got "
                f"{type(bindings[name]).__name__}"
            )

    def _one(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in bindings:
            known = ", ".join(sorted(bindings)) or "(none)"
            raise SelectionError(
                f"${{{name}}} is not a value this run binds; declared "
                f"bindings: {known}. An unbound placeholder is refused rather "
                "than left as text, which would fail later as a confusing "
                "'no such file', or filled from the environment, which would "
                "make every variable on the runner an input to the component "
                "set."
            )
        return bindings[name]

    def _walk(node: Any) -> Any:
        if isinstance(node, str):
            # `.sub` on the ORIGINAL string only: the replacement is never
            # re-scanned, so a bound value containing `${...}` is data.
            return _PLACEHOLDER.sub(_one, node)
        if isinstance(node, list):
            return [_walk(item) for item in node]
        if isinstance(node, dict):
            bound: dict[Any, Any] = {}
            for key, value in node.items():
                new_key = _walk(key)
                if new_key in bound:
                    # Two distinct declared keys that bind to the SAME key
                    # silently lose one -- last write wins, and the field
                    # that vanished is the one the author wrote first. That
                    # is a configuration error the author must see, not a
                    # dropped component discovered later as a mismatch.
                    raise SelectionError(
                        f"binding collapses two distinct keys onto {new_key!r} "
                        "in the same object -- one would silently overwrite the "
                        "other; give them values that stay distinct"
                    )
                bound[new_key] = _walk(value)
            return bound
        return node

    return _walk(spec)


def resolve_library_set(
    spec: Any,
    *,
    root: Path | str,
    require_elf: bool = True,
    require_same_machine: bool = True,
    bindings: Mapping[str, str] | None = None,
) -> list[ResolvedLibrary]:
    """Resolve a declarative component spec against the tree at *root*.

    *bindings*, when given, fills ``${NAME}`` placeholders in the declaration
    before it is parsed -- see :func:`bind_declaration` for exactly what that
    does and, more importantly, what it deliberately does not. Binding runs
    first so every field validates the value that will actually be used, not
    the template it came from.

    Raises :class:`SelectionError` -- never returns a partially-resolved set.
    A component that could not be resolved is a configuration error the caller
    must see before any dump runs, not a component quietly dropped from the
    baseline-set (which would later read as "this library was removed").
    """
    if bindings is not None:
        # `is not None`, not truthiness: an explicitly EMPTY binding map is a
        # caller stating "this declaration binds nothing", and must therefore
        # still refuse an unbound `${NAME}` rather than pass the template
        # through to be parsed as a literal path.
        spec = bind_declaration(spec, bindings)
    root = Path(root).resolve()
    if not root.is_dir():
        raise SelectionError(f"root {root} is not a directory")
    if not isinstance(spec, list) or not spec:
        raise SelectionError(
            "library spec must be a non-empty array of component objects"
        )

    specs = [_parse_spec(index, raw) for index, raw in enumerate(spec)]
    seen: dict[str, int] = {}
    for index, parsed in enumerate(specs):
        if parsed.name in seen:
            raise SelectionError(
                f"component {parsed.name!r} is declared twice (entries {seen[parsed.name]} "
                f"and {index}). Two entries under one name overwrite each other's snapshot "
                "while the manifest still lists both."
            )
        seen[parsed.name] = index

    resolved: list[ResolvedLibrary] = []
    for parsed in specs:
        artifact, machine = _resolve_artifact(
            parsed.name, root, parsed.artifact, require_elf=require_elf
        )
        resolved.append(
            ResolvedLibrary(
                name=parsed.name,
                artifact=artifact,
                headers=_resolve_headers(parsed.name, root, parsed),
                includes=_resolve_includes(parsed.name, root, parsed.include),
                stage_binary=parsed.stage_binary,
                machine=machine,
            )
        )

    by_artifact: dict[Path, str] = {}
    for library in resolved:
        if library.artifact in by_artifact:
            raise SelectionError(
                f"components {by_artifact[library.artifact]!r} and {library.name!r} resolve "
                f"to the same artifact {library.artifact}. Two components sharing one binary "
                "would be dumped twice and compared as if they were independent."
            )
        by_artifact[library.artifact] = library.name

    if require_same_machine:
        machines = {lib.machine for lib in resolved if lib.machine}
        if len(machines) > 1:
            detail = ", ".join(
                f"{lib.name}={lib.machine}" for lib in resolved if lib.machine
            )
            raise SelectionError(
                "the declared components were not all built for the same machine: "
                f"{detail}. Comparing a mixed-architecture set produces findings that "
                "describe the toolchain, not the change."
            )
    return resolved


def libraries_payload(resolved: list[ResolvedLibrary]) -> list[dict[str, Any]]:
    """*resolved* as the ``libraries`` array ``actions/baseline`` consumes."""
    return [library.as_entry() for library in resolved]
