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

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: Build-evidence schema version, independent of the pack/snapshot versions.
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

    kind: str = "generic"           # cmake | ninja | bazel | make | generic
    version: str = ""
    generator: str = ""             # e.g. CMake's backend "Ninja"

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

    id: str                         # "toolchain://gcc-14-cxx"
    path: str = ""
    compiler_id: str = ""           # "GNU" | "Clang" | "MSVC"
    version: str = ""
    language: str = ""              # "C" | "CXX"
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

    id: str                         # "target://libfoo"
    name: str = ""
    kind: TargetKind = TargetKind.UNKNOWN
    build_system: str = "generic"
    source_files: list[str] = field(default_factory=list)
    public_headers: list[str] = field(default_factory=list)
    private_headers: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    visibility: str = "unknown"     # public | private | interface | unknown
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

    id: str                         # "cu://src/foo.cpp#cfg:abc123"
    source: str = ""
    output: str = ""
    directory: str = ""
    target_id: str = ""
    compiler: str = ""              # "toolchain://gcc-14-cxx"
    argv: list[str] = field(default_factory=list)
    language: str = ""              # "C" | "CXX"
    standard: str = ""              # "c++20"
    defines: dict[str, str] = field(default_factory=dict)
    undefines: list[str] = field(default_factory=list)
    include_paths: list[str] = field(default_factory=list)
    system_include_paths: list[str] = field(default_factory=list)
    sysroot: str | None = None
    target_triple: str = ""
    abi_relevant_flags: list[str] = field(default_factory=list)
    raw_ref: str = ""               # content-addressed path under raw/

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
            sysroot=d.get("sysroot"),
            target_triple=str(d.get("target_triple", "")),
            abi_relevant_flags=list(d.get("abi_relevant_flags", [])),
            raw_ref=str(d.get("raw_ref", "")),
        )


@dataclass
class LinkUnit:
    """One link action producing a shared/static library or executable (D2)."""

    id: str                         # "link://libfoo.so"
    target_id: str = ""
    output: str = ""
    kind: str = "shared_library"
    inputs: list[str] = field(default_factory=list)
    linker_argv: list[str] = field(default_factory=list)
    version_script: str = ""        # exports.map / .def / version script
    soname: str = ""

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
    scope: str = "global"           # global | target:<id> | compile-unit:<id>
    raw: str = ""                   # original flag text, redacted

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


@dataclass
class BuildEvidence:
    """Top-level normalized build evidence (ADR-029 D1)."""

    schema_version: int = BUILD_EVIDENCE_VERSION
    source_root: str = ""           # "repo://root" — redacted
    build_root: str = ""            # "build://root" — redacted
    generators: list[Generator] = field(default_factory=list)
    toolchains: list[Toolchain] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)
    compile_units: list[CompileUnit] = field(default_factory=list)
    link_units: list[LinkUnit] = field(default_factory=list)
    generated_files: list[str] = field(default_factory=list)
    build_options: list[BuildOption] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    raw_artifacts: list[str] = field(default_factory=list)

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
            compile_units=[CompileUnit.from_dict(c) for c in d.get("compile_units", [])],
            link_units=[LinkUnit.from_dict(link) for link in d.get("link_units", [])],
            generated_files=list(d.get("generated_files", [])),
            build_options=[BuildOption.from_dict(o) for o in d.get("build_options", [])],
            diagnostics=list(d.get("diagnostics", [])),
            raw_artifacts=list(d.get("raw_artifacts", [])),
        )

    def merge(self, other: BuildEvidence) -> None:
        """Fold another adapter's output into this one (in place).

        Used by ``collect-evidence`` when several adapters run against the same
        tree (e.g. CMake File API for targets + compile DB for exact argv).
        De-duplicates by entity id so a compile unit collected twice is kept
        once (CMake File API wins on target facts, compile DB on argv).
        """
        self.generators.extend(other.generators)
        _merge_by_id(self.toolchains, other.toolchains)
        _merge_by_id(self.targets, other.targets)
        _merge_by_id(self.compile_units, other.compile_units)
        _merge_by_id(self.link_units, other.link_units)
        self.generated_files = sorted(set(self.generated_files) | set(other.generated_files))
        # De-duplicate build options by (key, value) so running two adapters on
        # one tree (e.g. compile DB + Ninja) doesn't store the same option twice.
        seen_opts = {(o.key, o.value) for o in self.build_options}
        for opt in other.build_options:
            if (opt.key, opt.value) not in seen_opts:
                self.build_options.append(opt)
                seen_opts.add((opt.key, opt.value))
        self.diagnostics.extend(other.diagnostics)
        self.raw_artifacts = sorted(set(self.raw_artifacts) | set(other.raw_artifacts))


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
