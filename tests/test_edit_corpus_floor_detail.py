"""The floor check's job-log detail: what a run has for each unverified kind.

``check_floor`` names a floor kind the run did not verify. Its detail lines say
which records could have verified it, and why an unavailable cell was skipped,
so the CI log says what happened without a second download. Each case states
its expected text directly.
"""

from __future__ import annotations

import json

from abiforge.coverage import CellRecord, check_floor, load_records


def _record(**kw: object) -> CellRecord:
    base: dict[str, object] = {
        "tier": "pr",
        "cell": "op-seed-config",
        "op": "op",
        "seed": "c_elf",
        "config": "c17-gcc13-O0-headers",
        "status": "ran",
        "cls": "agree",
    }
    base.update(kw)
    return CellRecord(**base)  # type: ignore[arg-type]


LEDGER = {
    "verified_floor": {"pr": ["MISMATCH_KIND", "UNAVAILABLE_KIND", "ABSENT_KIND"]}
}


def _problems(records: list[CellRecord]) -> dict[str, str]:
    out = {}
    for problem in check_floor(records, LEDGER, "pr"):
        kind = problem.split(":", 1)[0]
        out[kind] = problem
    return out


def test_mismatching_cell_is_listed_with_its_class_and_produced_kinds():
    records = [
        _record(
            op="rebuild_x",
            cls="wrong-kind",
            expected=("MISMATCH_KIND",),
            produced=("OTHER_KIND",),
            origin="edit-corpus-coverage-shard1",
        ),
    ]
    text = _problems(records)["MISMATCH_KIND"]
    assert "MISMATCH_KIND: in the pr verified floor, not verified by this run" in text
    assert (
        "pr rebuild_x seed=c_elf config=c17-gcc13-O0-headers status=ran cls=wrong-kind "
        "expected=MISMATCH_KIND produced=OTHER_KIND reason=- from=edit-corpus-coverage-shard1"
    ) in text
    assert "records came from: edit-corpus-coverage-shard1" in text


def test_unavailable_cell_shows_its_skip_reason():
    records = [
        _record(
            op="rebuild_m32",
            status="unavailable",
            cls="",
            expected=("UNAVAILABLE_KIND",),
            reason="gcc13 cannot build with -O0 -m32: ld returned 1 exit status",
            origin="edit-corpus-coverage-shard2",
        ),
    ]
    text = _problems(records)["UNAVAILABLE_KIND"]
    assert "status=unavailable cls=- " in text
    assert "reason=gcc13 cannot build with -O0 -m32: ld returned 1 exit status" in text


def test_kind_with_no_cell_says_so_and_lists_the_shards():
    records = [
        _record(origin="edit-corpus-coverage-shard1"),
        _record(origin="edit-corpus-coverage-shard2", expected=("OTHER_KIND",)),
    ]
    text = _problems(records)["ABSENT_KIND"]
    assert (
        "no cell for this kind ran; records came from: "
        "edit-corpus-coverage-shard1, edit-corpus-coverage-shard2"
    ) in text


def test_verified_kind_is_not_reported():
    records = [
        _record(
            expected=("MISMATCH_KIND",),
            produced=("MISMATCH_KIND",),
            origin="edit-corpus-coverage-shard1",
        ),
    ]
    assert "MISMATCH_KIND" not in _problems(records)


def test_load_records_tags_each_record_with_its_artifact_directory(tmp_path):
    root = tmp_path / "edit-corpus-coverage"
    for shard in ("edit-corpus-coverage-shard1", "edit-corpus-coverage-shard2"):
        d = root / shard
        d.mkdir(parents=True)
        data = _record(cell=shard).to_json()
        (d / f"{shard}.json").write_text(json.dumps(data), encoding="utf-8")
    loaded = load_records([root])
    assert sorted(r.origin for r in loaded) == [
        "edit-corpus-coverage-shard1",
        "edit-corpus-coverage-shard2",
    ]
    # The origin is not written to disk: a record round-trips without it.
    assert "origin" not in loaded[0].to_json()


def test_load_records_from_a_single_file_uses_its_name(tmp_path):
    path = tmp_path / "pr-abc.json"
    path.write_text(json.dumps(_record().to_json()), encoding="utf-8")
    (loaded,) = load_records([path])
    assert loaded.origin == "pr-abc.json"
