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

"""`RUNTIME_ONLY_FIELDS` vs. what the codec actually drops.

The constant exists so a consumer outside `storage` can ask "is this the
same *persisted* content" without mirroring `snapshot_to_dict`'s own pop
list (`policy.analysis_assurance_schema_staleness._same_content` may not
import `storage` at all). A second copy of a list is a second thing to
drift, so this pins the two against each other by *executing* the codec
rather than by reading its source: every field the constant names must be
absent from a real encode, and every field it does not name must be
present. Derived from the real dataclass field set, so a new snapshot
field is covered without editing this file.
"""

from __future__ import annotations

from dataclasses import fields

from abicheck.model.snapshot import AbiSnapshot
from abicheck.model.snapshot_persistence import (
    RUNTIME_ONLY_FIELDS,
    persisted_from_headers,
)
from abicheck.storage.snapshot_encode import snapshot_to_dict


def _encoded_keys() -> set[str]:
    return set(snapshot_to_dict(AbiSnapshot(version="1.0", library="libfoo.so.1")))


def test_every_runtime_only_field_is_absent_from_a_real_encode() -> None:
    encoded = _encoded_keys()
    for name in RUNTIME_ONLY_FIELDS:
        assert name in {f.name for f in fields(AbiSnapshot)}, (
            f"{name} is not an AbiSnapshot field at all"
        )
        assert name not in encoded, f"{name} IS persisted; drop it from the constant"


def test_listed_fields_are_exactly_the_digest_irrelevant_ones() -> None:
    """The property `_same_content` actually rests on, checked against the
    canonical digest rather than against the codec's source: perturbing a
    listed field must NOT change the digest (so comparing it would make
    assurance disagree with the digest), and the complement -- perturbing
    a persisted field DOES change it -- is covered by
    `tests/test_analysis_assurance_content_identity.py`.

    Populating the caches via `index()` is the real-world trigger: the
    reported failure was two content-identical snapshots reading
    `degraded` purely because one side had been indexed and the other had
    not."""
    from abicheck.storage.snapshot_encode import snapshot_content_digest

    plain = AbiSnapshot(version="1.0", library="libfoo.so.1")
    baseline = snapshot_content_digest(plain)

    indexed = AbiSnapshot(version="1.0", library="libfoo.so.1")
    indexed.index()
    assert any(
        getattr(indexed, name) is not None
        for name in ("_func_by_mangled", "_var_by_mangled", "_type_by_name")
    ), "index() populated no cache; this check would be vacuous"
    assert snapshot_content_digest(indexed) == baseline

    # `from_headers_inferred` is the one listed field that is NOT
    # digest-irrelevant: it makes the codec drop `from_headers` entirely,
    # so it changes the digest whatever `from_headers` itself reads. That
    # is exactly why `_same_content` compares `persisted_from_headers`
    # separately rather than relying on the field-skip alone, and why
    # `from_headers` is skipped by the field walk too.
    inferred = AbiSnapshot(version="1.0", library="libfoo.so.1")
    inferred.from_headers_inferred = True
    assert snapshot_content_digest(inferred) != baseline

    explicit_headers = AbiSnapshot(
        version="1.0", library="libfoo.so.1", from_headers=True
    )
    inferred_headers = AbiSnapshot(
        version="1.0", library="libfoo.so.1", from_headers=True
    )
    inferred_headers.from_headers_inferred = True
    assert snapshot_content_digest(explicit_headers) != snapshot_content_digest(
        inferred_headers
    )


def test_two_differently_spelled_inferred_snapshots_persist_identically() -> None:
    """The subtle half of the conditional drop, and why `from_headers` is
    skipped by the field walk: once `from_headers_inferred` is set the key
    is dropped whatever it held, so an inferred True and an inferred False
    ARE the same persisted content. `_same_content` must agree with the
    digest here too — in the direction that costs, since calling them
    different would report `degraded` for a pair that serializes
    identically."""
    from abicheck.policy.analysis_assurance_schema_staleness import _same_content
    from abicheck.storage.snapshot_encode import snapshot_content_digest

    a = AbiSnapshot(version="1.0", library="libfoo.so.1", from_headers=True)
    b = AbiSnapshot(version="1.0", library="libfoo.so.1", from_headers=False)
    a.from_headers_inferred = b.from_headers_inferred = True

    assert snapshot_content_digest(a) == snapshot_content_digest(b)
    assert _same_content(a, b)


def test_same_content_ignores_index_state() -> None:
    """The consumer half, end to end through the function that had the
    bug: indexing one side must not change the answer."""
    from abicheck.policy.analysis_assurance_schema_staleness import _same_content

    a = AbiSnapshot(version="1.0", library="libfoo.so.1")
    b = AbiSnapshot(version="1.0", library="libfoo.so.1")
    a.index()
    assert _same_content(a, b)
    assert _same_content(b, a)


def test_persisted_from_headers_models_the_conditional_drop() -> None:
    """`snapshot_to_dict` drops `from_headers` entirely when it was merely
    inferred, so an inferred True and an explicit True are different
    persisted content despite both reading True in memory."""
    explicit = AbiSnapshot(version="1.0", library="libfoo.so.1", from_headers=True)
    inferred = AbiSnapshot(version="1.0", library="libfoo.so.1", from_headers=True)
    inferred.from_headers_inferred = True

    assert persisted_from_headers(explicit) is True
    assert persisted_from_headers(inferred) is None
    assert "from_headers" in snapshot_to_dict(explicit)
    assert "from_headers" not in snapshot_to_dict(inferred)


def test_same_content_tracks_the_conditional_from_headers_drop() -> None:
    """The consumer half of the conditional drop: an inferred `True` and
    an explicit `True` read the same in memory but persist differently, so
    `_same_content` must call them different content — the same answer the
    digest gives."""
    from abicheck.policy.analysis_assurance_schema_staleness import _same_content
    from abicheck.storage.snapshot_encode import snapshot_content_digest

    explicit = AbiSnapshot(version="1.0", library="libfoo.so.1", from_headers=True)
    inferred = AbiSnapshot(version="1.0", library="libfoo.so.1", from_headers=True)
    inferred.from_headers_inferred = True

    assert not _same_content(explicit, inferred)
    assert not _same_content(inferred, explicit)
    assert snapshot_content_digest(explicit) != snapshot_content_digest(inferred)


def test_no_unlisted_scalar_field_is_digest_irrelevant() -> None:
    """The drift this constant could otherwise suffer, made mechanical
    rather than argued (Codex review, PR #1229): the risk is not that a
    listed field stops being dropped — the checks above catch that — but
    that the codec starts dropping a NEW field and nobody adds it here, so
    `_same_content` keeps comparing something the digest ignores and
    reports `degraded` for a pair that persists identically.

    So rather than trusting the list, this sweeps every `AbiSnapshot`
    field it can give a scalar value to — including the many that default
    to `None`, which is where a new provenance qualifier would start life
    — and asserts that setting it DOES change `snapshot_content_digest`
    unless the field is already listed. A newly-dropped field fails here
    on the commit that drops it, without anyone remembering this file
    exists.

    Verified to bite rather than pass vacuously: adding a `d.pop(...)` for
    an unlisted field to `snapshot_to_dict` fails this test."""
    from dataclasses import fields

    from abicheck.storage.snapshot_encode import snapshot_content_digest

    #: Tried in order; the first that both assigns and encodes is used, so
    #: a `str | None` field is exercised as a string and a `bool` as a
    #: flipped bool.
    _CANDIDATES: tuple[object, ...] = ("perturbed-value", 1, True)

    swept: list[str] = []
    digest_irrelevant: list[str] = []
    for f in fields(AbiSnapshot):
        snap = AbiSnapshot(version="1.0", library="libfoo.so.1")
        try:
            baseline = snapshot_content_digest(snap)
        except Exception:  # pragma: no cover - a default snapshot must encode
            raise
        current = getattr(snap, f.name)
        candidates = (not current,) if isinstance(current, bool) else _CANDIDATES
        for candidate in candidates:
            if candidate == current:
                continue
            setattr(snap, f.name, candidate)
            try:
                perturbed_digest = snapshot_content_digest(snap)
            except Exception:
                setattr(snap, f.name, current)
                continue
            swept.append(f.name)
            if perturbed_digest == baseline:
                digest_irrelevant.append(f.name)
            break

    assert len(swept) > 25, f"sweep covered too little to be meaningful: {swept}"
    unlisted = set(digest_irrelevant) - RUNTIME_ONLY_FIELDS
    assert unlisted == set(), (
        "these fields do not reach persisted content but are not in "
        "RUNTIME_ONLY_FIELDS, so _same_content compares what the digest "
        f"ignores: {sorted(unlisted)}"
    )


def test_nested_pack_root_is_not_persisted_content() -> None:
    """Codex review (PR #1229): the same rule applies to the nested objects
    a snapshot embeds. `BuildSourcePack.root` is where the pack was
    *loaded from*, and `to_embedded_dict` embeds only the normalized facts
    (ADR-028 D4) — so two snapshots whose packs came from different
    directories are the same persisted content, and comparing `root` made
    assurance turn on a load path.

    Checked against the digest in both directions, so the exclusion cannot
    become "pack differences never count"."""
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.policy.analysis_assurance_schema_staleness import _same_content
    from abicheck.storage.snapshot_encode import snapshot_content_digest

    a = AbiSnapshot(version="1.0", library="libfoo.so.1")
    b = AbiSnapshot(version="1.0", library="libfoo.so.1")
    a.build_source = BuildSourcePack.empty(root="/one/checkout/pack")
    b.build_source = BuildSourcePack.empty(root="/another/checkout/pack")

    assert a.build_source.root != b.build_source.root
    assert snapshot_content_digest(a) == snapshot_content_digest(b)
    assert _same_content(a, b)

    # The must-stay-distinct half: a difference in the pack's own persisted
    # content is still a difference.
    b.build_source.manifest.abicheck_version = "9.9.9-different"
    assert snapshot_content_digest(a) != snapshot_content_digest(b)
    assert not _same_content(a, b)


def test_unpersisted_fields_are_looked_up_per_type() -> None:
    """An unrecognized nested object is compared in full — the safe
    direction, since an over-strict compare reports `degraded` for a pair
    that persists identically, while an over-lax one claims two distinct
    captures are the same."""
    from abicheck.model.snapshot_persistence import unpersisted_fields_for

    assert unpersisted_fields_for(AbiSnapshot(version="1.0", library="l")) == (
        RUNTIME_ONLY_FIELDS
    )
    assert unpersisted_fields_for(object()) == frozenset()
    assert unpersisted_fields_for("a string") == frozenset()


def test_a_derived_graph_id_is_compared_as_persisted() -> None:
    """Codex review (PR #1229): `SourceGraphSummary.to_dict` serializes
    `graph_id or compute_graph_id()`, so an unset in-memory id and the
    computed one are the SAME persisted content — the walk compared the
    raw field and called them different.

    Normalized rather than excluded, because a *stale* stored id is
    genuinely different persisted content; both directions are asserted
    against the digest, since excluding the field would silently pass the
    first and fail the second."""
    import dataclasses

    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_graph import SourceGraphSummary
    from abicheck.policy.analysis_assurance_schema_staleness import _same_content
    from abicheck.storage.snapshot_encode import snapshot_content_digest

    finalized = SourceGraphSummary().finalize()
    assert finalized.graph_id, "finalize() must set an id or this is vacuous"
    unset = dataclasses.replace(finalized, graph_id="")
    stale = dataclasses.replace(finalized, graph_id="sha256:stale-and-wrong")

    def _snap(graph: SourceGraphSummary) -> AbiSnapshot:
        snap = AbiSnapshot(version="1.0", library="libfoo.so.1")
        pack = BuildSourcePack.empty(root="/p")
        pack.source_graph = graph
        snap.build_source = pack
        return snap

    a, b, c = _snap(finalized), _snap(unset), _snap(stale)
    assert snapshot_content_digest(a) == snapshot_content_digest(b)
    assert _same_content(a, b)
    assert snapshot_content_digest(a) != snapshot_content_digest(c)
    assert not _same_content(a, c)


def test_graph_aliasing_is_itself_persisted_content() -> None:
    """Codex review (PR #1229): when `surface_graph` and
    `build_source.source_graph` hold the IDENTICAL object,
    `storage.surface_graph_codec` writes the graph once at the top level
    and drops the nested copy — so an aliased snapshot and a
    structurally-equal unaliased one are different persisted content.

    Field-by-field equality cannot see that difference (reporting two
    distinct-but-equal objects as equal is its whole job), which made this
    the one case failing in the *unsafe* direction: `_same_content` said
    `True` for two snapshots whose digests differ, so neither the
    staleness status nor the digest-driven byte-identical warning would
    have said anything. All three alias combinations are checked against
    the digest."""
    import dataclasses

    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_graph import SourceGraphSummary
    from abicheck.policy.analysis_assurance_schema_staleness import _same_content
    from abicheck.storage.snapshot_encode import snapshot_content_digest

    shared = SourceGraphSummary().finalize()

    def _snap(*, aliased: bool) -> AbiSnapshot:
        snap = AbiSnapshot(version="1.0", library="libfoo.so.1")
        pack = BuildSourcePack.empty(root="/p")
        if aliased:
            snap.surface_graph = shared
            pack.source_graph = shared
        else:
            snap.surface_graph = dataclasses.replace(shared)
            pack.source_graph = dataclasses.replace(shared)
        snap.build_source = pack
        return snap

    aliased, unaliased = _snap(aliased=True), _snap(aliased=False)
    assert aliased.surface_graph is aliased.build_source.source_graph
    assert unaliased.surface_graph is not unaliased.build_source.source_graph
    assert unaliased.surface_graph == unaliased.build_source.source_graph, (
        "the two graphs must be structurally EQUAL or this proves nothing"
    )

    assert snapshot_content_digest(aliased) != snapshot_content_digest(unaliased)
    assert not _same_content(aliased, unaliased)
    assert not _same_content(unaliased, aliased)

    # Matching shapes still agree, so the check cannot degrade into
    # "anything with a graph is always different".
    assert _same_content(_snap(aliased=True), _snap(aliased=True))
    assert _same_content(_snap(aliased=False), _snap(aliased=False))
