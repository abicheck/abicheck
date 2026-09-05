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

"""Single-variant reads of `storage.import_bundle_facts`'s own
`VariantRef.sections[BUNDLE_COMPOSITION_SECTION_KIND]` layout, for a caller
that has only one variant's `root`/`variant_id` -- not the full
multi-artifact `PackageManifest` `export_bundle_facts` itself needs (a
single-artifact `project_snapshot_legacy.materialize_release_variant_
artifacts` sub-package never has one, only its own preserved `VariantRef`).

Split out of `import_bundle_facts.py` (ADR-062 A1.7, Codex review) purely to
keep that module under the storage layer's 800-line production cap; it
imports `_manifest_entry_for_export` from there rather than duplicating it.
`workflows.release_package` (`_release_match_key`'s real-filename fallback,
`read_embedded_manifest`) is the reader these two functions exist for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .bundle_facts_validation import (
    require_degraded_members_known,
    validated_degraded_members,
)
from .dto import (
    BUNDLE_COMPOSITION_SECTION_KIND,
    SectionDTO,
    bundle_composition_from_dto,
)
from .import_bundle_facts import _LIBRARY_NAME_KEY, _manifest_entry_for_export

__all__ = [
    "read_variant_composition_degraded_members",
    "read_variant_composition_library_filenames",
    "read_variant_composition_manifest_payload",
]


def _read_variant_composition(
    root: str | Path, variant_id: str
) -> dict[str, Any] | None:
    """*variant_id*'s own decoded `BUNDLE_COMPOSITION_SECTION_KIND` payload,
    `None` if genuinely absent (no readable variant, no section). Shared by
    the two readers below; a decode failure once the section is *present*
    raises rather than degrading to `None` (CodeRabbit, security finding).

    `ObjectRef.kind` is a caller-controlled label, not verified by
    `VariantRef`/`ObjectRef` construction -- a corrupted or hand-assembled
    package could map `BUNDLE_COMPOSITION_SECTION_KIND` to an `ObjectRef` of
    a different actual kind, so it is checked here rather than trusted (the
    same guard `bundle_facts_store.py`'s own now-retired project-level
    reader needed; Codex review, fresh evidence).
    """
    from ..project_snapshot_store import DirectoryObjectStore, read_variant_ref

    try:
        variant = read_variant_ref(root, variant_id)
    except Exception:
        return None
    composition_ref = variant.sections.get(BUNDLE_COMPOSITION_SECTION_KIND)
    if composition_ref is None:
        return None
    if composition_ref.kind != BUNDLE_COMPOSITION_SECTION_KIND:
        raise ValueError(
            f"{root}: variant {variant_id!r}'s "
            f"sections[{BUNDLE_COMPOSITION_SECTION_KIND!r}] names an "
            f"ObjectRef of kind {composition_ref.kind!r}, not "
            f"{BUNDLE_COMPOSITION_SECTION_KIND!r} -- the package is "
            "corrupted or was hand-edited"
        )
    raw = DirectoryObjectStore(root).get(composition_ref.digest)
    return bundle_composition_from_dto(SectionDTO.from_dict(raw))


def read_variant_composition_manifest_payload(
    root: str | Path, variant_id: str
) -> dict[str, Any] | None:
    """`export_bundle_facts`'s own manifest half, for a single-artifact
    materialized sub-package that has no full `PackageManifest`. A plain
    dict (`storage/` may not import `bundle_manifest`), `None` if absent.
    """
    composition = _read_variant_composition(root, variant_id)
    if composition is None:
        return None
    raw_manifest = composition.get("manifest")
    if raw_manifest is None:
        return None
    return {
        **raw_manifest,
        "provides": [
            _manifest_entry_for_export(entry) for entry in raw_manifest["provides"]
        ],
    }


def read_variant_composition_degraded_members(
    root: str | Path, variant_id: str
) -> dict[str, str]:
    """*variant_id*'s own composition `degraded_members` mapping (bundle
    key -> capture failure reason; ADR-065 D8), `{}` if absent -- the
    marker a stored package preserves so a later comparison records the
    member `failed` instead of diffing its ELF-only stand-in."""
    composition = _read_variant_composition(root, variant_id)
    if composition is None:
        return {}
    degraded = validated_degraded_members(composition.get("degraded_members", {}))
    if not degraded:
        return {}
    # Decision-bearing, so validated like every other reader of the marker
    # (Codex review): a hand-edited or directly constructed package whose
    # marker names no member of this variant would otherwise be re-keyed
    # away by the fan-out and vanish, leaving the package read as complete.
    require_degraded_members_known(
        degraded,
        _variant_member_keys(root, variant_id, composition),
        what=f"{root}: variant {variant_id!r} composition",
    )
    return degraded


def _variant_member_keys(
    root: str | Path, variant_id: str, composition: dict[str, Any]
) -> set[str]:
    """The bundle keys the selected variant actually stores: every stored
    artifact's own recorded library name -- never the composition's own
    ``library_filenames`` keys, which sit in the same untrusted payload as
    the marker and would let a hand-edited package vouch for itself (Codex
    review); *composition* is accepted only so a caller can pass the
    payload it already decoded."""
    from ..project_snapshot_store import read_artifact_ref, read_variant_ref

    del composition
    keys: set[str] = set()
    for artifact_id in read_variant_ref(root, variant_id).artifact_ids:
        name = read_artifact_ref(root, artifact_id).native_identity.get(
            _LIBRARY_NAME_KEY
        )
        if isinstance(name, str):
            keys.add(name)
    return keys


def read_variant_composition_library_filenames(
    root: str | Path, variant_id: str
) -> dict[str, str]:
    """*variant_id*'s own composition `library_filenames` mapping (bundle
    key -> real on-disk filename), `{}` if absent -- each artifact's own
    `native_identity` carries only the bundle key, which can differ from a
    live directory operand's own filename-derived key; `workflows.
    release_package._release_match_key` uses this to recover it.
    """
    composition = _read_variant_composition(root, variant_id)
    if composition is None:
        return {}
    return dict(composition.get("library_filenames", {}))
