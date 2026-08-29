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
import signal
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
    _descendant_pgids,
    _kill_process_tree,
    _layers_from_coverage,
    _reject_comparison_only_fields,
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


# ── _reject_comparison_only_fields: shared by run_scan / run_scan_set ─────────
#
# Ported from a former MCP `abi_scan` end-to-end test (the only place this
# behaviour used to be exercised) when the MCP server was removed — the rule
# itself is Tier-2 (`service_scan.py`) and belongs at this layer, not tied to
# any one front end.


class TestRejectComparisonOnlyFields:
    def _audit_request(self, **overrides: object) -> ScanRequest:
        return ScanRequest(binaries=[Path("lib.so")], mode="audit", **overrides)

    @pytest.mark.parametrize(
        ("overrides", "field"),
        [
            ({"policy": "sdk_vendor"}, "policy"),
            ({"contract_evaluation": True}, "contract_evaluation"),
            ({"policy_file": object()}, "policy_file"),
            ({"suppression": object()}, "suppression"),
            ({"max_findings": 5}, "max_findings"),
        ],
    )
    def test_rejects_a_comparison_only_field_without_a_baseline(
        self, overrides: dict, field: str
    ) -> None:
        req = self._audit_request(**overrides)
        with pytest.raises(ValidationError, match=field):
            _reject_comparison_only_fields(req)

    def test_plain_audit_request_is_accepted(self) -> None:
        assert _reject_comparison_only_fields(self._audit_request()) is None


class TestCliApiParity:
    """Structural regression tests generalizing two gaps a PR #724 review
    round found in this exact area, on two different fields (first several
    policy/contract fields via `run_scan_set()`'s missing guard, most
    recently `max_findings` reaching `run_scan_core` with no override, no
    validation, and no typed-API field at all): a CLI-only knob threaded
    through `run_scan_core`/`_run_baseline_compare` silently has no typed-API
    equivalent, or a `ScanRequest` field meaningful only for a baseline
    comparison silently has no guard. Both were previously only caught by
    hand-picked example tests (`test_rejects_a_comparison_only_field_without_
    a_baseline` above) -- which only proves the *listed* fields are guarded,
    never that the list itself is complete. These derive the expected set
    from the real function signatures instead, so a newly-added field that
    should join one of these lists fails CI on its own, without a human
    remembering to extend the example list too.
    """

    #: `_run_baseline_compare`/`ScanRequest` share these parameter names, but
    #: both matter for a plain one-build audit too -- not baseline-only.
    _ALWAYS_RELEVANT_FIELDS = frozenset(
        {"headers", "includes", "public_header_dirs", "lang", "baseline"}
    )

    def test_every_baseline_only_field_is_guarded(self) -> None:
        import inspect
        from dataclasses import fields

        from abicheck.cli_scan_baseline import _run_baseline_compare
        from abicheck.service_scan import _COMPARISON_ONLY_FIELD_PREDICATES

        baseline_only_params = set(inspect.signature(_run_baseline_compare).parameters)
        req_field_names = {f.name for f in fields(ScanRequest)}
        candidates = (
            baseline_only_params & req_field_names
        ) - self._ALWAYS_RELEVANT_FIELDS
        missing = candidates - set(_COMPARISON_ONLY_FIELD_PREDICATES)
        assert not missing, (
            f"ScanRequest field(s) {sorted(missing)} share a name with a "
            "_run_baseline_compare parameter but aren't in "
            "_COMPARISON_ONLY_FIELD_PREDICATES -- a caller can set them "
            "without --against and get a silent no-op instead of a "
            "ValidationError."
        )

    def test_every_run_scan_core_kwarg_matching_a_scan_request_field_is_forwarded(
        self,
    ) -> None:
        import ast
        import inspect
        import textwrap
        from dataclasses import fields

        from abicheck.scan_engine import run_scan_core
        from abicheck.service_scan import run_scan

        core_params = set(inspect.signature(run_scan_core).parameters)
        req_field_names = {f.name for f in fields(ScanRequest)}
        candidates = core_params & req_field_names

        tree = ast.parse(textwrap.dedent(inspect.getsource(run_scan)))
        forwarded: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == (
                "run_scan_core"
            ):
                forwarded = {kw.arg for kw in node.keywords if kw.arg}

        missing = candidates - forwarded
        assert not missing, (
            f"ScanRequest field(s) {sorted(missing)} share a name with a "
            "run_scan_core parameter but aren't passed by run_scan()'s call "
            "to it -- the field exists on ScanRequest but silently does "
            "nothing (the exact shape of the ScanRequest.max_findings gap a "
            "Codex review found on PR #724)."
        )


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


def test_scan_subprocess_worker_installs_sigterm_cleanup(
    monkeypatch: pytest.MonkeyPatch, snap_path: Path
) -> None:
    # Codex review (PR #591), round 9: _kill_process_tree's _descendant_pgids()
    # walk is a point-in-time snapshot taken before proc.terminate() fires; a
    # clang/castxml child this worker spawns via deadline.run_bounded() in the
    # gap between that snapshot and the terminate() call is invisible to it.
    # Without its own SIGTERM handler installed, proc.terminate() (default
    # disposition, since this is a fresh spawn-context process with nothing
    # inherited) would kill the worker immediately, orphaning that child. The
    # worker must install its own cleanup handler so its own _active_pgroups
    # registry gets a chance to sweep whatever it has registered, independent
    # of what the outer snapshot did or didn't see.
    from abicheck import deadline

    monkeypatch.setattr(os, "setsid", lambda: None, raising=False)
    calls: list[bool] = []
    monkeypatch.setattr(deadline, "install_sigterm_cleanup", lambda: calls.append(True))
    q = _FakeQueue()
    _scan_subprocess_worker(ScanRequest(binaries=[snap_path], mode="audit"), q)
    assert calls == [True]


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
        return subprocess.CompletedProcess(
            cmd, 0, stdout="4321 100\n5555 4321\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", _fake_ps)
    proc = _FakeProc(pid=4321)  # stays alive → triggers the SIGKILL escalation
    _kill_process_tree(proc)
    import signal

    assert (4321, signal.SIGTERM) in signals
    assert (5555, signal.SIGTERM) in signals
    assert (4321, signal.SIGKILL) in signals
    assert (5555, signal.SIGKILL) in signals


@_posix_process_groups
def test_kill_process_tree_terminates_worker_even_with_detached_descendants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CodeRabbit review (PR #591): when the worker itself never detached
    (its pgid still equals the parent's group, so it's excluded from the
    pgid set to avoid killpg-ing the MCP server) but it spawned a detached
    clang/castxml descendant (pgids stays non-empty), proc.terminate() must
    still run -- it used to be skipped whenever pgids was non-empty,
    leaving the direct worker process running forever."""
    monkeypatch.setattr(
        os, "getpgid", lambda pid: {4321: 111, 5555: 5555}.get(pid, pid)
    )
    monkeypatch.setattr(os, "getpgrp", lambda: 111)  # worker never detached
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: None)

    def _fake_ps(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert cmd[0] == "ps"
        return subprocess.CompletedProcess(
            cmd, 0, stdout="4321 100\n5555 4321\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", _fake_ps)
    proc = _FakeProc(pid=4321)  # stays alive → also exercises the killpg sweep
    _kill_process_tree(proc)

    assert proc.terminated == 1


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


@_posix_process_groups
def test_kill_process_tree_getpgrp_failure_falls_back_to_terminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # os.getpgrp() itself can raise on a platform without process groups
    # (AttributeError, the same guard the rest of this helper uses) — fall
    # back to a plain terminate() before ever touching getpgid/killpg.
    monkeypatch.setattr(
        os, "getpgrp", lambda: (_ for _ in ()).throw(AttributeError("no getpgrp"))
    )
    proc = _FakeProc()
    _kill_process_tree(proc)  # must not raise
    assert proc.terminated == 1
    assert 5 in proc.joins


@_posix_process_groups
def test_kill_process_tree_getpgrp_and_terminate_both_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The doubly-defensive path for the getpgrp-failure fallback itself:
    # os.getpgrp() raises, and the subsequent terminate() *also* raises. Both
    # must be swallowed — the trailing reap join still runs, never propagates.
    monkeypatch.setattr(
        os, "getpgrp", lambda: (_ for _ in ()).throw(AttributeError("no getpgrp"))
    )

    class _StubbornProc(_FakeProc):
        def terminate(self) -> None:
            super().terminate()
            raise OSError("terminate failed")

    proc = _StubbornProc()
    _kill_process_tree(proc)  # must not raise
    assert proc.terminated == 1
    assert 5 in proc.joins


@_posix_process_groups
def test_kill_process_tree_sigterm_failure_on_one_pgid_still_signals_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Multiple detached groups (the worker's own + a descendant's): one
    # killpg(SIGTERM) call raising must not abort the sweep over the rest.
    monkeypatch.setattr(
        os, "getpgid", lambda pid: {4321: 4321, 5555: 5555}.get(pid, pid)
    )
    monkeypatch.setattr(os, "getpgrp", lambda: 111)
    signals: list[tuple[int, int]] = []

    def _killpg(pgid: int, sig: int) -> None:
        if pgid == 4321 and sig == signal.SIGTERM:
            raise PermissionError("no permission for this group")
        signals.append((pgid, sig))

    monkeypatch.setattr(os, "killpg", _killpg)

    def _fake_ps(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd, 0, stdout="4321 100\n5555 4321\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", _fake_ps)
    proc = _FakeProc(pid=4321)
    _kill_process_tree(proc)  # must not raise despite the first killpg failing
    assert (5555, signal.SIGTERM) in signals
    assert (4321, signal.SIGKILL) in signals
    assert (5555, signal.SIGKILL) in signals


# ── _descendant_pgids: ps-output parsing edge cases (best-effort, never raises) ──


@_posix_process_groups
def test_descendant_pgids_empty_on_ps_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("ps not found")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert _descendant_pgids(4321) == set()


@_posix_process_groups
def test_descendant_pgids_skips_malformed_and_non_integer_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A malformed `ps` line (wrong field count) or a non-integer pid/ppid must
    # be skipped rather than raising, while well-formed lines still parse.
    def _fake_ps(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        stdout = "not-a-valid-line\n4321 abc\nabc 4321\n5555 4321\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_ps)
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    assert _descendant_pgids(4321) == {4321, 5555}


@_posix_process_groups
def test_descendant_pgids_does_not_loop_on_a_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A pid appearing as its own (indirect) child — via a race in the ps
    # snapshot, or a re-parented pid reused mid-walk — must not spin forever;
    # the `seen` set stops the walk from revisiting it.
    def _fake_ps(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        stdout = "5555 4321\n4321 5555\n"  # 4321 <-> 5555, a two-node cycle
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_ps)
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    assert _descendant_pgids(4321) == {4321, 5555}


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


def test_run_scan_subprocess_joins_already_exited_worker(
    monkeypatch: pytest.MonkeyPatch, snap_path: Path
) -> None:
    # When the worker has already finished by the time the parent checks
    # (the common case — q.get() already returned the payload), cleanup is a
    # plain join(), not the process-tree kill (that path is only for a
    # still-running worker after a timeout).
    import multiprocessing
    import queue as _queue_mod

    class _FakeProcess:
        def __init__(self, target, args, daemon) -> None:
            self._target = target
            self._args = args
            self.joined: list[float | None] = []

        def start(self) -> None:
            self._target(*self._args)

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            self.joined.append(timeout)

    class _FakeCtx:
        def Queue(self):  # noqa: N802 - matches multiprocessing.Queue's name
            return _queue_mod.Queue()

        def Process(self, target, args, daemon):  # noqa: N802
            return _FakeProcess(target, args, daemon)

    monkeypatch.setattr(multiprocessing, "get_context", lambda _name: _FakeCtx())
    payload = run_scan_subprocess(
        ScanRequest(binaries=[snap_path], mode="audit"), timeout=5.0
    )
    assert isinstance(payload, dict)
    assert "verdict" in payload


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


# ── run_scan_set_subprocess: the artifact-set killable MCP harness (G35) ──────
#
# Reuses test_run_scan_subprocess_joins_already_exited_worker's in-process
# _FakeCtx/_FakeProcess trick: the "child" runs synchronously in this same
# process, so monkeypatching service_scan.run_scan_set (the module-global the
# worker calls) drives the real _scan_set_subprocess_worker/
# run_scan_set_subprocess boundary logic without paying for a real spawn or
# needing real ELF fixtures.


class _FakeSetCtx:
    def __init__(self) -> None:
        import queue as _queue_mod

        self.queue = _queue_mod.Queue()

    def Queue(self):  # noqa: N802 - matches multiprocessing.Queue's name
        return self.queue

    def Process(self, target, args, daemon):  # noqa: N802
        class _FakeProcess:
            def start(self) -> None:
                target(*args)

            def is_alive(self) -> bool:
                return False

            def join(self, timeout: float | None = None) -> None:
                pass

        return _FakeProcess()


def test_run_scan_set_subprocess_propagates_artifact_set_error(
    monkeypatch: pytest.MonkeyPatch, snap_path: Path
) -> None:
    # P2 regression (Codex review): an ArtifactSetError (ValueError subclass)
    # raised inside run_scan_set must cross the subprocess boundary as a real
    # ValueError, carrying its own actionable message -- not collapse into a
    # generic RuntimeError an MCP tool's _sanitize_error would then report as
    # a bare "unexpected error".
    import multiprocessing

    import abicheck.service_scan as service_scan_mod
    from abicheck.bundle import ArtifactSetError

    def _fake_run_scan_set(req):
        raise ArtifactSetError(
            "--artifact-set has ambiguous duplicate SONAME provider(s): x"
        )

    monkeypatch.setattr(multiprocessing, "get_context", lambda _name: _FakeSetCtx())
    monkeypatch.setattr(service_scan_mod, "run_scan_set", _fake_run_scan_set)
    with pytest.raises(ValueError, match="ambiguous duplicate SONAME"):
        service_scan_mod.run_scan_set_subprocess(
            ScanRequest(binaries=[snap_path, snap_path], mode="audit"),
            timeout=120.0,
        )


def test_run_scan_set_subprocess_propagates_plain_value_error(
    monkeypatch: pytest.MonkeyPatch, snap_path: Path
) -> None:
    # The cardinality/baseline ValueErrors run_scan_set raises itself (not
    # via ArtifactSetError) get the same real-ValueError treatment.
    import multiprocessing

    import abicheck.service_scan as service_scan_mod

    def _fake_run_scan_set(req):
        raise ValueError("run_scan_set requires 2 or more distinct binaries")

    monkeypatch.setattr(multiprocessing, "get_context", lambda _name: _FakeSetCtx())
    monkeypatch.setattr(service_scan_mod, "run_scan_set", _fake_run_scan_set)
    with pytest.raises(ValueError, match="2 or more distinct binaries"):
        service_scan_mod.run_scan_set_subprocess(
            ScanRequest(binaries=[snap_path, snap_path], mode="audit"),
            timeout=120.0,
        )


def test_run_scan_set_subprocess_propagates_unexpected_error_as_runtime_error(
    monkeypatch: pytest.MonkeyPatch, snap_path: Path
) -> None:
    # Anything other than a ValueError still becomes the generic RuntimeError
    # (unchanged behavior) -- this fix is additive, not a blanket pass-through.
    import multiprocessing

    import abicheck.service_scan as service_scan_mod

    def _fake_run_scan_set(req):
        raise RuntimeError("boom")

    monkeypatch.setattr(multiprocessing, "get_context", lambda _name: _FakeSetCtx())
    monkeypatch.setattr(service_scan_mod, "run_scan_set", _fake_run_scan_set)
    with pytest.raises(RuntimeError, match="boom"):
        service_scan_mod.run_scan_set_subprocess(
            ScanRequest(binaries=[snap_path, snap_path], mode="audit"),
            timeout=120.0,
        )


class TestResolveMemberScanLevel:
    """``_resolve_member_scan_level`` is the one resolution
    ``_run_scan_one_member`` and ``estimate_artifact_set`` (``scan
    --artifact-set --dry-run``) both consume (Codex review, ``workflows/
    AGENTS.md``'s "dry-run and execution must consume the same resolved
    plan" rule) -- test it directly rather than only through those two
    callers.
    """

    def test_raises_on_malformed_risk_rules(self, tmp_path: Path) -> None:
        import abicheck.service_scan as service_scan_mod

        bad = tmp_path / "bad.yml"
        bad.write_text("risk_rules: [1, 2\n  - broken")
        req = ScanRequest(
            binaries=[], mode="audit", source_method="auto",
            changed_paths=["src/foo.c"], seeded=True, risk_rules_path=bad,
        )
        with pytest.raises(ValueError, match="cannot read --risk-rules"):
            service_scan_mod._resolve_member_scan_level(req)

    def test_pinned_depth_ignores_risk_rules_default_and_still_resolves(self) -> None:
        import abicheck.service_scan as service_scan_mod

        req = ScanRequest(binaries=[], mode="audit", depth="build")
        sm, dp, changed, seeded, risk, resolved, eff_depth, collect_mode = (
            service_scan_mod._resolve_member_scan_level(req)
        )
        assert sm is None
        assert eff_depth.value == "build"
        assert changed == []
        assert seeded is False

    def test_seeded_auto_uses_risk_recommended_method(self) -> None:
        import abicheck.service_scan as service_scan_mod

        req = ScanRequest(
            binaries=[], mode="audit", source_method="auto",
            changed_paths=["include/public.h"], seeded=True,
        )
        _sm, _dp, changed, seeded, risk, resolved, _eff_depth, _collect_mode = (
            service_scan_mod._resolve_member_scan_level(req)
        )
        assert changed == ["include/public.h"]
        assert seeded is True
        assert resolved.value == risk.recommended_method
