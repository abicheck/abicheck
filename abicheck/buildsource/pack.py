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

"""``BuildSourcePack``: the in-memory evidence-pack dataclass (ADR-028 D1).

Pure data + pure (no I/O) transforms only. Persistence -- ``load``/``write``/
``content_hash``/``verify_integrity``/``to_ref`` (the last needs the second,
so it moved too), which ADR-061 assigns to ``storage`` -- lives in the
sibling ``pack_io.py`` as free functions taking a ``BuildSourcePack``
instance, not methods here: ``model``'s ``AbiSnapshot.build_source`` field
is typed ``BuildSourcePack``, and ``model`` may not import ``storage``, so
this class itself must stay import-free of anything that touches ``json``/
``hashlib``/the filesystem. See ``pack_io.py``'s own module docstring for
the full account of why the split lands here rather than as a
method-preserving subclass or facade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .build_evidence import BuildEvidence
from .model import BuildSourceManifest
from .source_abi import SourceAbiSurface

if TYPE_CHECKING:
    from ..model.source_graph import SourceGraphSummary


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
    def empty(
        cls, root: Path | str, abicheck_version: str = "", created_at: str = ""
    ) -> BuildSourcePack:
        """Create a manifest-only pack in memory (not yet written)."""
        manifest = BuildSourceManifest(
            abicheck_version=abicheck_version,
            created_at=created_at,
        )
        return cls(root=Path(root), manifest=manifest)

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
    def from_embedded_dict(
        cls, data: dict[str, Any], root: Path | str = ""
    ) -> BuildSourcePack:
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
            from ..model.source_graph import SourceGraphSummary

            source_graph = SourceGraphSummary.from_dict(sg)
        return cls(
            root=Path(root),
            manifest=manifest,
            build_evidence=BuildEvidence.from_dict(be) if be else None,
            source_abi=SourceAbiSurface.from_dict(sa) if sa else None,
            source_graph=source_graph,
        )
