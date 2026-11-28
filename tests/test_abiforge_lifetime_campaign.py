import csv
import shutil
import sys

import pytest

from abiforge.run.lifetime_campaign import run


@pytest.mark.skipif(
    sys.platform != "linux" or not shutil.which("g++") or not shutil.which("clang++"),
    reason="Lifetime campaign requires Linux, g++ and clang++",
)
def test_retained_value_campaign(tmp_path, monkeypatch):
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
    run(tmp_path)
    expected = {
        "old": (17, 1),
        "compatible": (17, 1),
        "extra_owner": (17, 0),
        "early_release": (-1, 1),
        "self_assignment_release": (17, 2),
        "missing_release": (17, 0),
        "double_release": (17, 2),
    }
    with (tmp_path / "results.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert {(r["case"], r["lane"]) for r in rows} == {
        (case, lane) for case in expected for lane in ("elf", "headers")
    }
    for row in rows:
        assert int(row["releases_after_assignment"]) == int(
            row["case"] == "self_assignment_release"
        )
        assert (int(row["surviving_value"]), int(row["deleter_calls"])) == expected[
            row["case"]
        ]
