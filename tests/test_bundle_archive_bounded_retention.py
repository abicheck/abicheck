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

"""``write_bundle_facts_archive`` retains blob *metadata*, not blob bytes.

The writer used to accumulate every distinct encoded payload in an in-memory
``unique_payloads`` dict and only start writing archive members once the whole
bundle had been encoded, so resident memory grew with the bundle's member
count before a single byte had been written. It now spools payloads to a
temporary file as they are produced and keeps only ``{hash: (offset, length)}``.

The invariants below are what that change must not break. They are written
against the mechanism (what is resident, what order members land in, what
happens on failure) rather than against one fixture's expected byte count.
"""

from __future__ import annotations

import hashlib
import zipfile

import pytest

from abicheck.errors import SnapshotError
from abicheck.model import AbiSnapshot, Function, Param
from abicheck.model.bundle_facts import BundleFacts
from abicheck.serialization import snapshot_from_dict
from abicheck.storage.bundle_facts_archive import (
    read_bundle_facts_archive,
    write_bundle_facts_archive,
)
from abicheck.storage.snapshot_encode import snapshot_to_dict


def _snap(library: str, n: int = 40, salt: int = 0) -> AbiSnapshot:
    return AbiSnapshot(
        library=library,
        version="1",
        functions=[
            Function(
                name=f"fn{i}_{salt}",
                mangled=f"_Zfn{i}_{salt}",
                return_type="int",
                params=[Param(name="a", type="int")],
            )
            for i in range(n)
        ],
    )


def _write(facts: BundleFacts, path):
    return write_bundle_facts_archive(facts, path, snapshot_to_dict=snapshot_to_dict)


def _read(path):
    return read_bundle_facts_archive(path, snapshot_from_dict=snapshot_from_dict)


def _facts(**kw) -> BundleFacts:
    kw.setdefault("variant_fingerprint", "fp")
    return BundleFacts(**kw)


# --------------------------------------------------------------------------- #
# Output equivalence: spooling must change nothing an archive reader can see.
# --------------------------------------------------------------------------- #


def test_distinct_members_round_trip(tmp_path) -> None:
    per = {f"lib{i}.so": _snap(f"lib{i}.so", salt=i) for i in range(5)}
    _write(_facts(per_library_snapshots=per), tmp_path / "b.zip")
    back = _read(tmp_path / "b.zip")
    assert set(back.per_library_snapshots) == set(per)
    for name, snap in per.items():
        assert [f.name for f in back.per_library_snapshots[name].functions] == [
            f.name for f in snap.functions
        ]


def test_duplicate_blobs_collapse_to_one_member(tmp_path) -> None:
    """Two names sharing one object, and two names with equal content."""
    shared = _snap("shared.so", salt=1)
    equal_content = _snap("shared.so", salt=1)  # distinct object, same bytes
    per = {
        "a.so": shared,
        "b.so": shared,
        "c.so": equal_content,
        "d.so": _snap("d.so", salt=2),
    }
    _write(_facts(per_library_snapshots=per), tmp_path / "b.zip")
    with zipfile.ZipFile(tmp_path / "b.zip") as zf:
        blob_members = [n for n in zf.namelist() if n != "manifest.json"]
    # a/b/c all share one content hash; d is the only other blob.
    assert len(blob_members) == 2
    back = _read(tmp_path / "b.zip")
    assert set(back.per_library_snapshots) == set(per)


def test_member_order_is_a_function_of_content_not_insertion_order(tmp_path) -> None:
    """Determinism: the same content in a different name order writes the
    same archive, byte for byte.

    This is the invariant that made spooling necessary in the first place --
    members are emitted sorted by content hash, which the writer can only do
    after every payload exists. Holding them on disk rather than in memory is
    what preserves it.
    """
    snaps = {f"lib{i}.so": _snap(f"lib{i}.so", salt=i) for i in range(4)}
    forward = _facts(per_library_snapshots=dict(sorted(snaps.items())))
    reverse = _facts(per_library_snapshots=dict(sorted(snaps.items(), reverse=True)))
    _write(forward, tmp_path / "f.zip")
    _write(reverse, tmp_path / "r.zip")
    f_bytes = (tmp_path / "f.zip").read_bytes()
    r_bytes = (tmp_path / "r.zip").read_bytes()
    assert hashlib.sha256(f_bytes).hexdigest() == hashlib.sha256(r_bytes).hexdigest()


def test_writing_is_reproducible(tmp_path) -> None:
    per = {f"lib{i}.so": _snap(f"lib{i}.so", salt=i) for i in range(3)}
    a = _write(_facts(per_library_snapshots=per), tmp_path / "a.zip")
    b = _write(_facts(per_library_snapshots=per), tmp_path / "b.zip")
    assert a.stored_sha256 == b.stored_sha256
    assert a.decoded_size_bytes == b.decoded_size_bytes


# --------------------------------------------------------------------------- #
# Retention: what stays resident must not grow with the bundle.
# --------------------------------------------------------------------------- #


def test_writer_retains_blob_metadata_not_blob_bytes(tmp_path) -> None:
    """Nothing resident in the writer's own frame holds a payload's bytes.

    A white-box check, deliberately: payload liveness cannot be observed from
    outside, because ``bytes`` objects are neither GC-tracked nor
    weak-referenceable, and two probes built on those (a ``gc.get_objects()``
    sweep, a ``weakref`` handle) were each tried first and are each incapable
    of failing -- the sweep passed unchanged against a writer deliberately
    retaining every payload. So this inspects the writer's frame at the
    moment it starts emitting members, when the old implementation held its
    whole ``unique_payloads`` map, and asserts that what it carries per blob
    is a pair of integers.
    """
    import inspect

    import abicheck.storage.bundle_archive as archive

    real_put = archive.BundleArchiveWriter.put_blob
    observed: list[dict] = []

    def spy(self, payload):
        caller = inspect.currentframe().f_back
        observed.append(dict(caller.f_locals))
        return real_put(self, payload)

    archive.BundleArchiveWriter.put_blob = spy
    try:
        per = {f"lib{i}.so": _snap(f"lib{i}.so", salt=i) for i in range(5)}
        _write(_facts(per_library_snapshots=per), tmp_path / "b.zip")
    finally:
        archive.BundleArchiveWriter.put_blob = real_put

    assert observed, "put_blob was never reached -- the test proves nothing"
    frame = observed[0]
    retained = frame.get("unique_blobs")
    assert retained is not None, (
        "the writer no longer keeps a per-blob map under the name this test "
        "inspects; update the probe rather than deleting the invariant"
    )
    assert len(retained) == 5
    for key, value in retained.items():
        assert isinstance(key, str)
        assert isinstance(value, tuple) and len(value) == 2, (
            f"blob {key} is retained as {type(value).__name__}, not "
            "(offset, length) metadata"
        )
        assert all(isinstance(part, int) for part in value)
    for name, value in frame.items():
        assert not isinstance(value, bytes) or len(value) < 256, (
            f"the writer frame still holds a large payload under {name!r}"
        )


def test_write_peak_does_not_grow_with_member_count(tmp_path) -> None:
    """The behavioural claim behind the spooling change.

    Relative, not absolute: no fixed MiB threshold is asserted (that would be
    machine-dependent and brittle). The invariant is that quadrupling the
    number of distinct members, at fixed per-member size, does not add
    anything like the payload bytes that adds -- which is exactly what the
    old whole-bundle retention did.
    """
    import gc
    import tracemalloc

    def peak_for(members: int, path):
        per = {f"lib{i}.so": _snap(f"lib{i}.so", n=400, salt=i) for i in range(members)}
        facts = _facts(per_library_snapshots=per)
        gc.collect()
        tracemalloc.start()
        tracemalloc.reset_peak()
        result = _write(facts, path)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return peak, result.decoded_size_bytes

    small_peak, small_bytes = peak_for(2, tmp_path / "s.zip")
    large_peak, large_bytes = peak_for(8, tmp_path / "l.zip")

    added_payload = large_bytes - small_bytes
    assert added_payload > 0, "the two bundles must differ in content size"
    added_peak = large_peak - small_peak
    assert added_peak < added_payload * 0.5, (
        f"peak grew by {added_peak} bytes for {added_payload} bytes of extra "
        "payload -- payload retention is scaling with the bundle again"
    )


def test_shared_snapshot_object_is_encoded_once(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    def counting_to_dict(snap):
        calls.append(snap.library)
        return snapshot_to_dict(snap)

    shared = _snap("shared.so", salt=7)
    per = {"a.so": shared, "b.so": shared, "c.so": shared}
    write_bundle_facts_archive(
        _facts(per_library_snapshots=per),
        tmp_path / "b.zip",
        snapshot_to_dict=counting_to_dict,
    )
    assert calls == ["shared.so"], "a shared object must serialize exactly once"


# --------------------------------------------------------------------------- #
# Failure handling: nothing is published, nothing is left behind.
# --------------------------------------------------------------------------- #


def test_budget_exhaustion_publishes_nothing(tmp_path, monkeypatch) -> None:
    import abicheck.storage.bundle_facts_archive as mod

    monkeypatch.setattr(mod, "DEFAULT_MAX_BUNDLE_DECODED_BYTES", 1024)
    per = {f"lib{i}.so": _snap(f"lib{i}.so", n=200, salt=i) for i in range(4)}
    target = tmp_path / "b.zip"
    with pytest.raises(SnapshotError):
        _write(_facts(per_library_snapshots=per), target)
    assert not target.exists(), "a refused write must not publish an archive"
    assert list(tmp_path.iterdir()) == [], "a refused write must leave no temp file"


def test_member_count_cap_publishes_nothing(tmp_path, monkeypatch) -> None:
    import abicheck.storage.bundle_archive as archive

    monkeypatch.setattr(archive, "MAX_ARCHIVE_MEMBERS", 2)
    per = {f"lib{i}.so": _snap(f"lib{i}.so", salt=i) for i in range(4)}
    target = tmp_path / "b.zip"
    with pytest.raises(SnapshotError):
        _write(_facts(per_library_snapshots=per), target)
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_malformed_archive_is_refused(tmp_path) -> None:
    target = tmp_path / "b.zip"
    target.write_bytes(b"not a zip at all")
    with pytest.raises(SnapshotError):
        _read(target)


# --------------------------------------------------------------------------- #
# Member isolation, which spooling must not weaken.
# --------------------------------------------------------------------------- #


def test_duplicate_blob_members_stay_independently_mutable(tmp_path) -> None:
    shared = _snap("shared.so", salt=1)
    per = {"a.so": shared, "b.so": shared}
    _write(_facts(per_library_snapshots=per), tmp_path / "b.zip")
    back = _read(tmp_path / "b.zip")
    a, b = back.per_library_snapshots["a.so"], back.per_library_snapshots["b.so"]
    assert a is not b
    a.functions.clear()
    assert b.functions, "mutating one member must not affect the other"
