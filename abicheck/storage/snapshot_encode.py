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
import operator
import pathlib
from dataclasses import fields as dataclass_fields, is_dataclass
from typing import Any

from ..model import AbiSnapshot
from ..model.declaration_store import STORE_ATTRIBUTE
from ..model.snapshot_reliability import FACT_FAMILIES, flag_name
from .acyclic_json import gc_paused
from .entity_id_codec import encode_entity_ids, encode_sidecar_entity_ids
from .enum_codec import encode_platform_enums
from .extraction_scope_codec import encode_extraction_scope
from .fact_codec import encode_fact_fields
from .generation_migrations import FACT_GENERATIONS_KEY, encode_fact_generations
from .json_stream import OMIT, LazyItems
from .semantic_ir_codec import encode_semantic_ir
from .snapshot_record_compaction import compact_records
from .snapshot_runtime_only import RUNTIME_ONLY_SNAPSHOT_FIELDS
from .snapshot_schema_versions import SCHEMA_VERSION
from .snapshot_section_stream import sectioned_document_stream
from .surface_graph_codec import drop_shared_source_graph, encode_surface_graph
from .versioning import this_build_generations
from .wire_extensions import (
    WireExtensions,
    extensions_of,
    refuse_lossy_flattening,
)
from .wire_field_names import wire_field_names

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


#: ``type -> its dataclass field names``, or ``None`` for a non-dataclass.
#: ``dataclasses.fields()``/``is_dataclass()`` answer the same thing for
#: every instance of a class, and calling them per value was the largest
#: single cost of encoding a snapshot (one pair per model object). A class
#: object is a dataclass instance of nothing, so ``type(obj)`` of a class
#: (its metaclass) maps to ``None`` -- matching the former
#: ``not isinstance(obj, type)`` guard.
_FIELD_NAMES: dict[type, tuple[str, ...] | None] = {}


def _dataclass_field_names(cls: type) -> tuple[str, ...] | None:
    try:
        return _FIELD_NAMES[cls]
    except KeyError:
        names = (
            # ADR-063 Phase 10: a class with retired bridge fields persists
            # them from their read-only views, in their historical position.
            wire_field_names(cls) if is_dataclass(cls) else None
        )
        _FIELD_NAMES[cls] = names
        return names


#: Checked inline at each recursive call site, so the common leaf value costs
#: a set lookup rather than a function call; the same rule the top of
#: :func:`_encode_value` applies.
_LEAF = _IMMUTABLE_LEAF_TYPES


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
    cls = type(obj)
    if cls in _IMMUTABLE_LEAF_TYPES:
        return obj
    names = _dataclass_field_names(cls)
    if names is not None:
        return {
            name: v if type(v := getattr(obj, name)) in _LEAF else _encode_value(v)
            for name in names
        }
    # ``Enum`` stays an ``isinstance`` test: a member's type is its own enum
    # class, never ``Enum`` itself, so exact matching cannot express it -- and
    # sharing one is safe regardless of what the enum subclasses, because
    # members are singletons that ``deepcopy`` returns unchanged anyway.
    if isinstance(obj, enum.Enum):
        return obj
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if isinstance(obj, dict):
        return {k: v if type(v) in _LEAF else _encode_value(v) for k, v in obj.items()}
    if isinstance(obj, tuple):
        if hasattr(obj, "_fields"):  # namedtuple, as asdict special-cases it
            return type(obj)(*[_encode_value(v) for v in obj])
        return tuple(v if type(v) in _LEAF else _encode_value(v) for v in obj)
    if isinstance(obj, list):
        return [v if type(v) in _LEAF else _encode_value(v) for v in obj]
    return copy.deepcopy(obj)


def _encode_value_sorted(obj: Any) -> Any:
    """:func:`_encode_value` in the shape the storage layer canonicalizes to:
    a dataclass's fields in sorted key order and a plain tuple as a list.

    Same values as :func:`_encode_value` and the same detachment; only key
    order and the sequence type differ, and both are exactly what
    `canonical.canonical_form` rewrites. Used by :func:`snapshot_to_json`,
    whose document is canonicalized next: emitting the canonical shape here
    lets that step reuse the tree (`canonical.canonical_form_shared`) where
    it used to rebuild nearly every dict of it. Plain ``dict`` values keep
    their own key order -- the canonicalizer sorts the few that need it.

    Dispatches on the exact type through :data:`_SORTED_ENCODERS`, a
    per-class encoder chosen once by :func:`_sorted_encoder_for`: every
    branch of the former ``isinstance`` chain depended on the class alone,
    so the choice is the same for every instance and need not be re-made
    per value (it was ~6M times on an MKL snapshot).
    """
    return _sorted_encoder(type(obj))(obj)


def _identity(obj: Any) -> Any:
    return obj


def _sorted_list(obj: Any) -> Any:
    return sorted(obj)


def _sorted_sequence(obj: Any) -> list[Any]:
    # A sequence of immutable leaves (most of them: names, ids, offsets) is
    # copied by one C-level ``list()`` once a C-level type scan proves it.
    if _LEAF.issuperset(map(type, obj)):
        return list(obj)
    get = _SORTED_ENCODERS.get
    return [
        v if type(v) in _LEAF else (get(type(v)) or _sorted_encoder(type(v)))(v)
        for v in obj
    ]


def _sorted_mapping(obj: Any) -> dict[Any, Any]:
    get = _SORTED_ENCODERS.get
    return {
        k: v if type(v) in _LEAF else (get(type(v)) or _sorted_encoder(type(v)))(v)
        for k, v in obj.items()
    }


def _sorted_namedtuple(obj: Any) -> Any:
    return type(obj)(*[_encode_value_sorted(v) for v in obj])


def _sorted_dataclass_encoder(names: tuple[str, ...]) -> Any:
    get = _SORTED_ENCODERS.get
    if len(names) == 1:
        (only,) = names

        def encode_one(obj: Any) -> dict[str, Any]:
            v = getattr(obj, only)
            return {
                only: v
                if type(v) in _LEAF
                else (get(type(v)) or _sorted_encoder(type(v)))(v)
            }

        return encode_one
    if not names:
        return lambda obj: {}
    getter = operator.attrgetter(*names)

    def encode(obj: Any) -> dict[str, Any]:
        return {
            name: v
            if type(v) in _LEAF
            else (get(type(v)) or _sorted_encoder(type(v)))(v)
            for name, v in zip(names, getter(obj))
        }

    return encode


def _sorted_encoder_for(cls: type) -> Any:
    """The encoder :func:`_encode_value_sorted` applies to every *cls*
    instance -- the former per-value ``isinstance`` chain, decided once."""
    if cls in _IMMUTABLE_LEAF_TYPES:
        return _identity
    names = _dataclass_field_names(cls)
    if names is not None:
        return _sorted_dataclass_encoder(tuple(sorted(names)))
    if issubclass(cls, enum.Enum):
        return _identity
    if issubclass(cls, (set, frozenset)):
        return _sorted_list
    if issubclass(cls, dict):
        return _sorted_mapping
    if issubclass(cls, tuple):
        return _sorted_namedtuple if hasattr(cls, "_fields") else _sorted_sequence
    if issubclass(cls, list):
        return _sorted_sequence
    return copy.deepcopy


#: ``type -> the encoder for its instances``: pure per-class facts, like
#: :data:`_FIELD_NAMES`, filled on first sight of each class.
_SORTED_ENCODERS: dict[type, Any] = {}


def _sorted_encoder(cls: type) -> Any:
    encoder = _SORTED_ENCODERS.get(cls)
    if encoder is None:
        encoder = _SORTED_ENCODERS[cls] = _sorted_encoder_for(cls)
    return encoder


def _encode_dataclass_skipping(
    obj: Any,
    skip: frozenset[str],
    encode: Any = _encode_value,
    only: frozenset[str] | None = None,
) -> dict[str, Any]:
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
        f.name: encode(getattr(obj, f.name))
        for f in dataclass_fields(obj)
        if f.name not in skip and (only is None or f.name in only)
    }


#: ``AbiSnapshot`` fields the generic walk above must never descend into.
#: Each is a lazy lookup cache, a runtime-only field (see
#: :data:`RUNTIME_ONLY_SNAPSHOT_FIELDS`), or a field whose persisted form is
#: owned by a dedicated codec that reprojects it from the still-typed object
#: further down (never from this recursion's output).
_SNAPSHOT_CACHE_FIELDS = frozenset(
    {"_func_by_mangled", "_var_by_mangled", "_type_by_name"}
)
_SNAPSHOT_SKIP_FIELDS = frozenset(
    {
        *_SNAPSHOT_CACHE_FIELDS,
        *RUNTIME_ONLY_SNAPSHOT_FIELDS,
        # Owned by encode_surface_graph() below, which unconditionally
        # replaces the key with the graph codec's own to_dict()
        # (Codex review, PR #962).
        "surface_graph",
        # ADR-063 Phase 6 (v38): owned by encode_semantic_ir() below. Its
        # OccurrenceId-keyed mapping is also exactly the shape ``asdict``
        # could not walk at all.
        "semantic_ir",
        # Owned by BuildSourcePack.to_embedded_dict() below, which replaces
        # the walked value (or the key is dropped when nothing is embedded):
        # walking it first was most of a snapshot's encode time, discarded.
        "build_source",
    }
)


#: Where each declaration kind sat among ``AbiSnapshot``'s fields before the
#: kinds moved into ``semantic_ir.declarations`` (ADR-063 Phase 10): each is
#: written right after the named key, so the encoded document -- and every
#: digest over it -- is unchanged by the move.
_DECLARATION_KEY_ANCHORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("version", ("functions", "variables", "types")),
    ("macho", ("dwarf", "dwarf_advanced")),
    ("numpy_capi", ("enums", "typedefs", "constants")),
    (
        "dependency_scope",
        ("typedefs_qualified", "typedef_entity_ids", "constant_entity_ids"),
    ),
)


def _with_declarations(
    d: dict[str, Any],
    snap: AbiSnapshot,
    encode: Any = _encode_value,
    only: frozenset[str] | None = None,
) -> dict[str, Any]:
    decls = snap.declarations
    if only is not None:
        # A per-section encode: key order is canonicalized by the section
        # packaging, so the kinds asked for need no anchor.
        for _anchor, kinds in _DECLARATION_KEY_ANCHORS:
            for kind in kinds:
                if kind in only:
                    d[kind] = encode(getattr(decls, STORE_ATTRIBUTE[kind]))
        return d
    after = {anchor: kinds for anchor, kinds in _DECLARATION_KEY_ANCHORS}
    out: dict[str, Any] = {}
    for key, value in d.items():
        out[key] = value
        for kind in after.get(key, ()):
            out[kind] = encode(getattr(decls, STORE_ATTRIBUTE[kind]))
    return out


def _expand_stale_fact_families(d: dict[str, Any], snap: AbiSnapshot) -> dict[str, Any]:
    """Write ``stale_fact_families`` as the eight historical
    ``*_facts_reliable`` keys, in that field's position (ADR-063 Phase 10:
    the persisted document is unchanged by the model's retirement of them)."""
    out: dict[str, Any] = {}
    for key, value in d.items():
        if key != "stale_fact_families":
            out[key] = value
            continue
        for family in FACT_FAMILIES:
            out[flag_name(family)] = family not in snap.stale_fact_families
    return out


def snapshot_to_dict(snap: AbiSnapshot) -> dict[str, Any]:
    """Encode *snap* into its canonical, fully-detached dictionary form.

    Pure with respect to *snap*: nothing here reads, writes, or temporarily
    clears a field on the caller's object, so serializing a snapshot another
    thread is concurrently reading is safe.

    Raises `SnapshotError` when *snap* holds ADR-062 D14/D15 state the flat
    document cannot carry (`wire_extensions.refuse_lossy_flattening`): every
    flat document is then a complete statement of its snapshot, so a writer
    handed one never stores lost state as if it were whole. A writer of such
    a snapshot takes the `AbiSnapshot`; :func:`flat_document_and_carriers`
    is the storage layer's own split for one that stores the carriers beside
    the flat document.
    """
    refuse_lossy_flattening(snap)
    return _snapshot_to_dict(snap, _encode_value)


def flat_document_and_carriers(
    snap: AbiSnapshot,
) -> tuple[dict[str, Any], WireExtensions | None]:
    """*snap*'s flat document together with the D14/D15 carriers it cannot
    hold (``None`` when there are none), for a storage writer that keeps them
    beside it (a directory or bundle package's `ArtifactRef.carriers`) or
    hashes them with it (the `evidence` digest). The two are produced
    together so a caller never holds one without the other. A writer still
    calls `wire_extensions.refuse_unwritable_extensions` first."""
    return _snapshot_to_dict(snap, _encode_value), extensions_of(snap)


def _snapshot_to_dict(
    snap: AbiSnapshot, encode: Any, keys: frozenset[str] | None = None
) -> dict[str, Any]:
    """`snapshot_to_dict`, walking the model with *encode* --
    :func:`_encode_value`, or :func:`_encode_value_sorted` for a document
    the write path canonicalizes next.

    *keys* (ADR-062 D12, plan A2.2): encode only these flat-document keys --
    one section's (`snapshot_section_stream`). Each post-pass below touches
    only the keys it reads and writes, so a restricted encode yields exactly
    the whole encode's values for *keys*; a pass none of whose outputs is
    wanted is skipped. ``tests/test_snapshot_section_stream.py`` proves the
    streamed bytes equal the whole-document encoder's."""

    def wanted(*names: str) -> bool:
        return keys is None or any(n in keys for n in names)

    only = None
    if keys is not None:
        only = keys | (
            {"stale_fact_families"} if keys & _STALE_FLAG_NAMES else frozenset()
        )
    d = _with_declarations(
        _encode_dataclass_skipping(snap, _SNAPSHOT_SKIP_FIELDS, encode, only),
        snap,
        encode,
        keys,
    )
    # Runtime-only provenance qualifier — never persisted.
    d.pop("from_headers_inferred", None)
    d = _expand_stale_fact_families(d, snap)
    # Runtime-only source-read licence — never persisted, by design. Writing it
    # would let a stored snapshot grant itself permission to re-read whatever
    # now lives at the ``source_header`` paths it records, which is exactly the
    # defect ``buildsource/source_inputs.py``'s contract forbids: a recorded
    # path is provenance, not a licence. A loaded snapshot therefore always
    # comes back with the field at its deny-by-default ``False``.
    for name in RUNTIME_ONLY_SNAPSHOT_FIELDS:
        d.pop(name, None)
    # The -E -dD branch scan (schema v67) is walked like any persisted field,
    # but a run that made no scan leaves it out, so a document without one
    # encodes exactly as it did before v67.
    if d.get("live_preprocessor_hidden") is None:
        d.pop("live_preprocessor_hidden", None)
    # Schema v69: the typedef target facts, written only when a producer captured
    # some; a status is written as its string, as encode_fact_fields does.
    typedef_facts = d.get("typedef_abi_facts")
    if not typedef_facts:
        d.pop("typedef_abi_facts", None)
    else:
        for fact in typedef_facts.values():
            fact["status"] = getattr(fact["status"], "value", fact["status"])
    # ADR-062 D2: the producing build's generations, never re-dated on re-save.
    d.pop("fact_generations", None)
    d.pop("spellings_generation", None)  # folded into the stamp below
    # A recorded spelling generation is kept whether or not a stamp was carried:
    # with no stamp it is written against this build's generations, so the
    # migrated-once marker is never lost on a re-save.
    d[FACT_GENERATIONS_KEY] = encode_fact_generations(
        snap.fact_generations
        if snap.fact_generations is not None
        else this_build_generations(carries_source_facts=snap.build_source is not None),
        snap.spellings_generation,
    )
    # The dump's own provenance block (model/snapshot.py): persisted verbatim
    # when the snapshot carries one, and absent otherwise, so a snapshot no
    # `dump` produced encodes exactly as it always did (no ``null`` key).
    d.pop("dump_provenance", None)
    if snap.dump_provenance is not None:
        d["dump_provenance"] = encode(snap.dump_provenance)
    # If ``from_headers`` was only *inferred* (a legacy snapshot loaded without
    # the explicit key), do not persist it as explicit provenance: drop the key
    # so a reload re-runs the same inference and re-marks it inferred, rather
    # than promoting a guess to explicit provenance and re-enabling source-level
    # param-rename detection on DWARF-only baselines this is meant to suppress.
    if snap.from_headers_inferred:
        d.pop("from_headers", None)

    # ElfMetadata/PeMetadata/MachoMetadata enums -> strings (storage/enum_codec.py).
    encode_platform_enums(d)
    compact_records(d)

    # ADR-063 Phase 0 (schema v26): see storage/fact_codec.py.
    encode_fact_fields(d)

    # ADR-063 Phase 2 (c1): encode the `entity_id` carrier
    # (storage/entity_id_codec.py). Sets were already converted to sorted
    # lists by the single walk above -- the former second full-tree
    # `_sets_to_lists(...)` pass over this result is gone.
    converted: dict[str, Any] = encode_entity_ids(d, snap)
    if wanted(*_SIDECAR_KEYS):
        encode_sidecar_entity_ids(converted, snap)

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
    if snap.build_source is not None and wanted("build_source"):
        # The shared header graph is written once, at the top level, by
        # encode_surface_graph(); encoding it here too only to pop it again
        # doubled the graph's encode cost on every save.
        shared = snap.surface_graph is not None and (
            snap.build_source.source_graph is snap.surface_graph
        )
        converted["build_source"] = snap.build_source.to_embedded_dict(
            include_source_graph=not shared
        )
    if wanted("surface_graph"):
        encode_surface_graph(converted, snap)  # storage/surface_graph_codec.py
    else:
        drop_shared_source_graph(converted, snap)
    if wanted("semantic_ir", "semantic_ir_conflicts"):
        encode_semantic_ir(converted, snap)  # storage/semantic_ir_codec.py (v38)
    if wanted(*_EXTRACTION_SCOPE_KEYS):
        encode_extraction_scope(converted, snap)  # extraction_scope_codec.py (v52)
    # v54: only the Fact is persisted (its value carries the identifiers; the
    # legacy field is rebuilt from it on load), and an evidence-free
    # not_collected one is omitted, so a snapshot without the index encodes
    # exactly as v53.
    converted.pop("public_header_identifiers", None)
    phi = snap.public_header_identifiers_fact
    if phi is None or (phi.status.value == "not_collected" and not phi.diagnostics):
        converted.pop("public_header_identifiers_fact", None)

    # Embed schema version for forward-compatibility.
    # Placed at top level so loaders can inspect it without parsing the full snapshot.
    converted["schema_version"] = SCHEMA_VERSION

    if keys is not None:
        return {k: v for k, v in converted.items() if k in keys}
    return converted


#: The ``*_facts_reliable`` keys ``stale_fact_families`` is written as.
_STALE_FLAG_NAMES = frozenset(flag_name(family) for family in FACT_FAMILIES)
_SIDECAR_KEYS = ("typedef_entity_ids", "constant_entity_ids")
#: Every key `encode_extraction_scope` reads or writes.
_EXTRACTION_SCOPE_KEYS = (
    "functions",
    "variables",
    "types",
    "enums",
    "extraction_scope",
    "ownership_without_scope",
)


def snapshot_to_json(snap: AbiSnapshot, indent: int = 2) -> str:
    # ADR-062/063 Phase 8 (redesign): the file this function's own callers
    # (`write_snapshot`/`save_snapshot`) actually write to disk is now the
    # single-file sectioned shape (`storage.sectioned_document`) by
    # default -- `snapshot_to_dict()` itself keeps returning the flat shape
    # unchanged for every other caller (tests, `canonical_form` comparisons,
    # programmatic manipulation); only the JSON-file boundary changes.
    # `snapshot_from_dict` transparently unwraps either shape, so an older
    # flat `.abi.json` a prior build wrote stays fully readable.
    # The encoded document is a fresh tree of millions of containers that is
    # serialized and dropped; the cyclic collector re-traversing it while it
    # is built frees nothing (`acyclic_json.gc_paused`). Lists from the
    # sorted encoder are always GC-tracked, unlike tuples of scalars, so
    # without the pause that encoder would pay for the canonical shape here.
    with gc_paused():
        return _sectioned_json(snap, indent)


def _sectioned_json(snap: AbiSnapshot, indent: int) -> str:
    # Section by section (storage plan A2.2): the encoded tree of one
    # section is alive at a time, never the whole document's.
    from .json_stream import join_json_indented

    return join_json_indented(streamed_sectioned_document(snap), indent=indent)


def streamed_sectioned_document(snap: AbiSnapshot) -> LazyItems:
    """The single-file sectioned document a snapshot write serializes, as a
    `json_stream.LazyItems` that encodes one section at a time (storage plan
    A2.2, `snapshot_section_stream`). Serialize it once, under
    `acyclic_json.gc_paused`."""
    return sectioned_document_stream(
        snap,
        lambda keys: _snapshot_to_dict(snap, _encode_value_sorted, keys),
        _SNAPSHOT_SKIP_FIELDS,
    )


def sectioned_document_for_write(snap: AbiSnapshot) -> dict[str, Any]:
    """`streamed_sectioned_document` materialized as a plain ``dict``: the
    same members, for a writer that embeds the document in another one (a
    bundle member, `storage.bundle_member_carriers`) rather than streaming
    it to a file. It holds every section at once."""
    document: dict[str, Any] = _materialize(streamed_sectioned_document(snap))
    return document


def _materialize(value: Any) -> Any:
    if isinstance(value, LazyItems):
        out: dict[str, Any] = {}
        for key in value.keys:
            produced = value.produce(key)
            if produced is not OMIT:
                out[key] = _materialize(produced)
        return out
    return value


def snapshot_content_digest(snap: AbiSnapshot) -> str:
    """A canonical sha256 of *snap*'s serialized content.

    Shared by every caller of ``confidence.note_if_same_binary_compared``'s
    ``old_snapshot_digest``/``new_snapshot_digest`` fallback (Item 4 fix) --
    the typed-API path (``workflows.compare.execute.classify_compare_pair``)
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
    if persisted_content_provably_differs(old, new):
        return False
    try:
        return snapshot_content_digest(old) == snapshot_content_digest(new)
    except Exception:
        return False


#: Scalar ``AbiSnapshot`` fields the serializer writes verbatim, as a JSON
#: string under their own key. Two snapshots holding different strings in
#: any of them therefore serialize differently, with no need to serialize
#: either. ``tests/test_snapshot_digest_prefilter.py`` checks each against
#: the real serializer rather than trusting this list.
_VERBATIM_STR_FIELDS = (
    "created_at",
    "source_path",
    "library",
    "version",
    "git_commit",
    "build_id",
)


def persisted_content_provably_differs(old: AbiSnapshot, new: AbiSnapshot) -> bool:
    """``True`` only when *old* and *new* certainly serialize differently.

    A cheap, one-directional check ahead of :func:`snapshot_content_digest`:
    the digest's only consumers ask whether two snapshots are the *same*
    content, and two snapshots from different runs differ in ``created_at``
    alone. Answering "different" here skips two whole-snapshot
    serializations, the most expensive step of a stored-snapshot compare.
    ``False`` means only "not decided" -- the digest still answers it.
    """
    for name in _VERBATIM_STR_FIELDS:
        a, b = getattr(old, name, None), getattr(new, name, None)
        if type(a) is str and type(b) is str and a != b:
            return True
    return False
