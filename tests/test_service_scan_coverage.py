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

"""Coverage-closing unit tests for :mod:`abicheck.service_scan`.

Targets the error/fallback branches and the MCP subprocess harness that the
existing ``test_scan_estimate.py`` happy-path tests don't reach: header-input
edge cases, the compile-DB / source-tree / pack TU counters' failure paths, and
the killable ``run_scan_subprocess`` worker (``_scan_subprocess_worker`` /
``_kill_process_tree``). Default lane — no compiler; the subprocess tests use a
serialized ``.abi.json`` snapshot so the spawned child never invokes castxml.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.errors import ValidationError
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    ScopeOrigin,
    Visibility,
)
from abicheck.serialization import snapshot_to_json
from abicheck.service_scan import (
    ScanRequest,
    _count_compile_db_tus,
    _count_pack_tus,
    _count_source_tus,
    _kill_process_tree,
    _layers_from_coverage,
    _scan_subprocess_worker,
    estimate_scan,
    expand_header_inputs,
    run_scan_subprocess,
)

# The _kill_process_tree group-termination logic is POSIX-only: it relies on
# os.getpgid/os.getpgrp/os.killpg and signal.SIGKILL, none of which exist on
# Windows (there the production code hits AttributeError and degrades to a plain
# terminate()). These tests assert the POSIX escalation path, so skip them off
# POSIX rather than forcing an unreachable branch on Windows.
_posix_process_groups = pytest.mark.skipif(
    not hasattr(os, "getpgid"),
    reason="POSIX process-group termination (os.getpgid/killpg, signal.SIGKILL) is POSIX-only",
)


@pytest.fixture
def snap_path(tmp_path: Path) -> Path:
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="void",
                visibility=Visibility.PUBLIC,
                access=AccessLevel.PUBLIC,
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
        ],
        elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3foov")]),
    )
    p = tmp_path / "new.abi.json"
    p.write_text(snapshot_to_json(snap), encoding="utf-8")
    return p


# ── expand_header_inputs: the "exists but not a file/dir" branch (line 82) ────


def test_expand_header_inputs_rejects_non_file_non_dir(tmp_path: Path) -> None:
    # A FIFO exists but is neither a regular file nor a directory, so it falls
    # through to the final guard rather than being accepted as a header.
    fifo = tmp_path / "pipe"
    try:
        os.mkfifo(fifo)
    except (AttributeError, NotImplementedError, OSError):
        pytest.skip("os.mkfifo unavailable on this platform")
    with pytest.raises(ValidationError, match="neither file nor directory"):
        expand_header_inputs([fifo])


# ── _count_compile_db_tus: the malformed-input branches (290/291/293/304) ─────


def test_count_compile_db_tus_invalid_json_returns_zero(tmp_path: Path) -> None:
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text("{ this is not valid json ", encoding="utf-8")
    assert _count_compile_db_tus(cdb) == 0


def test_count_compile_db_tus_missing_file_returns_zero(tmp_path: Path) -> None:
    # OSError read failure is folded into the same zero-return guard.
    assert _count_compile_db_tus(tmp_path / "does-not-exist.json") == 0


def test_count_compile_db_tus_non_list_returns_zero(tmp_path: Path) -> None:
    # A well-formed JSON object (not the expected array) is not a compile DB.
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(json.dumps({"file": "a.cpp"}), encoding="utf-8")
    assert _count_compile_db_tus(cdb) == 0


def test_count_compile_db_tus_skips_bad_entries(tmp_path: Path) -> None:
    # Non-dict entries and dicts without a truthy `file` key are skipped; only the
    # one valid entry counts.
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [
                "not-a-dict",
                {"directory": "/x"},  # no `file`
                {"file": ""},  # empty `file` is falsy
                {"file": "real.cpp", "directory": "/x"},
            ]
        ),
        encoding="utf-8",
    )
    assert _count_compile_db_tus(cdb) == 1


# ── _count_source_tus: file vs. directory counting (lines 339-345) ────────────


def test_count_source_tus_single_source_file(tmp_path: Path) -> None:
    src = tmp_path / "one.cpp"
    src.write_text("int one(){return 0;}\n", encoding="utf-8")
    assert _count_source_tus(src) == 1


def test_count_source_tus_single_non_source_file(tmp_path: Path) -> None:
    doc = tmp_path / "readme.txt"
    doc.write_text("hello\n", encoding="utf-8")
    assert _count_source_tus(doc) == 0


def test_count_source_tus_directory_recursion(tmp_path: Path) -> None:
    (tmp_path / "a.cpp").write_text("//\n", encoding="utf-8")
    (tmp_path / "b.c").write_text("//\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.mm").write_text("//\n", encoding="utf-8")
    (sub / "notes.md").write_text("//\n", encoding="utf-8")  # ignored
    assert _count_source_tus(tmp_path) == 3


def test_estimate_counts_source_tree_without_compile_db(
    snap_path: Path, tmp_path: Path
) -> None:
    # A --sources tree with no compile DB (and no pack/Bazel build info) falls
    # through to the counted-source-files provenance in _estimate_total_tus.
    tree = tmp_path / "src"
    tree.mkdir()
    (tree / "x.cpp").write_text("//\n", encoding="utf-8")
    (tree / "y.cpp").write_text("//\n", encoding="utf-8")
    est = estimate_scan(
        ScanRequest(binaries=[snap_path], sources=tree, mode="baseline")
    )
    l3 = next(e for e in est if e.layer == "L3_build")
    assert l3.tus == 2
    assert l3.note == "counted source files (no compile DB)"


# ── _count_pack_tus: the best-effort guard swallows a bad pack (403/404) ───────


def test_count_pack_tus_non_directory_returns_none(tmp_path: Path) -> None:
    f = tmp_path / "plain.txt"
    f.write_text("x\n", encoding="utf-8")
    assert _count_pack_tus(f) is None


def test_count_pack_tus_swallows_load_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A directory that is_pack_dir accepts but whose load blows up must not raise
    # mid-estimate: the guard returns None so the caller falls back to other
    # counters.
    pack = tmp_path / "pack"
    pack.mkdir()

    def _boom(_p: Path) -> bool:
        raise RuntimeError("corrupt manifest")

    monkeypatch.setattr("abicheck.buildsource.inline.is_pack_dir", _boom)
    assert _count_pack_tus(pack) is None


def test_count_pack_tus_not_a_pack_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plain = tmp_path / "buildtree"
    plain.mkdir()
    monkeypatch.setattr("abicheck.buildsource.inline.is_pack_dir", lambda _p: False)
    assert _count_pack_tus(plain) is None


# ── _layers_from_coverage: defensive int coercion (lines 700-710) ─────────────


def test_layers_from_coverage_coerces_and_skips_bad_values() -> None:
    # A forward-compat / hand-edited coverage row can carry non-numeric counters or
    # a non-numeric `facts`; the mapper coerces what it can and drops the rest
    # rather than aborting the whole render.
    rows = [
        {
            "method": "s5",
            "layer": "L4_source_abi",
            "status": "present",
            "facts": "not-a-number",  # → coerced to 0
            "detail": "d",
            "counters": {"matched_symbols": 3, "bad": "nope", "also": None},
        }
    ]
    out = _layers_from_coverage(rows)
    assert len(out) == 1
    layer = out[0]
    assert layer.layer == "L4_source_abi"
    assert layer.facts == 0  # non-numeric facts fell back to 0
    assert layer.counters == {"matched_symbols": 3}  # bad counters dropped


# ── _scan_subprocess_worker: run-in-child entry (lines 874-883) ───────────────


class _FakeQueue:
    def __init__(self) -> None:
        self.items: list[tuple[str, object]] = []

    def put(self, item: tuple[str, object]) -> None:
        self.items.append(item)


def test_scan_subprocess_worker_conveys_ok(
    monkeypatch: pytest.MonkeyPatch, snap_path: Path
) -> None:
    # Neutralize the process-group detach so the test's own session is untouched,
    # then run the worker in-process and confirm it ships an ("ok", dict) payload.
    monkeypatch.setattr(os, "setsid", lambda: None, raising=False)
    q = _FakeQueue()
    _scan_subprocess_worker(ScanRequest(binaries=[snap_path], mode="audit"), q)
    assert len(q.items) == 1
    status, payload = q.items[0]
    assert status == "ok"
    assert isinstance(payload, dict)
    assert "verdict" in payload and "layers" in payload


def test_scan_subprocess_worker_conveys_error(
    monkeypatch: pytest.MonkeyPatch, snap_path: Path
) -> None:
    # run_scan raises for != 1 binary; the worker must convert the exception into a
    # sanitized ("err", "Type: message") pair rather than crash.
    monkeypatch.setattr(os, "setsid", lambda: None, raising=False)
    q = _FakeQueue()
    _scan_subprocess_worker(
        ScanRequest(binaries=[snap_path, snap_path], mode="audit"), q
    )
    assert len(q.items) == 1
    status, payload = q.items[0]
    assert status == "err"
    assert isinstance(payload, str)
    assert payload.startswith("ValueError:")


def test_scan_subprocess_worker_ignores_setsid_failure(
    monkeypatch: pytest.MonkeyPatch, snap_path: Path
) -> None:
    # A non-POSIX / already-leader setsid raises; the worker swallows it and still
    # produces a result.
    def _raise() -> None:
        raise OSError("no setsid")

    monkeypatch.setattr(os, "setsid", _raise, raising=False)
    q = _FakeQueue()
    _scan_subprocess_worker(ScanRequest(binaries=[snap_path], mode="audit"), q)
    assert q.items and q.items[0][0] == "ok"


# ── _kill_process_tree: the terminate/killpg branches (lines 886-912) ─────────


class _FakeProc:
    def __init__(self, alive: bool = True, pid: int = 4321) -> None:
        self._alive = alive
        self.pid = pid
        self.terminated = 0
        self.joins: list[float | None] = []

    def is_alive(self) -> bool:
        return self._alive

    def terminate(self) -> None:
        self.terminated += 1

    def join(self, timeout: float | None = None) -> None:
        self.joins.append(timeout)


def test_kill_process_tree_noop_when_dead() -> None:
    proc = _FakeProc(alive=False)
    _kill_process_tree(proc)
    assert proc.terminated == 0 and proc.joins == []


@_posix_process_groups
def test_kill_process_tree_kills_own_group_via_terminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # When the child never detached (its pgid still equals the parent's group),
    # killpg would nuke the parent — so terminate() the single process instead.
    monkeypatch.setattr(os, "getpgid", lambda _pid: 777)
    monkeypatch.setattr(os, "getpgrp", lambda: 777)
    proc = _FakeProc()
    _kill_process_tree(proc)
    assert proc.terminated == 1
    assert 5 in proc.joins  # the trailing reap join


@_posix_process_groups
def test_kill_process_tree_kills_detached_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A detached child (own process group) is killed group-wide: SIGTERM, and —
    # because our fake stays alive — an escalation SIGKILL.
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "getpgid", lambda _pid: 999)
    monkeypatch.setattr(os, "getpgrp", lambda: 111)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: signals.append((pgid, sig)))
    proc = _FakeProc()  # stays alive → triggers the SIGKILL escalation
    _kill_process_tree(proc)
    import signal

    assert (999, signal.SIGTERM) in signals
    assert (999, signal.SIGKILL) in signals
    assert 3 in proc.joins and 5 in proc.joins


@_posix_process_groups
def test_kill_process_tree_kills_detached_descendant_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex review (PR #591): a clang/castxml child spawned via
    deadline.run_bounded's start_new_session=True detaches into its OWN
    session/pgid, distinct from the worker's own group — killing only
    proc.pid's own group used to leave that child running as an orphan once
    the worker's group-kill fired. _descendant_pgids must find it by walking
    the live PPID tree, and _kill_process_tree must killpg it too."""
    signals: list[tuple[int, int]] = []
    # Worker pid 4321 is a detached group leader (pgid 4321, distinct from
    # this test's own group 111); its child 5555 (standing in for a clang/
    # castxml invocation) further detached into ITS OWN group (pgid 5555) —
    # exactly what deadline.run_bounded's start_new_session=True produces.
    monkeypatch.setattr(
        os, "getpgid", lambda pid: {4321: 4321, 5555: 5555}.get(pid, pid)
    )
    monkeypatch.setattr(os, "getpgrp", lambda: 111)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: signals.append((pgid, sig)))

    def _fake_ps(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert cmd[0] == "ps"
        return subprocess.CompletedProcess(cmd, 0, stdout="4321 100\n5555 4321\n", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_ps)
    proc = _FakeProc(pid=4321)  # stays alive → triggers the SIGKILL escalation
    _kill_process_tree(proc)
    import signal

    assert (4321, signal.SIGTERM) in signals
    assert (5555, signal.SIGTERM) in signals
    assert (4321, signal.SIGKILL) in signals
    assert (5555, signal.SIGKILL) in signals


@_posix_process_groups
def test_kill_process_tree_falls_back_on_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # If getpgid/killpg raise (race: pid already gone), fall back to terminate().
    def _boom(_pid: int) -> int:
        raise ProcessLookupError("gone")

    monkeypatch.setattr(os, "getpgid", _boom)
    proc = _FakeProc()
    _kill_process_tree(proc)
    assert proc.terminated == 1
    assert 5 in proc.joins


@_posix_process_groups
def test_kill_process_tree_swallows_terminate_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The doubly-defensive path: getpgid raises, so we fall back to terminate() —
    # and terminate() *also* raises. The inner guard swallows it, and the trailing
    # reap join still runs, so the helper never propagates.
    monkeypatch.setattr(
        os, "getpgid", lambda _pid: (_ for _ in ()).throw(OSError("gone"))
    )

    class _StubbornProc(_FakeProc):
        def terminate(self) -> None:
            super().terminate()
            raise OSError("terminate failed")

    proc = _StubbornProc()
    _kill_process_tree(proc)  # must not raise
    assert proc.terminated == 1
    assert 5 in proc.joins


# ── run_scan_subprocess: the killable MCP harness (lines 915-943) ─────────────


def test_run_scan_subprocess_returns_result_dict(snap_path: Path) -> None:
    # End-to-end: the child runs the scan under spawn and ships back the
    # ScanResult.to_dict() payload; the parent returns it unchanged.
    payload = run_scan_subprocess(
        ScanRequest(binaries=[snap_path], mode="audit"), timeout=120.0
    )
    assert isinstance(payload, dict)
    assert "verdict" in payload
    assert isinstance(payload["layers"], list)


def test_run_scan_subprocess_propagates_worker_error(snap_path: Path) -> None:
    # A worker-side failure (two binaries → ValueError) is re-raised as a
    # RuntimeError carrying the sanitized message.
    with pytest.raises(RuntimeError, match="ValueError"):
        run_scan_subprocess(
            ScanRequest(binaries=[snap_path, snap_path], mode="audit"),
            timeout=120.0,
        )


def test_run_scan_subprocess_times_out(snap_path: Path) -> None:
    # A timeout shorter than even the spawn/import startup forces the queue.get to
    # raise Empty → TimeoutError, and the still-starting child is killed.
    with pytest.raises(TimeoutError, match="exceeded"):
        run_scan_subprocess(
            ScanRequest(binaries=[snap_path], mode="audit"), timeout=0.001
        )
