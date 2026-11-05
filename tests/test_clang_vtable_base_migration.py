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

"""Generation-40 migration of an older clang vtable over bases (bug class
``extraction.clang_ast_shape_version_drift``, its cross-generation half).

Before generation 40 the clang backend could leave a base unresolved and
still store the record's vtable as ``present``: ``class D : public
detail::I`` under clang 22+ was stored with an empty, confirmed vtable. A
fresh generation-40 dump resolves the base, so comparing the two reported
``VPTR_INTRODUCED`` and ``TYPE_VTABLE_CHANGED`` -- BREAKING, exit 4 -- for a
class that did not change. The oracle is the source: the class is the same
on both sides, so the comparison must report no vtable finding, while a
class that really gains its first virtual function must still be reported.
"""

from __future__ import annotations

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.model import AbiSnapshot, Function, Param, RecordType, Visibility
from abicheck.model.fact import Fact, FactStatus
from abicheck.serialization import (
    load_snapshot,
    save_snapshot,
    snapshot_from_dict,
    snapshot_to_dict,
)
from abicheck.storage.clang_vtable_base_migration import migrate_clang_vtable_bases
from abicheck.storage.generation_migrations import FACT_GENERATIONS_KEY

_VTABLE_KINDS = {
    ChangeKind.VPTR_INTRODUCED,
    ChangeKind.TYPE_VTABLE_CHANGED,
    ChangeKind.VIRTUAL_METHOD_ADDED,
}
_D_VTABLE = ["_ZN3lib1DD1Ev", "_ZN3lib1D3runEv"]


def _snapshot(vtable: list[str], producer: str | None, bases: list[str]) -> AbiSnapshot:
    record = RecordType(
        name="D",
        kind="class",
        qualified_name="lib::D",
        size_bits=None,
        alignment_bits=None,
        bases=list(bases),
        vtable=list(vtable),
        vtable_fact=Fact.present(list(vtable)),
        vptr_offset_bits=0 if vtable else None,
        vptr_offset_bits_fact=Fact.partial(0 if vtable else None),
    )
    return AbiSnapshot(
        library="libx.so.1",
        version="1",
        functions=[
            Function(
                name="take",
                mangled="_Z4takePN3lib1DE",
                return_type="void",
                params=[Param(name="d", type="lib::D *")],
                visibility=Visibility.PUBLIC,
            )
        ],
        types=[record],
        from_headers=producer is not None,
        ast_producer=producer,
    )


def _stored(
    vtable: list[str], producer: str | None, extractor: int, bases: list[str]
) -> AbiSnapshot:
    doc = snapshot_to_dict(_snapshot(vtable, producer, bases))
    doc[FACT_GENERATIONS_KEY] = {"extractor": extractor, "resolver": 3, "source": 0}
    return snapshot_from_dict(doc)


def _fresh(vtable: list[str], producer: str | None, bases: list[str]) -> AbiSnapshot:
    return snapshot_from_dict(snapshot_to_dict(_snapshot(vtable, producer, bases)))


def _vtable_fact(snap: AbiSnapshot) -> Fact:
    return next(t for t in snap.declarations.types if t.name == "D").vtable_fact


@pytest.mark.parametrize("producer", ["clang", "hybrid"])
@pytest.mark.parametrize("extractor", [21, 37, 38, 39])
@pytest.mark.parametrize(
    "old_vtable",
    [
        pytest.param([], id="base-unresolved-empty"),
        pytest.param(["_ZN3lib1D3runEv"], id="base-unresolved-short"),
    ],
)
def test_old_clang_vtable_over_bases_never_reports_a_vtable_break(
    old_vtable: list[str], extractor: int, producer: str
) -> None:
    baseline = _stored(old_vtable, producer, extractor, ["detail::I"])
    assert _vtable_fact(baseline).status is FactStatus.PARTIAL
    assert any("clang vtable fact" in n for n in baseline.load_notices)
    changes = compare(baseline, _fresh(_D_VTABLE, producer, ["detail::I"])).changes
    assert not {c.kind for c in changes} & _VTABLE_KINDS


def test_unmigrated_baseline_would_report_the_false_break() -> None:
    """Control on the oracle: the same pair at generation 40 (no migration)
    does report the vtable findings the migration exists to stop."""
    baseline = _stored([], "clang", 40, ["detail::I"])
    assert _vtable_fact(baseline).status is FactStatus.PRESENT
    kinds = {
        c.kind
        for c in compare(baseline, _fresh(_D_VTABLE, "clang", ["detail::I"])).changes
    }
    assert ChangeKind.VPTR_INTRODUCED in kinds


def test_record_without_bases_keeps_a_confirmed_vtable_and_a_real_vptr_break() -> None:
    """A record with no base never depended on base resolution: its stored
    vtable stays confirmed, so a class that really gains its first virtual
    function is still reported across generations."""
    baseline = _stored([], "clang", 39, [])
    assert _vtable_fact(baseline).status is FactStatus.PRESENT
    assert not any("clang vtable fact" in n for n in baseline.load_notices)
    kinds = {c.kind for c in compare(baseline, _fresh(_D_VTABLE, "clang", [])).changes}
    assert ChangeKind.VPTR_INTRODUCED in kinds


@pytest.mark.parametrize(
    ("producer", "extractor"),
    [
        pytest.param("clang", 40, id="clang-current"),
        pytest.param("castxml", 37, id="castxml-never-lowered"),
        pytest.param(None, 37, id="dwarf-or-symbols"),
    ],
)
def test_vtable_kept_confirmed_outside_old_clang(
    producer: str | None, extractor: int
) -> None:
    stored = _stored(_D_VTABLE, producer, extractor, ["detail::I"])
    assert _vtable_fact(stored).status is FactStatus.PRESENT
    assert not any("clang vtable fact" in n for n in stored.load_notices)


@pytest.mark.parametrize("resaves", [1, 2, 3])
def test_resaved_migrated_baseline_stays_partial(resaves: int, tmp_path) -> None:
    snap = _stored([], "clang", 39, ["detail::I"])
    for index in range(resaves):
        path = tmp_path / f"resave{index}.json"
        save_snapshot(snap, path)
        snap = load_snapshot(path)
    assert snap.fact_generations is not None and snap.fact_generations[0] == 39
    assert _vtable_fact(snap).status is FactStatus.PARTIAL


def test_malformed_entries_pass_through_unchanged() -> None:
    document = {
        "ast_producer": "clang",
        "types": [
            "junk",
            {"name": "R", "bases": ["A"]},
            {"name": "S", "bases": ["A"], "vtable_fact": None},
            {"name": "T", "bases": [], "vtable_fact": {"status": "present"}},
            {"name": "U", "virtual_bases": ["V"], "vtable_fact": {"status": "present"}},
        ],
    }
    migrated, notices = migrate_clang_vtable_bases(document, 39)
    assert migrated["types"][:4] == document["types"][:4]
    assert migrated["types"][4]["vtable_fact"]["status"] == "partial"
    assert document["types"][4]["vtable_fact"]["status"] == "present"  # not mutated
    assert len(notices) == 1
    no_types = {"ast_producer": "clang", "types": None}
    assert migrate_clang_vtable_bases(no_types, 39) == (no_types, [])
