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

"""``abicheck.cu_stack_protector``: the per-unit census survives a save and
load, an older snapshot reads as "no unit read" (unknown), and a malformed
stored value is refused rather than read as that unknown."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from abicheck.errors import SnapshotError
from abicheck.model.snapshot import AbiSnapshot
from abicheck.serialization import load_snapshot, snapshot_from_dict
from abicheck.storage.extension_fact_catalog import CU_STACK_PROTECTOR
from abicheck.storage.extension_facts import apply_model_extension_facts
from abicheck.storage.snapshot_codec import save_snapshot
from abicheck.storage.wire_extensions import EXTENSIONS_KEY, WireExtensions, attach

_GENERATIONS = Path(__file__).resolve().parents[2] / "fixtures" / "generations"


@pytest.mark.parametrize(
    "census",
    [{"strong": 1}, {"all": 2, "none": 1, "unrecorded": 3}, {"explicit": 1}],
)
def test_census_round_trips(tmp_path: Path, census: dict[str, int]) -> None:
    snap = AbiSnapshot(library="l", version="1", cu_stack_protector=census)
    path = tmp_path / "s.json"
    save_snapshot(snap, path)
    assert load_snapshot(path).cu_stack_protector == census
    assert "cu_stack_protector" not in json.loads(path.read_text()).get("sections", {})


def test_a_reset_census_is_not_resaved(tmp_path: Path) -> None:
    snap = AbiSnapshot(library="l", version="1", cu_stack_protector={"strong": 1})
    first = tmp_path / "a.json"
    save_snapshot(snap, first)
    loaded = load_snapshot(first)
    loaded.cu_stack_protector = None
    second = tmp_path / "b.json"
    save_snapshot(loaded, second)
    assert load_snapshot(second).cu_stack_protector is None
    assert CU_STACK_PROTECTOR.name not in second.read_text(encoding="utf-8")


def test_every_frozen_older_generation_reads_as_unknown() -> None:
    corpora = sorted(_GENERATIONS.glob("gen*/corpus.json.gz"))
    assert corpora, "the frozen generation corpus is missing"
    for corpus in corpora:
        doc = json.loads(gzip.decompress(corpus.read_bytes()))
        docs = doc if isinstance(doc, list) else doc.get("snapshots", [doc])
        for entry in docs:
            if isinstance(entry, dict) and "snapshot" in entry:
                entry = entry["snapshot"]
            if not isinstance(entry, dict):
                continue
            assert snapshot_from_dict(entry).cu_stack_protector is None, corpus


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"strong": 0},
        {"strong": -1},
        {"strong": True},
        {"strong": "1"},
        {"medium": 1},
        ["strong"],
        "strong",
        None,
    ],
)
def test_a_malformed_stored_census_is_refused(bad: object) -> None:
    snap = AbiSnapshot(library="l", version="1")
    ext = WireExtensions()
    ext.header_extras[CU_STACK_PROTECTOR.section] = {
        EXTENSIONS_KEY: {CU_STACK_PROTECTOR.name: bad}
    }
    attach(snap, ext)
    with pytest.raises(SnapshotError, match="cu_stack_protector"):
        apply_model_extension_facts(snap)


def test_an_absent_census_reads_as_no_unit_read() -> None:
    snap = AbiSnapshot(library="l", version="1")
    apply_model_extension_facts(snap)
    assert snap.cu_stack_protector is None
