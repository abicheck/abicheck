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

"""On-disk BuildSourcePack: directory layout, content addressing, I/O (ADR-028 D1, D4).

Layout::

    <pack>/
      manifest.json
      build/build_evidence.json              # optional (ADR-029)
      source/source_abi.json                 # optional (ADR-030)
      graph/source_graph_summary.json        # optional (ADR-031)
      toolchain/toolchain_fingerprints.json  # optional
      raw/<extractor>/<content-addressed>    # external tool output, for provenance
      normalized/<extractor>/<json>          # abicheck-owned normalized facts

Raw artifacts are for debugging/reproducibility only; normalized facts are the
only stable input to comparison and reporting (ADR-028 D4).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .build_evidence import BuildEvidence
from .model import BuildSourceManifest, BuildSourceRef
from .source_abi import SourceAbiSurface

if TYPE_CHECKING:
    from .source_graph import SourceGraphSummary

MANIFEST_NAME = "manifest.json"
BUILD_EVIDENCE_REL = "build/build_evidence.json"
SOURCE_ABI_REL = "source/source_abi.json"
SOURCE_GRAPH_REL = "graph/source_graph_summary.json"

#: Sub-directories created for every pack so adapters have a stable place to
#: write. Empty directories are harmless and keep the layout self-documenting.
_PACK_SUBDIRS = ("build", "source", "graph", "toolchain", "raw", "normalized")


@dataclass
class BuildSourcePack:
    """In-memory view of an evidence pack rooted at ``root``.

    ``manifest`` is always present (Phase 0 supports an empty, manifest-only
    pack). ``build_evidence`` is the ADR-029 L3 payload, ``None`` until a build
    adapter runs.
    """

    root: Path
    manifest: BuildSourceManifest = field(default_factory=BuildSourceManifest)
    build_evidence: BuildEvidence | None = None
    source_abi: SourceAbiSurface | None = None
    source_graph: SourceGraphSummary | None = None

    # -- construction -------------------------------------------------------

    @classmethod
    def empty(cls, root: Path | str, abicheck_version: str = "", created_at: str = "") -> BuildSourcePack:
        """Create a manifest-only pack in memory (not yet written)."""
        manifest = BuildSourceManifest(
            abicheck_version=abicheck_version,
            created_at=created_at,
        )
        return cls(root=Path(root), manifest=manifest)

    @classmethod
    def load(cls, root: Path | str) -> BuildSourcePack:
        """Load a pack from disk. Raises ``FileNotFoundError`` if no manifest."""
        root = Path(root)
        manifest_path = root / MANIFEST_NAME
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"No build-source pack manifest at {manifest_path}. "
                f"Expected a directory produced by `abicheck collect`."
            )
        manifest = BuildSourceManifest.from_dict(_read_json(manifest_path))
        build_evidence: BuildEvidence | None = None
        be_path = root / BUILD_EVIDENCE_REL
        if be_path.is_file():
            build_evidence = BuildEvidence.from_dict(_read_json(be_path))
        source_abi: SourceAbiSurface | None = None
        sa_path = root / SOURCE_ABI_REL
        if sa_path.is_file():
            source_abi = SourceAbiSurface.from_dict(_read_json(sa_path))
        source_graph: SourceGraphSummary | None = None
        sg_path = root / SOURCE_GRAPH_REL
        if sg_path.is_file():
            from .source_graph import SourceGraphSummary
            source_graph = SourceGraphSummary.from_dict(_read_json(sg_path))
        return cls(
            root=root,
            manifest=manifest,
            build_evidence=build_evidence,
            source_abi=source_abi,
            source_graph=source_graph,
        )

    # -- persistence --------------------------------------------------------

    def write(self) -> Path:
        """Write the pack to ``root`` and return the manifest path.

        Recomputes ``manifest.artifacts`` from on-disk normalized payloads so
        the content hash is stable and reproducible.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        for sub in _PACK_SUBDIRS:
            (self.root / sub).mkdir(parents=True, exist_ok=True)

        # Write normalized build evidence first so it is hashed as an artifact.
        # When the new run produced no build evidence, remove any stale file left
        # by an earlier collection into the same directory — otherwise load() and
        # the content hash would keep using evidence the new manifest says was
        # not collected.
        be_path = self.root / BUILD_EVIDENCE_REL
        if self.build_evidence is not None:
            be_path.parent.mkdir(parents=True, exist_ok=True)
            _write_json(be_path, self.build_evidence.to_dict())
        elif be_path.is_file():
            be_path.unlink()

        # Same stale-file discipline for the optional L4 source ABI surface.
        sa_path = self.root / SOURCE_ABI_REL
        if self.source_abi is not None:
            sa_path.parent.mkdir(parents=True, exist_ok=True)
            _write_json(sa_path, self.source_abi.to_dict())
        elif sa_path.is_file():
            sa_path.unlink()

        # …and the optional L5 source graph summary (ADR-031 D7).
        sg_path = self.root / SOURCE_GRAPH_REL
        if self.source_graph is not None:
            sg_path.parent.mkdir(parents=True, exist_ok=True)
            _write_json(sg_path, self.source_graph.to_dict())
        elif sg_path.is_file():
            sg_path.unlink()

        # Record content-addressed digests of the normalized payloads.
        self.manifest.artifacts = self._artifact_digests()
        _write_json(self.root / MANIFEST_NAME, self.manifest.to_dict())
        return self.root / MANIFEST_NAME

    def _artifact_digests(self) -> list[str]:
        """Return sorted ``sha256:<hex>`` digests of normalized payloads.

        Only normalized/canonical facts contribute to the content hash; raw/
        provenance dumps are intentionally excluded so the same logical
        evidence hashes identically regardless of which tool produced it.

        Each L3/L4/L5 payload is hashed from its on-disk file when the pack was
        written, else from the in-memory object's canonical JSON. The in-memory
        path matters for an *embedded* pack (``root=""``, single-artifact UX):
        it is never written to disk, so without this its digests would be empty
        and ``content_hash()`` would ignore the embedded build/source facts —
        two different inputs with the same coverage would collide (Codex P2).
        The two formulations hash identically (same canonical JSON as
        ``_write_json``), so on-disk and embedded packs of the same facts agree.
        """
        digests: list[str] = []
        be_path = self.root / BUILD_EVIDENCE_REL
        if be_path.is_file():
            digests.append("sha256:" + _file_sha256(be_path))
        elif self.build_evidence is not None:
            digests.append("sha256:" + _payload_sha256(self.build_evidence.to_dict()))
        sa_path = self.root / SOURCE_ABI_REL
        if sa_path.is_file():
            digests.append("sha256:" + _file_sha256(sa_path))
        elif self.source_abi is not None:
            digests.append("sha256:" + _payload_sha256(self.source_abi.to_dict()))
        sg_path = self.root / SOURCE_GRAPH_REL
        if sg_path.is_file():
            digests.append("sha256:" + _file_sha256(sg_path))
        elif self.source_graph is not None:
            digests.append("sha256:" + _payload_sha256(self.source_graph.to_dict()))
        normalized = self.root / "normalized"
        if normalized.is_dir():
            for p in sorted(normalized.rglob("*")):
                if p.is_file():
                    digests.append("sha256:" + _file_sha256(p))
        return sorted(set(digests))

    # -- content addressing -------------------------------------------------

    def content_hash(self) -> str:
        """Stable ``sha256:<hex>`` over the manifest identity + artifact digests.

        Excludes volatile fields (``created_at``) so two packs with identical
        evidence collected at different times hash the same.
        """
        ident = {
            "build_source_pack_version": self.manifest.build_source_pack_version,
            "artifacts": sorted(self.manifest.artifacts or self._artifact_digests()),
            "coverage": [c.to_dict() for c in self.manifest.coverage],
            "extractors": [e.name + "@" + e.version for e in self.manifest.extractors],
        }
        blob = json.dumps(ident, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(blob).hexdigest()

    def verify_integrity(self) -> bool:
        """Whether the on-disk normalized payloads still match ``manifest.artifacts``.

        ``content_hash`` trusts the digests recorded in the manifest, so on its
        own it cannot tell that a normalized file was edited after the pack was
        written. Recomputing the digests from disk and comparing them to the
        recorded list detects exactly that drift — used by the baseline registry
        to reject a tampered/partial stored pack (ADR-028 Phase 5). A manifest
        with no recorded artifacts (legacy/empty pack) is treated as intact.
        """
        recorded = sorted(self.manifest.artifacts or [])
        if not recorded:
            return True
        return recorded == self._artifact_digests()

    # -- inline embedding (single-artifact UX) ------------------------------

    def to_embedded_dict(self) -> dict[str, Any]:
        """Serialize the normalized facts for embedding *inline* in a snapshot.

        This is the single-artifact path: instead of leaving the pack as an
        out-of-band directory referenced by hash, the normalized L3/L4/L5 facts
        ride inside the ``.abi.json`` so ``compare old.json new.json`` works with
        no pack directories. Raw provenance under ``raw/`` is never embedded
        (ADR-028 D4) — only the normalized facts that feed comparison.
        """
        out: dict[str, Any] = {"manifest": self.manifest.to_dict()}
        if self.build_evidence is not None:
            out["build_evidence"] = self.build_evidence.to_dict()
        if self.source_abi is not None:
            out["source_abi"] = self.source_abi.to_dict()
        if self.source_graph is not None:
            out["source_graph"] = self.source_graph.to_dict()
        return out

    @classmethod
    def from_embedded_dict(cls, data: dict[str, Any], root: Path | str = "") -> BuildSourcePack:
        """Reconstruct an in-memory pack from snapshot-embedded facts.

        ``root`` is empty for an embedded pack (it has no on-disk directory).
        Defensive ``.get()`` parsing keeps a newer/hand-edited snapshot loadable.
        """
        manifest = BuildSourceManifest.from_dict(data.get("manifest", {}))
        be = data.get("build_evidence")
        sa = data.get("source_abi")
        sg = data.get("source_graph")
        source_graph = None
        if sg:
            from .source_graph import SourceGraphSummary
            source_graph = SourceGraphSummary.from_dict(sg)
        return cls(
            root=Path(root),
            manifest=manifest,
            build_evidence=BuildEvidence.from_dict(be) if be else None,
            source_abi=SourceAbiSurface.from_dict(sa) if sa else None,
            source_graph=source_graph,
        )

    def to_ref(self, path_hint: str = "") -> BuildSourceRef:
        """Build the lightweight snapshot reference (ADR-028 D8)."""
        return BuildSourceRef(
            schema_version=self.manifest.build_source_pack_version,
            content_hash=self.content_hash(),
            path_hint=path_hint or str(self.root),
            coverage_summary={
                c.layer: {"status": c.status.value, "confidence": c.confidence.value}
                for c in self.manifest.coverage
            },
        )


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object, got {type(data).__name__}")
    return data


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    # newline="\n" disables the platform newline translation Path.write_text would
    # otherwise apply (CRLF on Windows). The on-disk bytes must be the same LF form
    # _payload_sha256 hashes, so a pack's content_hash is reproducible across
    # platforms and an embedded pack agrees with its on-disk equivalent.
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _payload_sha256(payload: dict[str, Any]) -> str:
    """SHA-256 of a normalized payload using the same bytes ``_write_json`` writes.

    Hashing the identical canonical form (``indent=2``, ``sort_keys=True``,
    trailing newline) means an embedded pack and the on-disk pack of the same
    facts produce the same digest — so ``content_hash()`` is stable across the
    embedded/out-of-band representations.
    """
    blob = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
