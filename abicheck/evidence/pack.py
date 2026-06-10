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

"""On-disk EvidencePack: directory layout, content addressing, I/O (ADR-028 D1, D4).

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
from typing import Any

from .build_evidence import BuildEvidence
from .model import EvidencePackManifest, EvidencePackRef

MANIFEST_NAME = "manifest.json"
BUILD_EVIDENCE_REL = "build/build_evidence.json"

#: Sub-directories created for every pack so adapters have a stable place to
#: write. Empty directories are harmless and keep the layout self-documenting.
_PACK_SUBDIRS = ("build", "source", "graph", "toolchain", "raw", "normalized")


@dataclass
class EvidencePack:
    """In-memory view of an evidence pack rooted at ``root``.

    ``manifest`` is always present (Phase 0 supports an empty, manifest-only
    pack). ``build_evidence`` is the ADR-029 L3 payload, ``None`` until a build
    adapter runs.
    """

    root: Path
    manifest: EvidencePackManifest = field(default_factory=EvidencePackManifest)
    build_evidence: BuildEvidence | None = None

    # -- construction -------------------------------------------------------

    @classmethod
    def empty(cls, root: Path | str, abicheck_version: str = "", created_at: str = "") -> EvidencePack:
        """Create a manifest-only pack in memory (not yet written)."""
        manifest = EvidencePackManifest(
            abicheck_version=abicheck_version,
            created_at=created_at,
        )
        return cls(root=Path(root), manifest=manifest)

    @classmethod
    def load(cls, root: Path | str) -> EvidencePack:
        """Load a pack from disk. Raises ``FileNotFoundError`` if no manifest."""
        root = Path(root)
        manifest_path = root / MANIFEST_NAME
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"No evidence-pack manifest at {manifest_path}. "
                f"Expected a directory produced by `abicheck collect-evidence`."
            )
        manifest = EvidencePackManifest.from_dict(_read_json(manifest_path))
        build_evidence: BuildEvidence | None = None
        be_path = root / BUILD_EVIDENCE_REL
        if be_path.is_file():
            build_evidence = BuildEvidence.from_dict(_read_json(be_path))
        return cls(root=root, manifest=manifest, build_evidence=build_evidence)

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

        # Record content-addressed digests of the normalized payloads.
        self.manifest.artifacts = self._artifact_digests()
        _write_json(self.root / MANIFEST_NAME, self.manifest.to_dict())
        return self.root / MANIFEST_NAME

    def _artifact_digests(self) -> list[str]:
        """Return sorted ``sha256:<hex>`` digests of normalized payload files.

        Only normalized/canonical files contribute to the content hash; raw/
        provenance dumps are intentionally excluded so the same logical
        evidence hashes identically regardless of which tool produced it.
        """
        digests: list[str] = []
        be_path = self.root / BUILD_EVIDENCE_REL
        if be_path.is_file():
            digests.append("sha256:" + _file_sha256(be_path))
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
            "evidence_pack_version": self.manifest.evidence_pack_version,
            "artifacts": sorted(self.manifest.artifacts or self._artifact_digests()),
            "coverage": [c.to_dict() for c in self.manifest.coverage],
            "extractors": [e.name + "@" + e.version for e in self.manifest.extractors],
        }
        blob = json.dumps(ident, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(blob).hexdigest()

    def to_ref(self, path_hint: str = "") -> EvidencePackRef:
        """Build the lightweight snapshot reference (ADR-028 D8)."""
        return EvidencePackRef(
            schema_version=self.manifest.evidence_pack_version,
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
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
