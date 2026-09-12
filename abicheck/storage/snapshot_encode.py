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

"""``AbiSnapshot`` -> dict/JSON encoding -- the encode-direction half of the
storage-owned codec (ADR-061 gap E).

Split out of :mod:`abicheck.storage.snapshot_codec` purely to keep that
module under the ADR-061 new-file production line ceiling; the decode
direction lives in that module and its own siblings
(``snapshot_decode_declarations``, ``snapshot_reliability_flags``, ...).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from ..model import AbiSnapshot
from .entity_id_codec import encode_entity_ids, encode_sidecar_entity_ids
from .enum_codec import encode_platform_enums
from .fact_codec import encode_fact_fields
from .sectioned_document import to_sectioned_document
from .semantic_ir_codec import encode_semantic_ir
from .snapshot_schema_versions import SCHEMA_VERSION
from .surface_graph_codec import encode_surface_graph


def _sets_to_lists(obj: Any) -> Any:
    """Recursively convert any set to a sorted list for JSON serialization.

    dataclasses.asdict() does NOT convert set → list, so json.dumps() would
    raise TypeError. This post-processes the entire dict tree.
    """
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if isinstance(obj, dict):
        return {k: _sets_to_lists(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sets_to_lists(v) for v in obj]
    return obj


def snapshot_to_dict(snap: AbiSnapshot) -> dict[str, Any]:
    # asdict() would recursively copy the lazy lookup caches, and
    # surface_graph's potentially-large nodes/edges, for nothing --
    # encode_surface_graph() below unconditionally replaces the latter with
    # its own to_dict(), never this recursion (Codex review, PR #962). Clear
    # them for the call and restore after, so this stays pure from the caller.
    caches = (snap._func_by_mangled, snap._var_by_mangled, snap._type_by_name)
    graph = snap.surface_graph
    # ADR-063 Phase 6 (v38): asdict() recurses into a dict's KEYS, so an
    # OccurrenceId-keyed mapping raises (unhashable dict key) inside asdict()
    # itself -- encode_semantic_ir() below owns this field's encoding, from
    # the still-typed object, exactly as surface_graph's codec does.
    semantic_ir = snap.semantic_ir
    try:
        snap._func_by_mangled = snap._var_by_mangled = snap._type_by_name = None
        snap.surface_graph = None
        snap.semantic_ir = None
        d = asdict(snap)
    finally:
        snap._func_by_mangled, snap._var_by_mangled, snap._type_by_name = caches
        snap.surface_graph = graph
        snap.semantic_ir = semantic_ir
    d.pop("_func_by_mangled", None)
    d.pop("_var_by_mangled", None)
    d.pop("_type_by_name", None)
    # Runtime-only provenance qualifier — never persisted.
    d.pop("from_headers_inferred", None)
    # Runtime-only source-read licence — never persisted, by design. Writing it
    # would let a stored snapshot grant itself permission to re-read whatever
    # now lives at the ``source_header`` paths it records, which is exactly the
    # defect ``buildsource/source_inputs.py``'s contract forbids: a recorded
    # path is provenance, not a licence. A loaded snapshot therefore always
    # comes back with the field at its deny-by-default ``False``.
    d.pop("live_source_evidence", None)
    # If ``from_headers`` was only *inferred* (a legacy snapshot loaded without
    # the explicit key), do not persist it as explicit provenance: drop the key
    # so a reload re-runs the same inference and re-marks it inferred, rather
    # than promoting a guess to explicit provenance and re-enabling source-level
    # param-rename detection on DWARF-only baselines this is meant to suppress.
    if snap.from_headers_inferred:
        d.pop("from_headers", None)

    # ElfMetadata/PeMetadata/MachoMetadata enums -> strings (storage/enum_codec.py).
    encode_platform_enums(d)

    # ADR-063 Phase 0 (schema v26): see storage/fact_codec.py.
    encode_fact_fields(d)

    # Convert all sets → sorted lists (needed for AdvancedDwarfMetadata.packed_structs and ToolchainInfo.abi_flags; json.dumps raises TypeError on set objects), having first encoded the ADR-063 Phase 2 (c1) `entity_id` carrier (storage/entity_id_codec.py).
    converted: dict[str, Any] = _sets_to_lists(
        encode_sidecar_entity_ids(encode_entity_ids(d, snap), snap)
    )

    # BuildMode enums are (str, Enum), so dataclasses.asdict() carries
    # them through as Enum instances rather than plain strings; normalize
    # the build_mode subtree to bare strings for JSON serialization.
    bm = converted.get("build_mode")
    if isinstance(bm, dict):
        for k in ("compiler_family", "language_std", "stdlib", "glibcxx_dual_abi"):
            v = bm.get(k)
            if v is not None and not isinstance(v, str):
                bm[k] = v.value if hasattr(v, "value") else str(v)

    # The inline embedded BuildSourcePack carries Path/enum/set-bearing nested
    # models asdict() cannot faithfully serialize; replace the raw asdict output
    # with the pack's canonical inline form, or drop the key when nothing was embedded.
    if snap.build_source is not None:
        converted["build_source"] = snap.build_source.to_embedded_dict()
    else:
        converted.pop("build_source", None)
    encode_surface_graph(converted, snap)  # storage/surface_graph_codec.py
    encode_semantic_ir(converted, snap)  # storage/semantic_ir_codec.py (v38)

    # Embed schema version for forward-compatibility.
    # Placed at top level so loaders can inspect it without parsing the full snapshot.
    converted["schema_version"] = SCHEMA_VERSION

    return converted


def snapshot_to_json(snap: AbiSnapshot, indent: int = 2) -> str:
    # ADR-062/063 Phase 8 (redesign): the file this function's own callers
    # (`write_snapshot`/`save_snapshot`) actually write to disk is now the
    # single-file sectioned shape (`storage.sectioned_document`) by
    # default -- `snapshot_to_dict()` itself keeps returning the flat shape
    # unchanged for every other caller (tests, `canonical_form` comparisons,
    # programmatic manipulation); only the JSON-file boundary changes.
    # `snapshot_from_dict` transparently unwraps either shape, so an older
    # flat `.abi.json` a prior build wrote stays fully readable.
    return json.dumps(
        to_sectioned_document(
            snapshot_to_dict(snap), max_known_schema_version=SCHEMA_VERSION
        ),
        indent=indent,
    )


def snapshot_content_digest(snap: AbiSnapshot) -> str:
    """A canonical sha256 of *snap*'s serialized content.

    Shared by every caller of ``confidence.note_if_same_binary_compared``'s
    ``old_snapshot_digest``/``new_snapshot_digest`` fallback (Item 4 fix) --
    the typed-API path (``service_compare_pipeline.classify_compare_pair``)
    and the native CLI path (``frontends.cli.runtime._finalize_compare_
    result``) both need the identical digest for two snapshots to ever
    compare equal, so this is the one place that computation lives rather
    than being inlined at each call site (Codex review, fresh evidence:
    the CLI path was found to still be missing this fallback entirely).

    Memoized per snapshot for the duration of an open
    ``storage.snapshot_digest_cache.digest_scope`` -- see that module for
    why the memo is run-scoped rather than unbounded. The digest string
    itself is unchanged by that: it appears in report output and in cache
    keys, so it stays a plain sha256 over the same canonical JSON.
    """
    from .snapshot_digest_cache import memoized_digest

    return memoized_digest(snap, _uncached_snapshot_content_digest)


def _uncached_snapshot_content_digest(snap: AbiSnapshot) -> str:
    """The digest computation itself, with no memoization around it."""
    return hashlib.sha256(snapshot_to_json(snap).encode()).hexdigest()


def same_persisted_content(old: AbiSnapshot, new: AbiSnapshot) -> bool:
    """Whether *old* and *new* are provably the same persisted content.

    The canonical serialization, compared -- so this cannot disagree with
    :func:`snapshot_content_digest` about what "content" means, because it
    IS that digest. Its one consumer is
    ``policy.analysis_assurance_schema_staleness.schema_staleness_status``,
    which needs the question answered but sits in a layer that may not
    import ``storage`` (``architecture/modules.yaml``: ``policy -> model,
    compare``), so the answer is computed here and passed in.

    It replaced a field-by-field reimplementation of this projection in
    ``model`` (Codex review, PR #1229, the P1 and four P2s that followed
    it). Every one of those P2s was the same defect: a nested object whose
    persisted form is not its raw fields -- a rounded float, a derived id,
    a codec that reprojects a mapping through fixed keys, an aliased graph
    the codec writes once -- read as a content difference that the real
    serializer does not record. There is no such class here: the projection
    is not reproduced, it is executed.

    Sound in the direction used: equal serialization means every input a
    pairwise detector reads agrees on both sides, so no pairwise finding is
    possible. The converse is neither claimed nor needed -- inequality
    falls through to the ordinary degraded report, the safe direction.

    Fails closed on *any* encoding failure, for the same reason. A
    snapshot carrying a value the encoder cannot serialize is not provably
    the same content as anything, and the caller is a status field on an
    already-degraded path: answering "not provably equal" there costs an
    over-cautious ``degraded``, while letting the exception out would fail
    a comparison that used to complete.

    ``old is new`` short-circuits without serializing anything. That is
    not an optimization that weakens the answer: one object trivially has
    the same persisted content as itself, and the serializer is a pure
    function of the snapshot, so the digest comparison it replaces could
    only ever have returned ``True``. Structural ``==`` is deliberately
    *not* used as a second short-circuit -- it is sound in the accepting
    direction but not the rejecting one (two unequal snapshots can persist
    identically: a rounded float, a derived id), so it would answer only
    the case identity already answers while paying a deep comparison on
    every case it cannot answer.
    """
    if old is new:
        return True
    try:
        return snapshot_content_digest(old) == snapshot_content_digest(new)
    except Exception:
        return False
