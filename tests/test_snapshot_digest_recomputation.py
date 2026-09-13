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

"""``snapshot_content_digest`` is computed once per snapshot, not once per
consumer.

Bug class ``perf.pure_content_digest_recomputed_per_consumer``: a pure,
expensive, content-derived function reached independently by several
consumers within one run, each recomputing what an earlier one already
produced. Measured on a real oneAPI corpus, a single graph-shaped
``compare`` serialized two snapshots six times -- 404s of a 620s run --
because ``workflows.gate.snapshot_identity_digest`` and
``storage.snapshot_encode.same_persisted_content`` (which serializes both
sides) are reached three times between ``checker.compare``'s own assurance
attach, the front end's re-attach with the real evidence pack, and the
same-binary-compared digest fallback.

The invariant is stated over *actual serializations counted during a real
comparison*, bounded by the number of distinct snapshots rather than by
the number of consumers. That is deliberately not "consumer X now uses the
cache": such a test forecloses exactly one call site, and the defect here
was that the fourth caller was added without anyone noticing the third.
The bound holds for a fifth caller added tomorrow, and the non-vacuity
assertion below fails if a future refactor makes the digest stop being
consulted at all.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

import abicheck.storage.snapshot_encode as snapshot_encode
from abicheck.cli import main

_FIXTURES = Path(__file__).parent / "fixtures" / "schema"


class _DigestSpy:
    """Counts both halves of the question separately.

    ``serializations`` is the expensive work actually performed;
    ``consultations`` is how many times some consumer asked for a digest.
    A test that only counted the former could pass by never asking.
    """

    def __init__(self) -> None:
        self.serializations: list[int] = []
        self.consultations: list[int] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_compute = snapshot_encode._uncached_snapshot_content_digest
        real_digest = snapshot_encode.snapshot_content_digest

        def compute(snap: Any) -> str:
            self.serializations.append(id(snap))
            return real_compute(snap)

        def digest(snap: Any) -> str:
            self.consultations.append(id(snap))
            return real_digest(snap)

        monkeypatch.setattr(
            snapshot_encode, "_uncached_snapshot_content_digest", compute
        )
        monkeypatch.setattr(snapshot_encode, "snapshot_content_digest", digest)

    @property
    def distinct_snapshots(self) -> int:
        return len(set(self.serializations) | set(self.consultations))


def _snapshot_pair(tmp_path: Path, fixture: str, *, differ: bool) -> tuple[Path, Path]:
    """Two on-disk snapshots -- the same content, or genuinely different.

    Both are written as separate files so the comparison loads two distinct
    objects, which is the shape a real ``compare`` has (and the one where
    an object-identity short-circuit would prove nothing).
    """
    base = json.loads((_FIXTURES / fixture).read_text())
    old_path = tmp_path / "old.abi.json"
    new_path = tmp_path / "new.abi.json"
    old_path.write_text(json.dumps(base))
    other = json.loads((_FIXTURES / fixture).read_text())
    if differ:
        if other.get("functions"):
            fn = other["functions"][0]
            fn["return_type"] = f"{fn.get('return_type', 'int')} const"
        else:
            other["version"] = f"{other.get('version', '1')}-next"
    new_path.write_text(json.dumps(other))
    return old_path, new_path


def _run_cli_compare(old: Path, new: Path) -> int:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = CliRunner().invoke(main, ["compare", str(old), str(new)])
    assert result.exit_code in (0, 2, 4), (result.exit_code, result.output)
    return result.exit_code


def _run_typed_api_compare(old: Path, new: Path) -> None:
    from abicheck.service import CompareRequest, InputSpec, run_compare_request

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        run_compare_request(
            CompareRequest(old=InputSpec(path=old), new=InputSpec(path=new))
        )


_FRONT_ENDS = {
    "cli": _run_cli_compare,
    "typed_api": _run_typed_api_compare,
}


@pytest.mark.parametrize("front_end", sorted(_FRONT_ENDS))
@pytest.mark.parametrize("fixture", ["v4.json", "v5.json"])
@pytest.mark.parametrize("differ", [False, True])
def test_digest_serializations_are_bounded_by_snapshot_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    front_end: str,
    fixture: str,
    differ: bool,
) -> None:
    """The class invariant, over every front end and both pairings.

    ``differ`` matters: an identical pair is the case a cheap
    short-circuit could answer without serializing at all, so a test using
    only that shape would pass with no memoization anywhere. The differing
    pair is the one that must actually serialize -- and must serialize
    each side exactly once.
    """
    old, new = _snapshot_pair(tmp_path, fixture, differ=differ)
    spy = _DigestSpy()
    spy.install(monkeypatch)

    _FRONT_ENDS[front_end](old, new)

    assert spy.consultations, (
        "no consumer asked for a content digest at all -- the invariant "
        "below would hold vacuously"
    )
    assert len(spy.serializations) <= spy.distinct_snapshots, (
        f"{len(spy.serializations)} serializations for "
        f"{spy.distinct_snapshots} distinct snapshots: a consumer is "
        "recomputing a digest an earlier one already produced"
    )
    assert len(set(spy.serializations)) == len(spy.serializations), (
        "the same snapshot was serialized twice"
    )


@pytest.mark.parametrize("front_end", sorted(_FRONT_ENDS))
def test_consumers_outnumber_serializations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, front_end: str
) -> None:
    """The load-bearing negative for the test above.

    ``serializations <= distinct_snapshots`` would also hold in a build
    where the whole digest mechanism had been deleted. This asserts the
    saving is real: strictly more consultations happen than serializations,
    i.e. the memo is answering somebody.
    """
    old, new = _snapshot_pair(tmp_path, "v5.json", differ=True)
    spy = _DigestSpy()
    spy.install(monkeypatch)

    _FRONT_ENDS[front_end](old, new)

    assert len(spy.consultations) > len(spy.serializations), (
        f"{len(spy.consultations)} consultations vs "
        f"{len(spy.serializations)} serializations -- nothing was reused"
    )


def test_digest_string_is_unchanged_by_memoization(tmp_path: Path) -> None:
    """The correctness constraint the memo must not touch.

    The digest appears in report output and in cache keys, so it stays a
    plain sha256 over the same canonical JSON -- byte-identical whether it
    came from the memo, from an open scope's first computation, or from a
    call made with no scope open at all.
    """
    import hashlib

    from abicheck.serialization import digest_scope, snapshot_from_dict
    from abicheck.storage.snapshot_encode import (
        snapshot_content_digest,
        snapshot_to_json,
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        snap = snapshot_from_dict(json.loads((_FIXTURES / "v5.json").read_text()))

    expected = hashlib.sha256(snapshot_to_json(snap).encode()).hexdigest()
    assert snapshot_content_digest(snap) == expected
    with digest_scope():
        assert snapshot_content_digest(snap) == expected
        assert snapshot_content_digest(snap) == expected
    assert snapshot_content_digest(snap) == expected


def test_same_persisted_content_short_circuits_on_object_identity() -> None:
    """The `old is new` fast path, asserted rather than argued.

    One object trivially has the same persisted content as itself, and the
    serializer is a pure function of the snapshot, so the digest comparison
    this replaces could only ever have returned `True`. The load-bearing
    half is the second assertion: it must answer `True` *without*
    serializing anything, or it is not a short-circuit at all -- which is
    the whole reason it is here, since a self-compare is exactly the shape
    that pays the digest cost twice for no information.
    """
    from abicheck.serialization import snapshot_from_dict
    from abicheck.storage.snapshot_encode import same_persisted_content

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        snap = snapshot_from_dict(json.loads((_FIXTURES / "v5.json").read_text()))

    calls: list[int] = []
    original = snapshot_encode._uncached_snapshot_content_digest

    def counting(s: Any) -> str:
        calls.append(id(s))
        return original(s)

    snapshot_encode._uncached_snapshot_content_digest = counting
    try:
        assert same_persisted_content(snap, snap) is True
    finally:
        snapshot_encode._uncached_snapshot_content_digest = original

    assert calls == [], "the identity short-circuit serialized something"


def test_memo_does_not_survive_its_own_scope(tmp_path: Path) -> None:
    """A scope is the memo's whole lifetime.

    ``AbiSnapshot`` is mutable and the resolve half of a comparison
    genuinely mutates it, so a memo outliving the block it was entered for
    would answer from before a mutation. Stated as an executable
    invariant rather than left to the module docstring: mutate a snapshot
    between two scopes and the second scope must see the new content.
    """
    from abicheck.serialization import digest_scope, snapshot_from_dict
    from abicheck.storage.snapshot_encode import snapshot_content_digest

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        snap = snapshot_from_dict(json.loads((_FIXTURES / "v5.json").read_text()))

    with digest_scope():
        before = snapshot_content_digest(snap)
    snap.library = f"{snap.library}-mutated"
    with digest_scope():
        after = snapshot_content_digest(snap)
    assert before != after


def test_nested_scopes_share_one_memo(tmp_path: Path) -> None:
    """Re-entrancy: an inner scope must not shadow the outer one.

    A front end that wraps a whole comparison and a helper that wraps its
    own phase both open a scope; if the inner one started a fresh memo,
    the digests the outer one already paid for would be recomputed inside
    it -- exactly the defect, reintroduced by nesting.
    """
    from abicheck.serialization import digest_scope, snapshot_from_dict

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        snap = snapshot_from_dict(json.loads((_FIXTURES / "v5.json").read_text()))

    calls: list[int] = []
    real = snapshot_encode._uncached_snapshot_content_digest

    def counting(s: Any) -> str:
        calls.append(id(s))
        return real(s)

    original = snapshot_encode._uncached_snapshot_content_digest
    snapshot_encode._uncached_snapshot_content_digest = counting
    try:
        with digest_scope():
            snapshot_encode.snapshot_content_digest(snap)
            with digest_scope():
                snapshot_encode.snapshot_content_digest(snap)
            snapshot_encode.snapshot_content_digest(snap)
    finally:
        snapshot_encode._uncached_snapshot_content_digest = original

    assert len(calls) == 1, calls
