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

import copy
import datetime
import enum
import hashlib
import json
import pathlib
from dataclasses import fields as dataclass_fields, is_dataclass
from typing import Any

from ..model import AbiSnapshot
from .entity_id_codec import encode_entity_ids, encode_sidecar_entity_ids
from .enum_codec import encode_platform_enums
from .fact_codec import encode_fact_fields
from .sectioned_document import to_sectioned_document
from .semantic_ir_codec import encode_semantic_ir
from .snapshot_schema_versions import SCHEMA_VERSION
from .surface_graph_codec import encode_surface_graph

# Leaf types that are immutable, so the "detached container" contract
# :func:`snapshot_to_dict` promises its callers is satisfied by sharing the
# value itself rather than copying it. Anything *not* listed here falls back
# to ``copy.deepcopy``, exactly as ``dataclasses.asdict`` did, so a mutable
# leaf can still never be aliased back into the caller's snapshot.
#
# Matched by **exact type**, never ``isinstance``. A subclass of an immutable
# built-in is not itself immutable -- ``class Tagged(str): ...`` with an
# instance attribute is an ordinary mutable object that ``isinstance(x, str)``
# happily accepts -- so an ``isinstance`` test would share it and let a
# mutation through the encoded document reach the caller's snapshot, which is
# exactly what this contract forbids and what ``asdict``'s unconditional
# ``deepcopy`` never allowed (CodeRabbit, PR #1323). A subclass falls through
# to ``deepcopy``: correct, and rare enough that the cost does not matter.
# ``datetime`` is listed alongside ``date`` because it *is* a ``date``
# subclass, and exact matching would otherwise send every timestamp to
# ``deepcopy``; the concrete ``Path`` flavours are listed for the same reason.
_IMMUTABLE_LEAF_TYPES: frozenset[type] = frozenset(
    {
        str,
        int,
        float,
        bool,
        bytes,
        complex,
        type(None),
        pathlib.PurePosixPath,
        pathlib.PureWindowsPath,
        pathlib.PosixPath,
        pathlib.WindowsPath,
        datetime.date,
        datetime.datetime,
        datetime.time,
        datetime.timedelta,
    }
)


def _encode_value(obj: Any) -> Any:
    """Project one model value into its JSON-shaped, detached equivalent.

    This is the *single* structural walk of the snapshot tree. It replaces
    the former ``dataclasses.asdict(snap)`` followed by a second full
    ``_sets_to_lists(...)`` pass: those built two complete container trees
    where one suffices, so the encoder's transient peak was roughly twice
    the encoded size before anything had been written.

    Semantics are ``asdict``'s, fused with the set-to-sorted-list conversion
    the second pass used to perform (``asdict`` leaves sets alone, and
    ``json.dumps`` raises ``TypeError`` on one):

    * a dataclass instance becomes a plain ``dict`` of its fields;
    * ``set``/``frozenset`` becomes a sorted ``list`` (elements are *not*
      recursed into, matching the pass this replaces);
    * ``list``/``tuple`` keep their type and are recursed into;
    * ``dict`` keeps its keys as-is and recurses into its values -- keys are
      left alone deliberately, since ``asdict`` recursing into a *key* is
      what made an ``OccurrenceId``-keyed mapping raise (unhashable dict
      key) rather than encode;
    * any other leaf is shared when provably immutable, and deep-copied
      otherwise.
    """
    if is_dataclass(obj) and not isinstance(obj, type):
        return {
            f.name: _encode_value(getattr(obj, f.name)) for f in dataclass_fields(obj)
        }
    # ``Enum`` stays an ``isinstance`` test: a member's type is its own enum
    # class, never ``Enum`` itself, so exact matching cannot express it -- and
    # sharing one is safe regardless of what the enum subclasses, because
    # members are singletons that ``deepcopy`` returns unchanged anyway.
    if type(obj) in _IMMUTABLE_LEAF_TYPES or isinstance(obj, enum.Enum):
        return obj
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if isinstance(obj, dict):
        return {k: _encode_value(v) for k, v in obj.items()}
    if isinstance(obj, tuple):
        if hasattr(obj, "_fields"):  # namedtuple, as asdict special-cases it
            return type(obj)(*[_encode_value(v) for v in obj])
        return tuple(_encode_value(v) for v in obj)
    if isinstance(obj, list):
        return [_encode_value(v) for v in obj]
    return copy.deepcopy(obj)


def _encode_dataclass_skipping(obj: Any, skip: frozenset[str]) -> dict[str, Any]:
    """:func:`_encode_value` over *obj*'s fields, omitting the names in *skip*.

    Omission happens at the point of the walk, which is what lets
    :func:`snapshot_to_dict` stay a pure function of its argument. The
    previous implementation instead *assigned ``None``* to those fields on
    the caller's live snapshot, ran ``asdict``, and restored them in a
    ``finally``: correct for one thread in isolation, but it made the
    snapshot observably wrong to anything else holding it for the duration
    of the encode -- a second serialization, a concurrent reader, or a
    reader in a thread this one never knew about.
    """
    return {
        f.name: _encode_value(getattr(obj, f.name))
        for f in dataclass_fields(obj)
        if f.name not in skip
    }


# Fields of ``AbiSnapshot`` the generic walk above must never descend into.
# Each is either a runtime-only lookup cache, or a field whose persisted form
# is owned by a dedicated codec that reprojects it from the still-typed
# object further down (never from this recursion's output).
_SNAPSHOT_SKIP_FIELDS = frozenset(
    {
        # Lazy lookup caches -- recursively copying them would cost the size
        # of the snapshot again for something that is never persisted.
        "_func_by_mangled",
        "_var_by_mangled",
        "_type_by_name",
        # Owned by encode_surface_graph() below, which unconditionally
        # replaces the key with the graph codec's own to_dict()
        # (Codex review, PR #962).
        "surface_graph",
        # ADR-063 Phase 6 (v38): owned by encode_semantic_ir() below. Its
        # OccurrenceId-keyed mapping is also exactly the shape ``asdict``
        # could not walk at all.
        "semantic_ir",
    }
)


#: ``DwarfMetadata``'s ODR-conflict fields (schema v51). Written only when
#: the producer looked for conflicts (or found one), so a snapshot whose
#: debug info was never walked for them -- BTF/CTF/PDB, a symbols-only dump
#: -- encodes byte-identically to v50.
_DWARF_ODR_KEYS = ("struct_odr_conflicts", "enum_odr_conflicts")


def _drop_unobserved_odr_conflicts(d: dict[str, Any]) -> None:
    dwarf = d.get("dwarf")
    if not isinstance(dwarf, dict):
        return
    for key in _DWARF_ODR_KEYS:
        if not dwarf.get(key):
            dwarf.pop(key, None)
    if not dwarf.get("odr_conflicts_observed"):
        dwarf.pop("odr_conflicts_observed", None)


def snapshot_to_dict(snap: AbiSnapshot) -> dict[str, Any]:
    """Encode *snap* into its canonical, fully-detached dictionary form.

    Pure with respect to *snap*: nothing here reads, writes, or temporarily
    clears a field on the caller's object, so serializing a snapshot another
    thread is concurrently reading is safe.
    """
    d = _encode_dataclass_skipping(snap, _SNAPSHOT_SKIP_FIELDS)
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
    _drop_unobserved_odr_conflicts(d)

    # ADR-063 Phase 0 (schema v26): see storage/fact_codec.py.
    encode_fact_fields(d)

    # ADR-063 Phase 2 (c1): encode the `entity_id` carrier
    # (storage/entity_id_codec.py). Sets were already converted to sorted
    # lists by the single walk above -- the former second full-tree
    # `_sets_to_lists(...)` pass over this result is gone.
    converted: dict[str, Any] = encode_sidecar_entity_ids(
        encode_entity_ids(d, snap), snap
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
        # The shared header graph is written once, at the top level, by
        # encode_surface_graph(); encoding it here too only to pop it again
        # doubled the graph's encode cost on every save.
        shared = snap.surface_graph is not None and (
            snap.build_source.source_graph is snap.surface_graph
        )
        converted["build_source"] = snap.build_source.to_embedded_dict(
            include_source_graph=not shared
        )
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
    """The digest computation itself, with no memoization around it.

    Deliberately still a single ``dumps(...).encode()``. Feeding
    ``json.JSONEncoder.iterencode`` fragments into a running sha256 was
    measured here and rejected: it removes the whole-JSON ``str`` and its
    ``bytes`` copy, but both of those are allocated *after*
    ``to_sectioned_document`` has already peaked well above them, so the
    measured peak of this function did not move at all (576.1 MiB, both
    ways, for a 20k-function snapshot) while wall time grew ~20% from the
    per-fragment encode loop. The amplification that actually dominates
    this path is inside ``to_sectioned_document``/``ObjectStore.put`` --
    see ``docs/contribute/known-gaps.md``.
    """
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
