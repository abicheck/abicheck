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

"""Build-system-neutral build evidence model (ADR-029 D1, D2).

``BuildEvidence`` is abicheck's own normalized schema for L3 build context.
Adapters for compile_commands.json, CMake File API, Ninja, Bazel, and Make
(ADR-029 D3–D7) all emit into this model; external formats never become the
stable public schema (ADR-028 D4). Stored as ``build/build_evidence.json``
inside an evidence pack.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

#: Build-evidence schema version, independent of the pack/snapshot versions.
from .comdat_groups import ComdatScan, collect_vague_linkage_symbols

BUILD_EVIDENCE_VERSION: int = 1


class TargetKind(str, Enum):
    SHARED_LIBRARY = "shared_library"
    STATIC_LIBRARY = "static_library"
    OBJECT_LIBRARY = "object_library"
    EXECUTABLE = "executable"
    INTERFACE = "interface"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    HIGH = "high"
    REDUCED = "reduced"
    UNKNOWN = "unknown"


@dataclass
class Generator:
    """A build-system generator that produced the tree (ADR-029 D1)."""

    kind: str = "generic"  # cmake | ninja | bazel | make | generic
    version: str = ""
    generator: str = ""  # e.g. CMake's backend "Ninja"

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "version": self.version, "generator": self.generator}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Generator:
        return cls(
            kind=str(d.get("kind", "generic")),
            version=str(d.get("version", "")),
            generator=str(d.get("generator", "")),
        )


@dataclass
class Toolchain:
    """A compiler/toolchain referenced by compile units (ADR-029 D4, D8)."""

    id: str  # "toolchain://gcc-14-cxx"
    path: str = ""
    compiler_id: str = ""  # "GNU" | "Clang" | "MSVC"
    version: str = ""
    language: str = ""  # "C" | "CXX"
    implicit_include_dirs: list[str] = field(default_factory=list)
    implicit_link_dirs: list[str] = field(default_factory=list)
    target_triple: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "compiler_id": self.compiler_id,
            "version": self.version,
            "language": self.language,
            "implicit_include_dirs": list(self.implicit_include_dirs),
            "implicit_link_dirs": list(self.implicit_link_dirs),
            "target_triple": self.target_triple,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Toolchain:
        return cls(
            id=str(d["id"]),
            path=str(d.get("path", "")),
            compiler_id=str(d.get("compiler_id", "")),
            version=str(d.get("version", "")),
            language=str(d.get("language", "")),
            implicit_include_dirs=list(d.get("implicit_include_dirs", [])),
            implicit_link_dirs=list(d.get("implicit_link_dirs", [])),
            target_triple=str(d.get("target_triple", "")),
        )


@dataclass
class Target:
    """A build target: library/executable mapping (ADR-029 D2)."""

    id: str  # "target://libfoo"
    name: str = ""
    kind: TargetKind = TargetKind.UNKNOWN
    build_system: str = "generic"
    source_files: list[str] = field(default_factory=list)
    public_headers: list[str] = field(default_factory=list)
    private_headers: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    visibility: str = "unknown"  # public | private | interface | unknown
    confidence: Confidence = Confidence.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "build_system": self.build_system,
            "source_files": list(self.source_files),
            "public_headers": list(self.public_headers),
            "private_headers": list(self.private_headers),
            "outputs": list(self.outputs),
            "dependencies": list(self.dependencies),
            "visibility": self.visibility,
            "confidence": self.confidence.value,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Target:
        return cls(
            id=str(d["id"]),
            name=str(d.get("name", "")),
            kind=_target_kind(d.get("kind")),
            build_system=str(d.get("build_system", "generic")),
            source_files=list(d.get("source_files", [])),
            public_headers=list(d.get("public_headers", [])),
            private_headers=list(d.get("private_headers", [])),
            outputs=list(d.get("outputs", [])),
            dependencies=list(d.get("dependencies", [])),
            visibility=str(d.get("visibility", "unknown")),
            confidence=_confidence(d.get("confidence")),
        )


@dataclass
class CompileUnit:
    """One translation-unit compile action (ADR-029 D2, D3)."""

    id: str  # "cu://src/foo.cpp#cfg:abc123"
    source: str = ""
    output: str = ""
    directory: str = ""
    target_id: str = ""
    compiler: str = ""  # "toolchain://gcc-14-cxx"
    argv: list[str] = field(default_factory=list)
    language: str = ""  # "C" | "CXX"
    standard: str = ""  # "c++20"
    defines: dict[str, str] = field(default_factory=dict)
    undefines: list[str] = field(default_factory=list)
    include_paths: list[str] = field(default_factory=list)
    system_include_paths: list[str] = field(default_factory=list)
    #: Parallel to ``include_paths``/``system_include_paths`` respectively
    #: (same index, own list per structured field -- deliberately NOT a
    #: shared value-identity set), recording whether each entry's ORIGINAL,
    #: as-written ``-I``/``-isystem`` operand was already absolute exactly
    #: as recorded in the real build, as opposed to a relative operand this
    #: pipeline resolved by joining it onto ``directory`` (round 30 Finding
    #: 2; round 31 Finding 1, Codex review, fresh evidence). A value-keyed
    #: ``set[str]`` of "explicitly absolute strings" cannot distinguish two
    #: *occurrences* that normalize to the identical final string -- e.g. a
    #: unit recording both ``-Iinclude`` (relative, resolves to
    #: ``directory/include``) and ``-I<directory>/include`` (already
    #: explicitly absolute) has both ``include_paths`` entries end up as the
    #: exact same string, and a value-based set would then treat BOTH as
    #: explicit. Position-aligned per-entry booleans keep each occurrence's
    #: own provenance independent, so one can be rebased under a leading
    #: ``env -C DIR`` prefix while the other correctly is not.
    #:
    #: A pack persisted by a version of abicheck that predates this field
    #: has both position-aligned lists absent (empty). That is
    #: indistinguishable, by list length alone, from a caller who simply
    #: never populated per-entry provenance for a directly-constructed
    #: ``CompileUnit`` (every pre-round-31 test in this repo, for one) --
    #: and those two cases need OPPOSITE defaults: a direct construction's
    #: omitted field should degrade to "derived" (rebase), the pre-
    #: round-30 behavior every existing caller already assumed, while a
    #: genuinely legacy PERSISTED pack must degrade to "unknown, do not
    #: rebase" (round 31 Finding 3, Codex review, fresh evidence) -- the
    #: pre-round-30 pipeline never rebased a structured include path at
    #: all, so an old, previously-correct persisted pack must not have its
    #: replay semantics silently changed by a rebase it was never subject
    #: to. ``include_provenance_known`` is the explicit signal that tells
    #: :meth:`explicit_or_unknown` which of the two defaults applies --
    #: ``True`` (the ordinary default, for every direct construction and
    #: every adapter-produced unit) trusts the paired lists at face value
    #: (falling back to "derived" only for a length mismatch, which should
    #: not occur on a real adapter-produced unit); ``False`` (set only by
    #: :meth:`from_dict` when loading a dict that has neither
    #: ``include_paths_explicit`` nor ``system_include_paths_explicit`` --
    #: the actual legacy-schema signal) forces "unknown, do not rebase"
    #: regardless of what the (necessarily empty) lists contain.
    include_provenance_known: bool = field(default=True, kw_only=True)
    #: ``kw_only=True`` (CodeRabbit review, fresh evidence): these fields
    #: were inserted after ``system_include_paths`` rather than appended at
    #: the end of the dataclass. Every in-repo caller already uses keyword
    #: arguments, but ``CompileUnit`` carries no explicit ``__init__`` and
    #: nothing stops an external positional caller from existing --
    #: per-field ``kw_only`` (the same convention already established by
    #: ``AbiSnapshot``/``Change`` in ``model.py``/``checker_types.py``) means
    #: a field inserted anywhere in the list can never silently shift what
    #: a positional caller's later arguments bind to.
    include_paths_explicit: list[bool] = field(default_factory=list, kw_only=True)
    system_include_paths_explicit: list[bool] = field(
        default_factory=list, kw_only=True
    )
    input_files: list[str] = field(default_factory=list)
    sysroot: str | None = None
    target_triple: str = ""
    abi_relevant_flags: list[str] = field(default_factory=list)
    raw_ref: str = ""  # content-addressed path under raw/

    def explicit_or_unknown(self, *, system: bool) -> list[bool]:
        """Per-entry "treat as explicitly absolute, do not rebase" flags for
        ``include_paths`` (``system=False``) or ``system_include_paths``
        (``system=True``).

        A genuinely legacy pack (``include_provenance_known`` is ``False``
        -- see its own docstring) degrades every entry to "do not rebase",
        regardless of list contents. Otherwise a length mismatch between
        the provenance list and its path list (a direct construction that
        never populated per-entry provenance at all) degrades every entry
        to "derived" (rebase), the pre-round-30 default every existing
        caller already assumed.
        """
        paths = self.system_include_paths if system else self.include_paths
        if not self.include_provenance_known:
            return [True] * len(paths)
        explicit = (
            self.system_include_paths_explicit
            if system
            else self.include_paths_explicit
        )
        if len(explicit) != len(paths):
            return [False] * len(paths)
        return list(explicit)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "output": self.output,
            "directory": self.directory,
            "target_id": self.target_id,
            "compiler": self.compiler,
            "argv": list(self.argv),
            "language": self.language,
            "standard": self.standard,
            "defines": dict(self.defines),
            "undefines": list(self.undefines),
            "include_paths": list(self.include_paths),
            "system_include_paths": list(self.system_include_paths),
            "include_provenance_known": self.include_provenance_known,
            "include_paths_explicit": list(self.include_paths_explicit),
            "system_include_paths_explicit": list(self.system_include_paths_explicit),
            "input_files": list(self.input_files),
            "sysroot": self.sysroot,
            "target_triple": self.target_triple,
            "abi_relevant_flags": list(self.abi_relevant_flags),
            "raw_ref": self.raw_ref,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CompileUnit:
        return cls(
            id=str(d["id"]),
            source=str(d.get("source", "")),
            output=str(d.get("output", "")),
            directory=str(d.get("directory", "")),
            target_id=str(d.get("target_id", "")),
            compiler=str(d.get("compiler", "")),
            argv=list(d.get("argv", [])),
            language=str(d.get("language", "")),
            standard=str(d.get("standard", "")),
            defines=dict(d.get("defines", {})),
            undefines=list(d.get("undefines", [])),
            include_paths=list(d.get("include_paths", [])),
            system_include_paths=list(d.get("system_include_paths", [])),
            # A legacy pack (persisted before round 30/31) has NEITHER key
            # at all -- the real "this pack predates per-entry include
            # provenance" signal (round 31 Finding 3) -- as opposed to a
            # modern pack that legitimately has no includes to record
            # provenance for. Falls back to deriving the flag from key
            # PRESENCE only when the pack itself predates
            # ``include_provenance_known`` too (every pack this PR
            # produces sets it explicitly, so this fallback only matters
            # for a hypothetical intermediate schema between the two).
            include_provenance_known=bool(
                d.get(
                    "include_provenance_known",
                    "include_paths_explicit" in d
                    or "system_include_paths_explicit" in d,
                )
            ),
            include_paths_explicit=list(d.get("include_paths_explicit", [])),
            system_include_paths_explicit=list(
                d.get("system_include_paths_explicit", [])
            ),
            input_files=list(d.get("input_files", [])),
            sysroot=d.get("sysroot"),
            target_triple=str(d.get("target_triple", "")),
            abi_relevant_flags=list(d.get("abi_relevant_flags", [])),
            raw_ref=str(d.get("raw_ref", "")),
        )


@dataclass
class LinkUnit:
    """One link action producing a shared/static library or executable (D2)."""

    id: str  # "link://libfoo.so"
    target_id: str = ""
    output: str = ""
    kind: str = "shared_library"
    inputs: list[str] = field(default_factory=list)
    linker_argv: list[str] = field(default_factory=list)
    version_script: str = ""  # exports.map / .def / version script
    soname: str = ""
    # The link action's own working directory (redacted, mirrors
    # CompileUnit.directory) -- populated only by adapters/make.py today
    # (dry-run transcript scraping, the one build system with no absolute-
    # path-carrying target graph to lean on instead). Empty when unknown
    # (every other adapter's own inputs/output are already absolute).
    # Additive field: defensive .get() parsing keeps this forward/backward
    # compatible without a BUILD_EVIDENCE_VERSION bump (Codex review).
    directory: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target_id": self.target_id,
            "output": self.output,
            "kind": self.kind,
            "inputs": list(self.inputs),
            "linker_argv": list(self.linker_argv),
            "version_script": self.version_script,
            "soname": self.soname,
            "directory": self.directory,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LinkUnit:
        return cls(
            id=str(d["id"]),
            target_id=str(d.get("target_id", "")),
            output=str(d.get("output", "")),
            kind=str(d.get("kind", "shared_library")),
            inputs=list(d.get("inputs", [])),
            linker_argv=list(d.get("linker_argv", [])),
            version_script=str(d.get("version_script", "")),
            soname=str(d.get("soname", "")),
            directory=str(d.get("directory", "")),
        )


@dataclass
class BuildOption:
    """A normalized, ABI-relevant build option (ADR-029 D9).

    ``key`` is a canonical option name (e.g. "std", "define:FOO",
    "visibility", "glibcxx_use_cxx11_abi"); ``value`` is the normalized value.
    ``abi_relevant`` marks options whose drift the build-evidence diff treats
    as a risk signal rather than mere quality noise.
    """

    key: str
    value: str = ""
    abi_relevant: bool = False
    scope: str = "global"  # global | target:<id> | compile-unit:<id>
    raw: str = ""  # original flag text, redacted

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "abi_relevant": self.abi_relevant,
            "scope": self.scope,
            "raw": self.raw,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BuildOption:
        return cls(
            key=str(d["key"]),
            value=str(d.get("value", "")),
            abi_relevant=bool(d.get("abi_relevant", False)),
            scope=str(d.get("scope", "global")),
            raw=str(d.get("raw", "")),
        )


def comdat_scan_requested() -> bool:
    """Whether a collection run should sweep object files for COMDAT groups.

    Off by default. Parsing every object file's symbol table is real I/O on a
    large build, and no detector consumes the result yet — the demotion built
    on it was attempted and reverted (see AGENTS.md), so scanning by default
    would be an unbounded cost with no user-visible outcome (CodeRabbit
    review). Lives here rather than at the call site so ``inline.py``, which
    sits on its line-count cap, spends one line on the gate.
    """
    return os.environ.get("ABICHECK_COLLECT_COMDAT", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _resolved_object(cu: CompileUnit) -> Path | None:
    """The object file *cu* emitted, as a path that can actually be opened.

    ``CompileUnit.output`` is normalized *for persistence*, not for reading
    back: a home-rooted path is redacted to ``~/...`` (ADR-032 D7), and the
    Ninja/Make/Bazel adapters record an output relative to ``directory``. So a
    consumer that opens the field verbatim marks real objects unreadable
    whenever collection runs outside the build directory or under the user's
    home — silently turning "this build has objects" into "nothing scanned"
    (Codex/CodeRabbit review).

    ``None`` when the label names no file that exists, which is also what
    keeps the scan free on the common path: nothing is opened, and no ELF is
    parsed, for a build whose objects are gone or were never recorded.
    """
    if not cu.output:
        return None
    path = Path(cu.output).expanduser()
    if not path.is_absolute() and cu.directory:
        path = Path(cu.directory).expanduser() / path
    try:
        return path if path.is_file() else None
    except OSError:
        return None


@dataclass
class TargetScope:
    """Root-target scoping request/resolution for this build evidence (P0.2).

    A caller (``dump --build-target``, ``.abicheck.yml``'s ``build.targets``)
    can declare which specific build-system target(s) are the library under
    test instead of always collecting a workspace-wide query -- see
    ``BazelAdapter``/``build_query.run_inferred_build_query``. ``None`` on
    :attr:`BuildEvidence.target_scope` means no roots were requested (the
    ordinary, unscoped collection); a real :class:`TargetScope` (even with
    empty ``resolved``) means scoping was requested and this is what came of
    it -- so a consumer can always tell "not supported/not requested" from
    "requested but nothing resolved" (a typo'd target label, say).

    Field names deliberately mirror ``analysis_assurance.TargetAccounting``
    (P0.4, tracked separately) so that once that block's own root-target
    fields are wired, populating them from here is a direct copy rather than
    a translation.
    """

    #: The target labels the caller asked to scope collection to.
    requested: list[str] = field(default_factory=list)
    #: The subset of ``requested`` that a query actually resolved to a real
    #: build-system target (catches a typo'd/nonexistent label).
    resolved: list[str] = field(default_factory=list)
    #: Total target count reached transitively from ``resolved`` (i.e. the
    #: size of the scoped dependency closure), or ``None`` when unknown (no
    #: cquery/target-graph evidence was collected for this run).
    transitive_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested": list(self.requested),
            "resolved": list(self.resolved),
            "transitive_count": self.transitive_count,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TargetScope:
        return cls(
            requested=list(d.get("requested", [])),
            resolved=list(d.get("resolved", [])),
            transitive_count=(
                int(d["transitive_count"])
                if d.get("transitive_count") is not None
                else None
            ),
        )


def l3_coverage_fields(merged: BuildEvidence) -> dict[str, Any]:
    """The P0.2 root-target-scoping ``LayerCoverage`` kwargs for *merged*'s
    L3_build row, plus a ``detail_suffix`` prose fragment -- one shared
    implementation for the two call sites that build an L3_build row
    (``cli_buildsource_helpers._build_coverage`` and ``inline.
    build_inline_coverage``) so they cannot independently drift on this
    P0.2 field set. All five fields default empty/``None`` (and
    ``detail_suffix`` to ``""``) when *merged* carries no ``target_scope``
    or an empty ``requested`` list -- the "no scoping requested" case.
    """
    ts = merged.target_scope
    if ts is None or not ts.requested:
        return {
            "requested_roots": (),
            "resolved_roots": (),
            "transitive_targets": None,
            "compile_units": None,
            "link_units": None,
            "detail_suffix": "",
        }
    return {
        "requested_roots": tuple(ts.requested),
        "resolved_roots": tuple(ts.resolved),
        "transitive_targets": ts.transitive_count,
        "compile_units": len(merged.compile_units),
        "link_units": len(merged.link_units),
        "detail_suffix": (
            f"; scoped to {len(ts.requested)} root target(s) "
            f"({len(ts.resolved)} resolved)"
        ),
    }


@dataclass
class BuildEvidence:
    """Top-level normalized build evidence (ADR-029 D1)."""

    schema_version: int = BUILD_EVIDENCE_VERSION
    source_root: str = ""  # "repo://root" — redacted
    build_root: str = ""  # "build://root" — redacted
    generators: list[Generator] = field(default_factory=list)
    toolchains: list[Toolchain] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)
    compile_units: list[CompileUnit] = field(default_factory=list)
    link_units: list[LinkUnit] = field(default_factory=list)
    generated_files: list[str] = field(default_factory=list)
    build_options: list[BuildOption] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    raw_artifacts: list[str] = field(default_factory=list)
    #: COMDAT-group scan over this build's object files -- the only evidence
    #: that proves *vague linkage* (see ``comdat_groups``). ``None`` means no
    #: scan ran, which a consumer must never read as "nothing is vague".
    #: Additive field: defensive .get() parsing keeps this forward/backward
    #: compatible without a BUILD_EVIDENCE_VERSION bump, the same convention
    #: ``CompileUnit.directory`` above already documents.
    comdat: ComdatScan | None = None
    #: P0.2 root-target scoping (see :class:`TargetScope`). ``None`` when no
    #: roots were requested for this run -- additive, same forward/backward
    #: compatibility convention as ``comdat`` above.
    target_scope: TargetScope | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_root": self.source_root,
            "build_root": self.build_root,
            "generators": [g.to_dict() for g in self.generators],
            "toolchains": [t.to_dict() for t in self.toolchains],
            "targets": [t.to_dict() for t in self.targets],
            "compile_units": [c.to_dict() for c in self.compile_units],
            "link_units": [link.to_dict() for link in self.link_units],
            "generated_files": list(self.generated_files),
            "build_options": [o.to_dict() for o in self.build_options],
            "diagnostics": list(self.diagnostics),
            "raw_artifacts": list(self.raw_artifacts),
            # Omitted entirely when no scan ran, so an older reader and a
            # newer one agree that absence means "not established".
            **({"comdat": self.comdat.to_dict()} if self.comdat is not None else {}),
            **(
                {"target_scope": self.target_scope.to_dict()}
                if self.target_scope is not None
                else {}
            ),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BuildEvidence:
        return cls(
            schema_version=int(d.get("schema_version", BUILD_EVIDENCE_VERSION)),
            source_root=str(d.get("source_root", "")),
            build_root=str(d.get("build_root", "")),
            generators=[Generator.from_dict(g) for g in d.get("generators", [])],
            toolchains=[Toolchain.from_dict(t) for t in d.get("toolchains", [])],
            targets=[Target.from_dict(t) for t in d.get("targets", [])],
            compile_units=[
                CompileUnit.from_dict(c) for c in d.get("compile_units", [])
            ],
            link_units=[LinkUnit.from_dict(link) for link in d.get("link_units", [])],
            generated_files=list(d.get("generated_files", [])),
            build_options=[
                BuildOption.from_dict(o) for o in d.get("build_options", [])
            ],
            diagnostics=list(d.get("diagnostics", [])),
            raw_artifacts=list(d.get("raw_artifacts", [])),
            comdat=(
                ComdatScan.from_dict(d["comdat"])
                if isinstance(d.get("comdat"), dict)
                else None
            ),
            target_scope=(
                TargetScope.from_dict(d["target_scope"])
                if isinstance(d.get("target_scope"), dict)
                else None
            ),
        )

    def scan_comdat(self) -> None:
        """Populate ``comdat`` from the objects this build's compile units emit.

        Lives here rather than in ``comdat_groups`` so that module stays a
        leaf: it is a pure ELF parser and must not know about build evidence,
        or the two form an import cycle (CLAUDE.md "M1-3").

        Reads ``CompileUnit.output`` — already the normalized "this TU produced
        this object" fact, and the same field ``source_graph``'s link
        provenance mints its ``object_file`` nodes from — so no new discovery
        step is introduced. That field is a *persisted label*, though, not a
        usable path (see ``_resolved_object``), so it is resolved back to a
        real file before anything is opened.

        A no-op when no output resolves to a file on disk — a cleaned build
        tree, a compile DB describing a build never run, or a pack collected
        on another machine. Leaving ``comdat`` untouched there keeps "no scan
        ran" distinct from "scanned, found nothing vague" (only the latter may
        license a demotion) and, on the ``base_build`` path, keeps a scan
        loaded from an existing pack rather than replacing it with an empty
        one. For the same reason a fresh scan that established nothing never
        displaces one that did.
        """
        objects = [
            p for cu in self.compile_units if (p := _resolved_object(cu)) is not None
        ]
        if not objects:
            return
        scan = collect_vague_linkage_symbols(list(objects))
        if scan.resolvable or self.comdat is None:
            self.comdat = scan

    def merge(self, other: BuildEvidence) -> None:
        """Fold another adapter's output into this one (in place).

        Used by ``collect`` when several adapters run against the same
        tree (e.g. CMake File API for targets + compile DB for exact argv).
        De-duplicates by entity id so a compile unit collected twice is kept
        once (CMake File API wins on target facts, compile DB on argv).
        """
        self.generators.extend(other.generators)
        _merge_by_id(self.toolchains, other.toolchains)
        _merge_by_id(self.targets, other.targets)
        _merge_by_id(self.compile_units, other.compile_units)
        _merge_by_id(self.link_units, other.link_units)
        self.generated_files = sorted(
            set(self.generated_files) | set(other.generated_files)
        )
        # De-duplicate build options by (key, value) so running two adapters on
        # one tree (e.g. compile DB + Ninja) doesn't store the same option twice.
        seen_opts = {(o.key, o.value) for o in self.build_options}
        for opt in other.build_options:
            if (opt.key, opt.value) not in seen_opts:
                self.build_options.append(opt)
                seen_opts.add((opt.key, opt.value))
        self.diagnostics.extend(other.diagnostics)
        self.raw_artifacts = sorted(set(self.raw_artifacts) | set(other.raw_artifacts))
        # Union, for the same reason the scan unions across object files: an
        # entity emitted vaguely by any translation unit is vague for the
        # library, so two adapters over one tree must not lose one's findings.
        if other.comdat is not None:
            self.comdat = (
                other.comdat
                if self.comdat is None
                else ComdatScan(
                    symbols=self.comdat.symbols | other.comdat.symbols,
                    objects_scanned=self.comdat.objects_scanned
                    + other.comdat.objects_scanned,
                    objects_failed=self.comdat.objects_failed
                    + other.comdat.objects_failed,
                    diagnostics=[*self.comdat.diagnostics, *other.comdat.diagnostics],
                )
            )
        # Last-one-wins, mirroring `comdat`'s "other replaces when self is
        # unset" half: in practice only one adapter run ever carries a
        # TargetScope (the Bazel root-target query), so there is nothing
        # meaningful to union across adapters the way `comdat`'s symbol sets
        # are; other's presence always means it is the more recent request.
        if other.target_scope is not None:
            self.target_scope = other.target_scope


def _merge_by_id(dst: list[Any], src: list[Any]) -> None:
    seen = {item.id for item in dst}
    for item in src:
        if item.id not in seen:
            dst.append(item)
            seen.add(item.id)


def _target_kind(raw: Any) -> TargetKind:
    try:
        return TargetKind(raw if raw is not None else "unknown")
    except ValueError:
        return TargetKind.UNKNOWN


def _confidence(raw: Any) -> Confidence:
    try:
        return Confidence(raw if raw is not None else "unknown")
    except ValueError:
        return Confidence.UNKNOWN
