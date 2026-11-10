"""Persist the tier a request can read, while preserving cold/warm results."""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.workflows.dump.cache import api, store
from abicheck.workflows.dump.cache.finished import Finisher, offering


@pytest.mark.parametrize("finish", [False, True])
@pytest.mark.parametrize("write", [False, True])
def test_only_consumed_tier_is_written(
    tmp_path: Path, monkeypatch, finish: bool, write: bool
):
    binary = tmp_path / "lib.so"
    binary.write_bytes(b"fixture")
    cache = tmp_path / "cache"
    monkeypatch.setattr(store, "_CACHE_DIR", cache)
    monkeypatch.setattr(api, "_cache_key_for", lambda req: "stable-key")
    monkeypatch.setenv("ABICHECK_SNAPSHOT_CACHE_WRITE", str(int(write)))
    calls = []

    def dump(*args, **kwargs):
        calls.append("dump")
        return AbiSnapshot(
            library="lib.so",
            version="1",
            functions=[
                Function(
                    name="api",
                    mangled="api",
                    return_type="int",
                    visibility=Visibility.PUBLIC,
                )
            ],
        )

    def finishing(snap):
        calls.append("finish")
        snap.declarations.functions[0].return_type = "long"
        return snap

    for rep in range(2):
        finisher = Finisher("scope-A", finishing) if finish else None
        with offering(finisher):
            snap = api.cached_run_dump(dump, binary, "elf")
        assert snap.declarations.functions[0].return_type == (
            "long" if finish else "int"
        )
    assert calls == (["dump", "finish"] if finish else ["dump"]) * (1 if write else 2)
    raw = list(cache.glob("*.json.zst"))
    finished = list((cache / "finished").glob("*.json.zst"))
    assert len(raw) == (1 if write and not finish else 0)
    assert len(finished) == (1 if write and finish else 0)


def test_unstable_key_populates_neither_tier(tmp_path: Path, monkeypatch):
    binary = tmp_path / "lib.so"
    binary.write_bytes(b"fixture")
    cache = tmp_path / "cache"
    monkeypatch.setattr(store, "_CACHE_DIR", cache)
    keys = iter(["before", "after"])
    monkeypatch.setattr(api, "_cache_key_for", lambda req: next(keys))
    finisher = Finisher("scope-A", lambda snap: snap)
    with offering(finisher):
        result = api.cached_run_dump(
            lambda *a, **kw: AbiSnapshot(library="lib.so", version="1"), binary, "elf"
        )
    assert finisher.applied
    assert result.library == "lib.so"
    assert list(cache.rglob("*.json.zst")) == []


@pytest.mark.integration
def test_cli_cold_warm_and_no_write_reports_match(tmp_path, monkeypatch):
    import json
    import shutil
    import subprocess
    import sys

    from click.testing import CliRunner

    from abicheck.cli import main

    if (
        sys.platform != "linux"
        or not shutil.which("gcc")
        or not shutil.which("castxml")
    ):
        pytest.skip("requires Linux, gcc and castxml")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    header, source, binary = inputs / "api.h", inputs / "api.c", inputs / "lib.so"
    header.write_text("struct Value { int x; }; int api(struct Value*);\n")
    source.write_text('#include "api.h"\nint api(struct Value*p){return p->x;}\n')
    subprocess.run(
        ["gcc", "-g", "-shared", "-fPIC", str(source), "-o", str(binary)],
        check=True,
        capture_output=True,
    )
    cache = tmp_path / "snapshots"
    monkeypatch.setattr(store, "_CACHE_DIR", cache)
    monkeypatch.setenv("ABICHECK_SIDE_PROCESSES", "0")
    report = tmp_path / "report.json"
    args = [
        "compare",
        str(binary),
        str(binary),
        "-H",
        f"old={header}",
        "-H",
        f"new={header}",
        "--set",
        "compile.frontend=castxml",
        "-o",
        f"json={report}",
    ]

    def run():
        result = CliRunner().invoke(main, args)
        assert result.exit_code == 0, str(result.exception) + result.output
        doc = json.loads(report.read_text())
        doc.get("evidence_metrics", {}).pop("extractor.duration_seconds", None)
        return doc

    monkeypatch.setenv("ABICHECK_SNAPSHOT_CACHE_WRITE", "0")
    expected = run()
    assert list(cache.rglob("*.json.zst")) == []
    monkeypatch.setenv("ABICHECK_SNAPSHOT_CACHE_WRITE", "1")
    assert run() == expected
    assert list(cache.glob("*.json.zst")) == []
    assert list((cache / "finished").glob("*.json.zst"))

    from abicheck.workflows import input_resolution

    def forbidden(*args, **kwargs):
        raise AssertionError("warm finished hit re-extracted or re-finished")

    monkeypatch.setattr(input_resolution, "run_dump", forbidden)
    monkeypatch.setattr(input_resolution, "finish_snapshot", forbidden)
    assert run() == expected
