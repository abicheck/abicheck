"""The dropped-header record's storage edges (evidence-entity-model gap A3).

Every malformed, missing or unwritable record reads as "nothing recorded":
the record only ever narrows what a snapshot claims, so a damaged one must
never invent a dropped header or break the parse it describes."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from abicheck.model.header_parse_coverage import (
    HEADER_PARSE_EXCLUDED_METADATA,
    header_parse_excluded,
)
from abicheck.storage import ast_parse_exclusions as ape


def test_round_trip_through_the_sidecar(tmp_path):
    entry = tmp_path / "entry.json"
    root: dict = {}
    ape.record_parse_exclusions(["b.h", "a.h"], root, entry, cache_write=True)
    assert root[ape.HEADER_PARSE_EXCLUDED_KEY] == ["b.h", "a.h"]
    restored: dict = {}
    ape.attach_parse_exclusions(restored, entry)
    assert restored[ape.HEADER_PARSE_EXCLUDED_KEY] == ["a.h", "b.h"]


def test_a_clean_parse_removes_a_stale_record(tmp_path):
    entry = tmp_path / "entry.json"
    ape.record_parse_exclusions(["a.h"], None, entry, cache_write=True)
    ape.record_parse_exclusions([], None, entry, cache_write=True)
    restored: dict = {}
    ape.attach_parse_exclusions(restored, entry)
    assert restored == {}


def test_an_unwritable_sidecar_leaves_no_temp_and_does_not_raise(tmp_path, monkeypatch):
    entry = tmp_path / "entry.json"

    def failing_dump(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(ape.json, "dump", failing_dump)
    root: dict = {}
    ape.record_parse_exclusions(["a.h"], root, entry, cache_write=True)
    # The in-memory record still stands; nothing half-written is left behind.
    assert root[ape.HEADER_PARSE_EXCLUDED_KEY] == ["a.h"]
    assert list(tmp_path.iterdir()) == []


def test_a_non_oserror_during_write_still_cleans_up_and_propagates(
    tmp_path, monkeypatch
):
    entry = tmp_path / "entry.json"

    def failing_dump(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(ape.json, "dump", failing_dump)
    with pytest.raises(KeyboardInterrupt):
        ape.record_parse_exclusions(["a.h"], {}, entry, cache_write=True)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("content", ["{not json", "[]", '{"a": 1}', '"a.h"', "null"])
def test_a_damaged_sidecar_reads_as_nothing_recorded(tmp_path, content):
    entry = tmp_path / "entry.json"
    (tmp_path / "entry.json.excluded.json").write_text(content, encoding="utf-8")
    restored: dict = {}
    ape.attach_parse_exclusions(restored, entry)
    assert restored == {}


@pytest.mark.parametrize("raw", ["{not json", '{"a": 1}', '"a.h"', "", None])
def test_a_damaged_snapshot_record_reads_as_nothing_dropped(raw):
    snap = SimpleNamespace(ast_toolchain={HEADER_PARSE_EXCLUDED_METADATA: raw})
    assert header_parse_excluded(snap) == ()


def test_a_snapshot_record_is_read_back():
    snap = SimpleNamespace(
        ast_toolchain={HEADER_PARSE_EXCLUDED_METADATA: json.dumps(["x.h"])}
    )
    assert header_parse_excluded(snap) == ("x.h",)
