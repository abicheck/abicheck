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

"""RecordType.is_final_fact serialization round-trip (schema v30, ADR-063
Phase 5's first fact registry conversion — see
docs/contribute/plans/one-semantic-pipeline.md's Phase 5 section).

Split into its own file rather than appended to
tests/test_serialization_roundtrip.py (the ``TestFactFieldRoundTrip`` class
there covers Phase 0's own five fields): that file already sat at the
architecture gate's 1200-line test-file cap, and CLAUDE.md's own guidance
is "move responsibility instead of raising the baseline" — a fresh test
file, not a bigger one.

Needs no reliability flag, unlike Phase 0's ``vtable``/``is_va_list``:
``is_final``'s own ``None`` already unambiguously means "not captured" —
there is no separate "confirmed no evidence" state distinct from the field
being unset — so the legacy backfill is a direct None-vs-real-value
bridge, not a flag-gated correction.
"""

from __future__ import annotations

import dataclasses
import json

from abicheck.model import AbiSnapshot, Fact, FactStatus, RecordType
from abicheck.model.fact import replace_with_fact_sync
from abicheck.serialization import (
    SCHEMA_VERSION,
    snapshot_from_dict,
    snapshot_to_dict,
)


def _make_snap(**kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = {
        "library": "libfoo.so",
        "version": "v1",
        "functions": [],
        "variables": [],
        "types": [],
        "enums": [],
        "typedefs": [],
    }
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)  # type: ignore[arg-type]


def _round_trip(snap: AbiSnapshot) -> AbiSnapshot:
    return snapshot_from_dict(json.loads(json.dumps(snapshot_to_dict(snap))))


def _minimal_dict(**overrides: object) -> dict:
    base: dict = {
        "library": "libtest.so",
        "version": "v1",
        "functions": [],
        "variables": [],
        "types": [],
        "enums": [],
        "typedefs": [],
    }
    base.update(overrides)
    return base


class TestIsFinalFactRoundTrip:
    def test_fresh_snapshot_round_trips_true(self) -> None:
        rec = RecordType(name="Widget", kind="class", is_final=True)
        r = _round_trip(_make_snap(types=[rec])).types[0]
        assert r.is_final is True
        assert r.is_final_fact.status is FactStatus.PRESENT
        assert r.is_final_fact.value is True

    def test_fresh_snapshot_confirmed_false_survives_as_present_not_not_collected(
        self,
    ) -> None:
        rec = RecordType(name="Widget", kind="class", is_final=False)
        r = _round_trip(_make_snap(types=[rec])).types[0]
        assert r.is_final is False
        assert r.is_final_fact.status is FactStatus.PRESENT
        assert r.is_final_fact.value is False

    def test_omitted_is_final_round_trips_not_collected(self) -> None:
        rec = RecordType(name="Widget", kind="class")
        r = _round_trip(_make_snap(types=[rec])).types[0]
        assert r.is_final is None
        assert r.is_final_fact.status is FactStatus.NOT_COLLECTED

    def test_explicit_unsupported_fact_survives_round_trip(self) -> None:
        rec = RecordType(
            name="Gapped", kind="struct", is_final_fact=Fact.unsupported("DWARF-only")
        )
        r = _round_trip(_make_snap(types=[rec])).types[0]
        assert r.is_final_fact.status is FactStatus.UNSUPPORTED
        assert r.is_final_fact.diagnostics == ("DWARF-only",)
        assert r.is_final is None

    def test_legacy_pre_v30_snapshot_with_true_value_backfills_present(self) -> None:
        d = _minimal_dict(
            schema_version=29,
            types=[{"name": "Foo", "kind": "class", "is_final": True}],
        )
        r = snapshot_from_dict(d).types[0]
        assert r.is_final is True
        assert r.is_final_fact.status is FactStatus.PRESENT
        assert r.is_final_fact.value is True

    def test_legacy_pre_v30_snapshot_with_none_value_backfills_not_collected(
        self,
    ) -> None:
        d = _minimal_dict(
            schema_version=29,
            types=[{"name": "Foo", "kind": "class"}],
        )
        r = snapshot_from_dict(d).types[0]
        assert r.is_final is None
        assert r.is_final_fact.status is FactStatus.NOT_COLLECTED

    def test_current_schema_missing_fact_key_is_not_collected_not_present(self) -> None:
        # A v30+ document already commits to serializing is_final_fact
        # whenever RecordType emits one — a missing key on a document at or
        # above that threshold means malformed/hand-authored, not legacy.
        d = _minimal_dict(
            schema_version=SCHEMA_VERSION,
            types=[{"name": "Foo", "kind": "class", "is_final": True}],
        )
        snap = snapshot_from_dict(d)
        assert snap.types[0].is_final_fact.status is FactStatus.NOT_COLLECTED
        assert snap.types[0].is_final is None

    def test_snapshot_to_dict_encodes_status_as_plain_string(self) -> None:
        rec = RecordType(name="Foo", kind="class", is_final_fact=Fact.present(True))
        d = snapshot_to_dict(_make_snap(types=[rec]))
        assert d["types"][0]["is_final_fact"]["status"] == "present"

    def test_schema_version_is_30_or_higher(self) -> None:
        assert SCHEMA_VERSION >= 30


class TestIsFinalReplaceBridge:
    """``is_final`` shares ``bridge_legacy_and_fact`` with Phase 0's own
    four fields, so it inherits that bridge's own already-documented,
    already-accepted ``dataclasses.replace()`` limitation (Codex review:
    ``replace(record, is_final=True)`` carries the old, already-resolved
    ``is_final_fact`` forward unchanged, which ``__post_init__`` cannot
    tell apart from a deliberate fresh pair — see
    ``bridge_legacy_and_fact``'s own docstring, and
    ``TestReplaceIsUnsafeForFactBridgedFields``/``TestReplaceWithFactSync``
    in ``test_model_fact.py`` for the identical pair of tests already
    covering ``bases``). Not a new gap this conversion introduces — the
    existing, generic ``replace_with_fact_sync()`` escape hatch already
    covers ``is_final`` automatically (it resolves via ``hasattr(obj,
    f"{name}_fact")``, not a hardcoded field list), which these tests
    verify directly rather than by inference."""

    def test_raw_replace_updating_only_is_final_is_silently_discarded(self) -> None:
        rec = RecordType(name="Widget", kind="class", is_final=False)
        rec2 = dataclasses.replace(rec, is_final=True)
        assert rec2.is_final is False
        assert rec2.is_final_fact.value is False

    def test_replace_with_fact_sync_updates_both_representations(self) -> None:
        rec = RecordType(name="Widget", kind="class", is_final=False)
        rec2 = replace_with_fact_sync(rec, is_final=True)
        assert rec2.is_final is True
        assert rec2.is_final_fact is not None
        assert rec2.is_final_fact.status is FactStatus.PRESENT
        assert rec2.is_final_fact.value is True

    def test_replace_with_fact_sync_honors_an_explicit_fact(self) -> None:
        rec = RecordType(name="Widget", kind="class", is_final=False)
        rec2 = replace_with_fact_sync(
            rec, is_final=True, is_final_fact=Fact.unsupported("DWARF-only")
        )
        assert rec2.is_final_fact is not None
        assert rec2.is_final_fact.status is FactStatus.UNSUPPORTED

    def test_post_construction_attribute_mutation_does_not_survive_round_trip(
        self,
    ) -> None:
        """A second, related trap (Codex review, fresh finding): plain
        attribute assignment (`rec.is_final = True`) never re-runs
        `__post_init__`, so `is_final_fact` is never re-derived and the
        pair goes out of sync -- confirmed to reproduce identically on
        `bases` (a Phase 0 field) in
        `test_model_fact.py::TestPostConstructionMutationIsUnsafeForFactBridgedFields`,
        so this is not a gap `is_final`'s own conversion introduces. On the
        next encode-then-decode round trip, the stale `is_final_fact` wins
        over the mutated `is_final`, silently reverting it -- the exact
        failure `bridge_legacy_and_fact`'s docstring now names explicitly."""
        rec = RecordType(name="Widget", kind="class", is_final=False)
        rec.is_final = True
        assert rec.is_final_fact is not None
        assert rec.is_final_fact.value is False  # already stale before any I/O

        r = _round_trip(_make_snap(types=[rec])).types[0]
        assert r.is_final is False  # the mutation to True did not survive
