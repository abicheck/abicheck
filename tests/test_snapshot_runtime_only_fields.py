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
