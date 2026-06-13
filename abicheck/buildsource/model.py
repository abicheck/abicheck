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

"""BuildSourcePack manifest and coverage model (ADR-028 D1, D5, D7, D8).

These dataclasses are the abicheck-owned, JSON-serializable schema for the
evidence pack. They version *independently* from the ABI snapshot schema
(``BUILD_SOURCE_PACK_VERSION`` here vs. ``serialization.SCHEMA_VERSION``) so that
heavyweight source graphs and raw build dumps never bloat ordinary ABI dumps.

Nothing here parses binaries or runs external tools — the model is pure data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: Evidence-pack schema version. Bumped on any breaking change to the manifest
#: or normalized-fact layout. Independent of ``serialization.SCHEMA_VERSION``
#: (ADR-028 D8): the snapshot only stores a lightweight reference.
BUILD_SOURCE_PACK_VERSION: int = 1


class DataLayer(str, Enum):
    """Optional evidence layers added by ADR-028 on top of L0/L1/L2.

    L0 (binary), L1 (debug info), and L2 (header AST) are intrinsic to the
    ``AbiSnapshot`` and are not represented here; they are reported directly
    from the snapshot. These three are the *optional* augmentation layers.
    """

    L3_BUILD = "L3_build"           # build context: compile DB, CMake, Ninja, Bazel, Make
    L4_SOURCE_ABI = "L4_source_abi"  # per-TU source ABI replay (ADR-030)
    L5_SOURCE_GRAPH = "L5_source_graph"  # include/type/call/build graph (ADR-031)


class LayerConfidence(str, Enum):
    """Confidence qualifier for an evidence layer or joined entity (ADR-028 D5).

    ``HIGH`` — facts are directly observed (e.g. exact compile command, exported
    symbol). ``REDUCED`` — facts are inferred or partial (e.g. public/private
    header intent without rule metadata). ``UNKNOWN`` — provenance could not be
    established.
    """

    HIGH = "high"
    REDUCED = "reduced"
    UNKNOWN = "unknown"


class CoverageStatus(str, Enum):
    """Collection status for an evidence layer in the coverage table (D7)."""

    PRESENT = "present"            # collected in full
    PARTIAL = "partial"           # collected for a subset (e.g. changed headers only)
    NOT_COLLECTED = "not_collected"  # extractor not run / unavailable


@dataclass
class LayerCoverage:
    """One row of the evidence-coverage table (ADR-028 D7).

    Reported across all output formats so users can tell which findings are
    artifact-proven vs. build-context-only vs. source/graph-assisted.
    """

    layer: str                      # DataLayer value OR "L0"/"L1"/"L2" for intrinsic layers
    status: CoverageStatus = CoverageStatus.NOT_COLLECTED
    confidence: LayerConfidence = LayerConfidence.UNKNOWN
    detail: str = ""                # human-readable note, e.g. "CMake+Ninja, 142 compile units"

    @property
    def present(self) -> bool:
        return self.status in (CoverageStatus.PRESENT, CoverageStatus.PARTIAL)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "status": self.status.value,
            "confidence": self.confidence.value,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LayerCoverage:
        return cls(
            layer=str(d["layer"]),
            status=_coverage_status(d.get("status")),
            confidence=_confidence(d.get("confidence")),
            detail=str(d.get("detail", "")),
        )


@dataclass
class BuildSourceEntity:
    """Cross-layer canonical identity joining build/source/debug/binary facts.

    The ``entity_id`` is a stable content hash. ``binary_refs`` and
    ``build_refs`` are the join keys back to L0 symbols and L3 build targets
    (ADR-028 D5). Phase 0 defines the model; extractors populate it.
    """

    entity_id: str                  # "sha256:..."
    kind: str                       # function|variable|record|enum|typedef|macro|file|target|compile_unit|binary_symbol|build_option
    names: dict[str, str] = field(default_factory=dict)  # source_qualified, mangled, demangled, usr
    locations: list[dict[str, Any]] = field(default_factory=list)  # {path, line, column, origin}
    binary_refs: list[str] = field(default_factory=list)  # "elf:symbol:_ZN..."
    build_refs: list[str] = field(default_factory=list)   # "target://libfoo", "compile-unit://src/bar.cpp"
    confidence: LayerConfidence = LayerConfidence.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "kind": self.kind,
            "names": dict(self.names),
            "locations": list(self.locations),
            "binary_refs": list(self.binary_refs),
            "build_refs": list(self.build_refs),
            "confidence": self.confidence.value,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BuildSourceEntity:
        return cls(
            entity_id=str(d["entity_id"]),
            kind=str(d.get("kind", "")),
            names=dict(d.get("names", {})),
            locations=list(d.get("locations", [])),
            binary_refs=list(d.get("binary_refs", [])),
            build_refs=list(d.get("build_refs", [])),
            confidence=_confidence(d.get("confidence")),
        )


@dataclass
class ExtractorRecord:
    """Provenance for one extractor run — the reproducibility ledger (D8, ADR-032 D10).

    Beyond the core ``name``/``version``/``status``, the optional fields capture
    the full ADR-032 D10 ledger for an external/CLI extractor: the exact
    (redacted) ``command`` and its ``command_hash``, the declared
    ``capabilities``, ``started_at``/``finished_at`` wall-clock bounds, and any
    ``diagnostics``. They are *only emitted when set*, so a built-in adapter's
    record stays byte-for-byte what it was before ADR-032 (and old readers keep
    working). This ledger is carried into JSON/SARIF output (ADR-014).
    """

    name: str                       # e.g. "compile_commands", "cmake_file_api", "ninja"
    version: str = ""               # extractor/tool version
    status: str = "ok"             # ok | partial | failed | skipped
    inputs: list[str] = field(default_factory=list)   # redacted input descriptors
    artifacts: list[str] = field(default_factory=list)  # content-addressed paths under raw/ or normalized/
    detail: str = ""
    # ADR-032 D10 reproducibility ledger (optional; emitted only when populated).
    command: str = ""               # redacted command line of an external extractor
    command_hash: str = ""          # "sha256:..." over the command + inputs + versions
    capabilities: list[str] = field(default_factory=list)  # declared capability tokens
    started_at: str = ""            # ISO 8601
    finished_at: str = ""           # ISO 8601
    diagnostics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "version": self.version,
            "status": self.status,
            "inputs": list(self.inputs),
            "artifacts": list(self.artifacts),
            "detail": self.detail,
        }
        # Emit the D10 ledger fields only when populated so built-in adapters
        # serialize exactly as before (stable hashes, no test churn).
        if self.command:
            out["command"] = self.command
        if self.command_hash:
            out["command_hash"] = self.command_hash
        if self.capabilities:
            out["capabilities"] = list(self.capabilities)
        if self.started_at:
            out["started_at"] = self.started_at
        if self.finished_at:
            out["finished_at"] = self.finished_at
        if self.diagnostics:
            out["diagnostics"] = list(self.diagnostics)
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExtractorRecord:
        return cls(
            name=str(d["name"]),
            version=str(d.get("version", "")),
            status=str(d.get("status", "ok")),
            inputs=list(d.get("inputs", [])),
            artifacts=list(d.get("artifacts", [])),
            detail=str(d.get("detail", "")),
            command=str(d.get("command", "")),
            command_hash=str(d.get("command_hash", "")),
            capabilities=list(d.get("capabilities", [])),
            started_at=str(d.get("started_at", "")),
            finished_at=str(d.get("finished_at", "")),
            diagnostics=list(d.get("diagnostics", [])),
        )


@dataclass
class BuildSourceManifest:
    """The pack ``manifest.json`` (ADR-028 D8).

    ``source_root`` is redaction-aware: the real path is never stored, only a
    repo hash (ADR-032 D7). ``coverage`` carries one ``LayerCoverage`` per
    optional layer; intrinsic L0/L1/L2 coverage is computed from the snapshot
    at report time, not stored here.
    """

    build_source_pack_version: int = BUILD_SOURCE_PACK_VERSION
    abicheck_version: str = ""
    created_at: str = ""            # ISO 8601
    source_root: dict[str, Any] = field(
        default_factory=lambda: {"path_redacted": True, "repo_hash": ""}
    )
    inputs: dict[str, Any] = field(default_factory=dict)
    extractors: list[ExtractorRecord] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)  # content-addressed artifact digests
    coverage: list[LayerCoverage] = field(default_factory=list)
    redaction: dict[str, Any] = field(default_factory=dict)

    def coverage_for(self, layer: DataLayer | str) -> LayerCoverage | None:
        key = layer.value if isinstance(layer, DataLayer) else layer
        for c in self.coverage:
            if c.layer == key:
                return c
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "build_source_pack_version": self.build_source_pack_version,
            "abicheck_version": self.abicheck_version,
            "created_at": self.created_at,
            "source_root": dict(self.source_root),
            "inputs": dict(self.inputs),
            "extractors": [e.to_dict() for e in self.extractors],
            "artifacts": list(self.artifacts),
            "coverage": [c.to_dict() for c in self.coverage],
            "redaction": dict(self.redaction),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BuildSourceManifest:
        # Back-compat: packs written before the evidence→buildsource rename
        # store the version under the legacy ``evidence_pack_version`` key.
        ver = int(
            d.get("build_source_pack_version")
            or d.get("evidence_pack_version", BUILD_SOURCE_PACK_VERSION)
        )
        return cls(
            build_source_pack_version=ver,
            abicheck_version=str(d.get("abicheck_version", "")),
            created_at=str(d.get("created_at", "")),
            source_root=dict(d.get("source_root", {"path_redacted": True, "repo_hash": ""})),
            inputs=dict(d.get("inputs", {})),
            extractors=[ExtractorRecord.from_dict(e) for e in d.get("extractors", [])],
            artifacts=list(d.get("artifacts", [])),
            coverage=[LayerCoverage.from_dict(c) for c in d.get("coverage", [])],
            redaction=dict(d.get("redaction", {})),
        )


@dataclass
class BuildSourceRef:
    """Lightweight reference stored *inside* the ``AbiSnapshot`` (ADR-028 D8).

    Keeps old snapshot readers functional (ADR-015): this is an optional field,
    and the heavyweight pack lives out-of-band, content-addressed by
    ``content_hash``.
    """

    schema_version: int = BUILD_SOURCE_PACK_VERSION
    content_hash: str = ""          # "sha256:..." of the pack manifest+artifacts
    path_hint: str = ""             # e.g. "libfoo.evidence/" — advisory only
    coverage_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "content_hash": self.content_hash,
            "path_hint": self.path_hint,
            "coverage_summary": dict(self.coverage_summary),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BuildSourceRef:
        return cls(
            schema_version=int(d.get("schema_version", BUILD_SOURCE_PACK_VERSION)),
            content_hash=str(d.get("content_hash", "")),
            path_hint=str(d.get("path_hint", "")),
            coverage_summary=dict(d.get("coverage_summary", {})),
        )


def _confidence(raw: Any) -> LayerConfidence:
    try:
        return LayerConfidence(raw if raw is not None else "unknown")
    except ValueError:
        return LayerConfidence.UNKNOWN


def _coverage_status(raw: Any) -> CoverageStatus:
    try:
        return CoverageStatus(raw if raw is not None else "not_collected")
    except ValueError:
        return CoverageStatus.NOT_COLLECTED
