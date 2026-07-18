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

"""Zero-config build-system inference (ADR-032 amendment): ``--sources`` alone
must detect the build system and run abicheck's own query — no
``--allow-build-query`` flag, no manual compile step. Pure detection / command
construction tested here; the live subprocess is exercised behind a stub."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from abicheck.buildsource.build_evidence import BuildEvidence
from abicheck.buildsource.build_query import (
    ABICHECK_BUILD_DIR,
    detect_build_system,
    inferred_query_command,
    run_inferred_build_query,
)


def test_detect_cmake(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    assert detect_build_system(tmp_path) == "cmake"


def test_detect_bazel(tmp_path: Path):
    (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")
    assert detect_build_system(tmp_path) == "bazel"


def test_detect_make(tmp_path: Path):
    (tmp_path / "Makefile").write_text("all:\n\techo hi\n")
    assert detect_build_system(tmp_path) == "make"


def test_cmake_wins_over_make_when_both_present(tmp_path: Path):
    # A CMake project often ships a convenience Makefile; CMake is authoritative.
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    (tmp_path / "Makefile").write_text("all:\n")
    assert detect_build_system(tmp_path) == "cmake"


def test_detect_none_for_plain_dir(tmp_path: Path):
    assert detect_build_system(tmp_path) == ""
    assert detect_build_system(None) == ""


def test_cmake_command_is_fixed_and_uses_export_flag(tmp_path: Path):
    cmd = inferred_query_command("cmake", tmp_path)
    assert cmd is not None
    assert cmd[0] == "cmake"
    assert "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON" in cmd
    assert str(tmp_path / ABICHECK_BUILD_DIR) in cmd


def test_make_command_is_fixed_dry_run(tmp_path: Path):
    cmd = inferred_query_command("make", tmp_path)
    assert cmd == ["make", "-B", "-n", "-k", "-w"]


def test_make_command_accepts_gnu_launcher(tmp_path: Path):
    cmd = inferred_query_command("make", tmp_path, make_launcher="gmake")
    assert cmd == ["gmake", "-B", "-n", "-k", "-w"]


def test_unknown_system_has_no_command(tmp_path: Path):
    assert inferred_query_command("scons", tmp_path) is None


def test_run_skips_with_diagnostic_when_tool_missing(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    merged = BuildEvidence()
    extractors: list = []
    # Simulate cmake not installed.
    out = run_inferred_build_query(
        tmp_path, merged, extractors, which=lambda _tool: None
    )
    assert out is None
    assert len(extractors) == 1
    rec = extractors[0]
    assert rec.status == "skipped"
    assert "not installed" in rec.detail


def test_run_returns_none_for_non_build_tree(tmp_path: Path):
    merged = BuildEvidence()
    extractors: list = []
    assert run_inferred_build_query(tmp_path, merged, extractors) is None
    assert extractors == []  # nothing detected -> no noise


# ── runner paths (subprocess stubbed) ────────────────────────────────────────

from abicheck.buildsource import build_query as _bq  # noqa: E402


def test_resolved_make_launcher_path_is_available_without_second_which():
    # CI stubs may hand back a resolved POSIX path while the host Path class is
    # WindowsPath. Treat the path-like spelling as already selected instead of
    # asking `which('/usr/bin/make')`, which would incorrectly skip the query.
    assert _bq._query_tool_available("/usr/bin/make", lambda _tool: None)
    assert _bq._query_tool_available(
        r"C:\Program Files\GnuWin32\bin\make.exe", lambda _tool: None
    )


def test_gnu_make_probe_bounded_by_local_cap_not_full_scan_budget(monkeypatch):
    """Codex review (PR #591), round 2: deadline.run_bounded() honors an
    active outer deadline verbatim (not min(timeout, left)), so a bare
    timeout=10 alone did nothing once a generous --budget was active -- a
    hung `make --version` wrapper could consume the whole remaining scan
    budget instead of this probe's own 10s cap. Mirrors the include-map
    local-cap fix."""
    from abicheck import deadline

    seen_remaining: list[float | None] = []

    def fake_run(*_a, **_k):
        seen_remaining.append(deadline.remaining())
        return _FakeProc(0, stdout="GNU Make 4.4\n")

    monkeypatch.setattr(_bq.deadline, "run_bounded", fake_run)
    with deadline.deadline_scope(1800.0):  # a generous 30-minute --budget
        assert _bq._is_gnu_make_launcher("/usr/bin/make") is True

    assert seen_remaining
    # Bound by the probe's own 10s cap, not the 1800s scan budget.
    assert seen_remaining[0] is not None and seen_remaining[0] <= 10.5


def test_gnu_make_probe_local_cap_hit_with_generous_scan_budget_returns_false(
    monkeypatch,
):
    """CodeRabbit review (PR #591): hitting this probe's OWN 10s cap is an
    ordinary "not GNU Make" result, even with an active outer --budget, as
    long as that outer budget still had more than 10s left when the probe
    started (so the local cap, not the scan deadline, was what actually
    bound the nested scope)."""
    from abicheck import deadline

    monkeypatch.setattr(
        _bq.deadline,
        "run_bounded",
        lambda *_a, **_k: (_ for _ in ()).throw(deadline.DeadlineExceeded(-1.0)),
    )
    with deadline.deadline_scope(1800.0):  # generous 30-minute --budget
        assert _bq._is_gnu_make_launcher("/usr/bin/make") is False


def test_gnu_make_probe_propagates_genuine_scan_deadline_exhaustion(monkeypatch):
    """CodeRabbit review (PR #591): when the OUTER scan --budget (not this
    probe's own 10s local cap) is what actually expired, the resulting
    DeadlineExceeded must propagate -- silently returning False would
    misreport a genuine budget overflow as "GNU Make isn't installed" and
    could waste more time probing further launcher candidates instead of
    aborting."""
    from abicheck import deadline

    monkeypatch.setattr(
        _bq.deadline,
        "run_bounded",
        lambda *_a, **_k: (_ for _ in ()).throw(deadline.DeadlineExceeded(-1.0)),
    )
    with deadline.deadline_scope(0.0):  # already-exhausted outer budget
        with pytest.raises(deadline.DeadlineExceeded):
            _bq._is_gnu_make_launcher("/usr/bin/make")


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_cmake_ingests_out_of_tree_and_merges(tmp_path: Path, monkeypatch):
    # cmake configures into an OUT-OF-TREE temp dir (never under --sources); the
    # compile DB is ingested + merged and the temp dir removed. Returns None
    # (evidence merged), and nothing is written into the source tree.
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    seen_build_dirs: list[Path] = []

    def fake_run(cmd, **kw):
        bdir = Path(cmd[cmd.index("-B") + 1])
        seen_build_dirs.append(bdir)
        # The -B dir must NOT be under the source tree (out-of-tree contract).
        assert tmp_path not in bdir.parents and bdir != tmp_path
        bdir.mkdir(parents=True, exist_ok=True)
        src = tmp_path / "a.cpp"
        (bdir / "compile_commands.json").write_text(
            json.dumps(
                [
                    {
                        "directory": str(bdir),
                        "file": str(src),
                        "command": f"c++ -I{tmp_path} -c {src}",
                    }
                ]
            )
        )
        return _FakeProc(0)

    monkeypatch.setattr(_bq.deadline, "run_bounded", fake_run)
    merged, ext = BuildEvidence(), []
    out = run_inferred_build_query(tmp_path, merged, ext)
    assert out is None  # evidence merged, no path threaded
    assert merged.compile_units  # the cmake compile DB became L3 evidence
    assert ext[-1].status == "ok"
    assert not (tmp_path / ".abicheck-build").exists()  # nothing written in-tree
    assert seen_build_dirs and not seen_build_dirs[0].exists()  # temp dir cleaned up


def test_run_cmake_defers_build_dir_cleanup_when_requested(tmp_path: Path, monkeypatch):
    # With a cleanup list (the real collect_inline_pack path), the out-of-tree
    # build dir is NOT removed immediately: L4 replay runs clang with each compile
    # unit's `directory` (the build dir) as cwd, so it must outlive replay. A
    # removal+unlock thunk is appended for the caller to invoke afterwards (P1); the
    # dir (and its lock) survive until then.
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    seen_build_dirs: list[Path] = []

    def fake_run(cmd, **kw):
        bdir = Path(cmd[cmd.index("-B") + 1])
        seen_build_dirs.append(bdir)
        bdir.mkdir(parents=True, exist_ok=True)
        src = tmp_path / "a.cpp"
        (bdir / "compile_commands.json").write_text(
            json.dumps(
                [{"directory": str(bdir), "file": str(src), "command": f"c++ -c {src}"}]
            )
        )
        return _FakeProc(0)

    monkeypatch.setattr(_bq.deadline, "run_bounded", fake_run)
    merged, ext = BuildEvidence(), []
    cleanup: list = []
    out = run_inferred_build_query(tmp_path, merged, ext, cleanup=cleanup)
    assert out is None
    assert merged.compile_units
    bdir = seen_build_dirs[0]
    # Deferred: the dir is still alive (clang replay needs it as cwd) and a single
    # cleanup thunk was queued rather than a bare path.
    assert len(cleanup) == 1 and callable(cleanup[0]) and bdir.exists()
    cleanup[0]()  # caller invokes it post-replay → removes dir + releases lock
    assert not bdir.exists()


def test_inferred_cmake_build_dir_lock_contention_falls_back_to_unique(
    tmp_path: Path, monkeypatch
):
    # When another live scan of the same checkout already holds the deterministic
    # build dir's lock, this invocation must NOT share/await it forever: it falls
    # back to a unique sibling dir so concurrent scans never corrupt one mutable
    # tree or rmtree it out from under each other's L4 cwd (Codex P2).
    fcntl = pytest.importorskip("fcntl")
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    resolved = tmp_path.resolve()
    base = _bq._inferred_cmake_build_base(resolved)  # owner-private deterministic path
    assert base is not None
    lock_path = base.with_name(base.name + ".lock")
    held = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(held, fcntl.LOCK_EX)  # simulate a concurrent scan holding the lock
    try:
        seen: list[Path] = []

        def fake_run(cmd, **kw):
            seen.append(Path(cmd[cmd.index("-B") + 1]))
            return _FakeProc(0)

        monkeypatch.setattr(_bq.deadline, "run_bounded", fake_run)
        # timeout=0 → fall back immediately instead of polling the held lock.
        run_inferred_build_query(tmp_path, BuildEvidence(), [], timeout=0)
        assert seen, "the query should still run on a fallback dir"
        assert seen[0] != base  # not the contended deterministic path
        assert seen[0].name.startswith(base.name)  # a unique sibling of it
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        os.close(held)


def test_claim_build_dir_posix_flock_roundtrip(tmp_path: Path):
    # POSIX: the claim takes an flock on `<base>.lock`, returns the deterministic
    # path, and the release thunk unlocks it so the next sequential claim re-takes
    # the same path (cache stability preserved).
    pytest.importorskip("fcntl")
    base = tmp_path / "abicheck-cmake-cafef00d"
    bdir, release = _bq._claim_inferred_build_dir(base, timeout=0)
    assert bdir == base
    lock = base.with_name(base.name + ".lock")
    assert lock.exists()
    release()  # unlock + close; the .lock file is intentionally left behind
    bdir2, release2 = _bq._claim_inferred_build_dir(base, timeout=0)
    assert bdir2 == base  # re-claim after release is deterministic
    release2()


def test_claim_build_dir_polls_until_lock_free(tmp_path: Path, monkeypatch):
    # When the lock is briefly busy, the claim polls (sleeps) and retries rather
    # than failing or blocking forever, then takes the deterministic path once free.
    fcntl = pytest.importorskip("fcntl")
    base = tmp_path / "abicheck-cmake-feedface"
    calls = {"n": 0}
    real_flock = fcntl.flock

    def flaky_flock(fd, op):
        # First non-blocking attempt reports busy; the retry succeeds.
        if (op & fcntl.LOCK_NB) and calls["n"] == 0:
            calls["n"] += 1
            raise OSError("busy")
        return real_flock(fd, op)

    monkeypatch.setattr(fcntl, "flock", flaky_flock)
    monkeypatch.setattr(_bq.time, "sleep", lambda _s: None)  # don't actually wait
    bdir, release = _bq._claim_inferred_build_dir(base, timeout=5)
    assert bdir == base and calls["n"] == 1  # polled once, then acquired
    release()


def test_claim_build_dir_wait_bounded_by_scan_deadline_not_just_local_timeout(
    tmp_path: Path, monkeypatch
):
    # Codex review (PR #591, round 4): the lock-wait loop only consulted its
    # own *timeout* argument (up to 600s by default for the caller), never the
    # active scan --budget -- a contended checkout under a tight --budget
    # could still block here for the full local timeout before falling back
    # to a unique dir. Must bound the wait by whichever is tighter.
    fcntl = pytest.importorskip("fcntl")
    from abicheck import deadline

    base = tmp_path / "abicheck-cmake-deadbeef"
    calls = {"n": 0}

    def always_busy(fd, op):
        calls["n"] += 1
        raise OSError("busy")

    monkeypatch.setattr(fcntl, "flock", always_busy)
    monkeypatch.setattr(_bq.time, "sleep", lambda _s: None)  # don't actually wait
    with deadline.deadline_scope(0.5):  # far tighter than the 5s local timeout
        bdir, release = _bq._claim_inferred_build_dir(base, timeout=5.0)
    release()
    assert bdir != base  # fell back to a unique dir
    # Bounded by the ~0.5s scan deadline (3 polls at the 0.2s poll interval),
    # not the full 5s local timeout (25 polls).
    assert calls["n"] <= 4


def test_claim_build_dir_marker_fallback_when_no_fcntl(tmp_path: Path, monkeypatch):
    # Without fcntl (e.g. Windows), the claim uses an O_CREAT|O_EXCL marker file:
    # sequential claims re-take the deterministic path (marker removed on release),
    # while a concurrent claim (marker still present) falls back to a unique dir.
    monkeypatch.setitem(sys.modules, "fcntl", None)  # force `import fcntl` to fail
    base = tmp_path / "abicheck-cmake-deadbeef"
    marker = base.with_name(base.name + ".lock")

    bdir, release = _bq._claim_inferred_build_dir(base, timeout=0)
    assert bdir == base and marker.exists()  # first claim owns the marker

    # Second claim while the marker is held → unique sibling dir, no lock.
    bdir2, release2 = _bq._claim_inferred_build_dir(base, timeout=0)
    assert bdir2 != base and bdir2.name.startswith(base.name)
    release2()  # no-op for the fallback
    bdir2.rmdir()  # mkdtemp left an empty dir

    release()  # removes the marker so a later sequential claim can re-take base
    assert not marker.exists()
    bdir3, release3 = _bq._claim_inferred_build_dir(base, timeout=0)
    assert bdir3 == base  # sequential re-claim is deterministic
    release3()


def test_private_tmp_root_is_owner_only_and_under_tmp(tmp_path: Path, monkeypatch):
    # The inferred cmake build dir lives inside a per-user 0700 root so another
    # local user can't pre-plant the predictable path (Codex P2 symlink attack).
    pytest.importorskip("fcntl")  # POSIX uid/perm model
    monkeypatch.setattr(_bq.tempfile, "gettempdir", lambda: str(tmp_path))
    root = _bq._private_tmp_root()
    assert root is not None
    assert root.parent == tmp_path and root.is_dir()
    assert (root.stat().st_mode & 0o077) == 0  # no group/other access
    assert root.stat().st_uid == os.getuid()  # owned by us


def test_private_tmp_root_rejects_symlinked_root(tmp_path: Path, monkeypatch):
    # If the per-user root already exists as a symlink (attacker-planted), reject
    # it rather than follow it — the caller then refuses the inferred query.
    pytest.importorskip("fcntl")
    monkeypatch.setattr(_bq.tempfile, "gettempdir", lambda: str(tmp_path))
    victim = tmp_path / "victim"
    victim.mkdir()
    (tmp_path / f"abicheck-{os.getuid()}").symlink_to(victim, target_is_directory=True)
    assert _bq._private_tmp_root() is None


def test_inferred_cmake_build_base_none_without_private_root(monkeypatch):
    # No secure root → no base path (the caller turns this into a skip).
    monkeypatch.setattr(_bq, "_private_tmp_root", lambda: None)
    assert _bq._inferred_cmake_build_base(Path("/some/sources")) is None


def test_inferred_query_skipped_when_no_private_root(tmp_path: Path, monkeypatch):
    # When no secure private temp root can be established, the cmake query is
    # skipped with a diagnostic rather than configuring into a predictable path.
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    monkeypatch.setattr(_bq, "_inferred_cmake_build_base", lambda _sources: None)
    merged, ext = BuildEvidence(), []
    assert (
        run_inferred_build_query(
            tmp_path,
            merged,
            ext,
            which=lambda tool: f"/usr/bin/{tool}",
        )
        is None
    )
    assert ext[-1].status == "skipped" and "private temp" in ext[-1].detail


def test_run_cmake_no_db_is_partial(tmp_path: Path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    monkeypatch.setattr(_bq.deadline, "run_bounded", lambda cmd, **kw: _FakeProc(0))
    merged, ext = BuildEvidence(), []
    assert (
        run_inferred_build_query(
            tmp_path,
            merged,
            ext,
            which=lambda tool: f"/usr/bin/{tool}",
        )
        is None
    )
    assert ext[-1].status == "partial"


def test_inferred_cmake_build_dir_is_stable_per_source_tree(
    tmp_path: Path, monkeypatch
):
    # The out-of-tree cmake build dir is deterministic per resolved source tree,
    # so repeated zero-config scans record identical compile-unit `directory`/`-I`
    # paths — the L4 replay cache key and compile-unit IDs stay stable run-to-run
    # rather than churning on a random /tmp path (review P2).
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    seen: list[str] = []

    def fake_run(cmd, **kw):
        seen.append(cmd[cmd.index("-B") + 1])
        return _FakeProc(0)

    monkeypatch.setattr(_bq.deadline, "run_bounded", fake_run)
    for _ in range(2):
        run_inferred_build_query(tmp_path, BuildEvidence(), [])
    assert seen[0] == seen[1]  # deterministic across runs
    assert str(tmp_path) not in seen[0]  # out-of-tree


def test_run_nonzero_exit_is_failed(tmp_path: Path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    monkeypatch.setattr(
        _bq.deadline, "run_bounded", lambda cmd, **kw: _FakeProc(1, stderr="boom")
    )
    merged, ext = BuildEvidence(), []
    assert run_inferred_build_query(tmp_path, merged, ext) is None
    assert ext[-1].status == "failed"
    assert merged.diagnostics


def test_ingest_failure_degrades_to_diagnostic(tmp_path: Path, monkeypatch):
    # "Never raises": if ingesting a *successful* query's output blows up (bad
    # aquery JSON, temp-dir full), it degrades to a failed diagnostic, not an
    # exception that aborts the dump (review).
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    monkeypatch.setattr(_bq.deadline, "run_bounded", lambda cmd, **kw: _FakeProc(0))

    def boom(*a, **k):
        raise ValueError("malformed query output")

    monkeypatch.setattr(_bq, "_ingest_query_output", boom)
    merged, ext = BuildEvidence(), []
    assert run_inferred_build_query(tmp_path, merged, ext) is None
    assert ext[-1].status == "failed"
    assert "could not be ingested" in ext[-1].detail
    assert merged.diagnostics


def test_no_inferred_query_after_trusted_query_fails(tmp_path: Path, monkeypatch):
    # A trusted --build-query / --config build.query that fails must NOT fall
    # through to abicheck's default inferred cmake/bazel query — that would mask
    # the explicit failure with wrong (default) flags (review).
    from abicheck.buildsource import build_query as _bqmod, inline as _inline
    from abicheck.buildsource.inline import BuildConfig, _resolve_compile_db

    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    cfg = BuildConfig(query="my-configure --custom-flags")
    monkeypatch.setattr(_inline, "_run_build_query", lambda *a, **k: None)  # fails
    called = {"infer": False}

    def _infer(*a, **k):
        called["infer"] = True
        return None

    monkeypatch.setattr(_bqmod, "run_inferred_build_query", _infer)
    merged, ext = BuildEvidence(), []
    out = _resolve_compile_db(None, tmp_path, cfg, True, merged, ext)
    assert out is None
    assert called["infer"] is False  # explicit failure not masked by inferred query


def test_no_db_fallback_after_trusted_query_fails(tmp_path: Path, monkeypatch):
    # A failed trusted query must not be masked by a stale/auto-discovered DB
    # already in the tree from a prior/default configure (review): return None so
    # the failure surfaces, rather than collecting L3 with the wrong flags.
    from abicheck.buildsource import inline as _inline
    from abicheck.buildsource.inline import BuildConfig, _resolve_compile_db

    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    (tmp_path / "compile_commands.json").write_text("[]")  # stale DB present
    cfg = BuildConfig(query="my-configure --custom-flags")
    monkeypatch.setattr(_inline, "_run_build_query", lambda *a, **k: None)  # fails
    merged, ext = BuildEvidence(), []
    out = _resolve_compile_db(None, tmp_path, cfg, True, merged, ext)
    assert out is None


def test_trusted_query_missing_configured_db_does_not_autodiscover(
    tmp_path: Path, monkeypatch
):
    # A trusted query that exits 0 but does NOT write its *configured*
    # build.compile_db must surface as partial/None — not silently accept a stale
    # auto-discovered compile_commands.json carrying the wrong flags (Codex P2).
    from abicheck.buildsource import inline as _inline
    from abicheck.buildsource.inline import BuildConfig, _run_build_query

    (tmp_path / "compile_commands.json").write_text("[]")  # stale default DB present
    cfg = BuildConfig(query="my-configure", compile_db="build/compile_commands.json")
    # Query exits 0 but writes nothing at the configured path.
    monkeypatch.setattr(_inline.deadline, "run_bounded", lambda *a, **k: _FakeProc(0))
    merged, ext = BuildEvidence(), []
    out = _run_build_query(cfg, tmp_path, merged, ext)
    assert out is None  # configured DB missing → not masked by autodiscovery
    assert ext[-1].status == "partial"


def test_trusted_query_is_bound_by_active_scan_deadline(tmp_path: Path, monkeypatch):
    """Codex-review-class gap (PR #591): the operator-configured trusted
    build.query command shares the same reachability-inside-the-scan-
    deadline-scope as the zero-config inferred query, but only consulted its
    own local 300s default. Must go through deadline.run_bounded and degrade
    to a failed ExtractorRecord + diagnostic on overflow."""
    from abicheck import deadline
    from abicheck.buildsource import inline as _inline
    from abicheck.buildsource.inline import BuildConfig, _run_build_query

    def _raise(*_a, **_k):
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(_inline.deadline, "run_bounded", _raise)
    cfg = BuildConfig(query="my-configure")
    merged, ext = BuildEvidence(), []
    out = _run_build_query(cfg, tmp_path, merged, ext)
    assert out is None
    assert ext[-1].status == "failed"
    assert "scan deadline exceeded" in ext[-1].detail
    assert any("scan deadline exceeded" in d for d in merged.diagnostics)


def test_trusted_query_bounded_by_local_cap_not_full_scan_budget(
    tmp_path: Path, monkeypatch
):
    """Codex review (PR #591), round 8: deadline.run_bounded() honors an
    active outer deadline verbatim (not min(timeout, left)), so a bare
    timeout=_QUERY_TIMEOUT_S (300s) on the trusted build.query call alone
    did nothing once a scan --budget was active: the call stayed bound by
    the FULL remaining scan budget instead of this query's own 300s local
    cap. A hung configured query under a generous --budget could therefore
    burn the whole remaining scan instead of degrading after 300s. Assert
    the ContextVar deadline observed inside run_bounded is capped near the
    local cap, not the much larger outer scan budget."""
    from abicheck import deadline
    from abicheck.buildsource import inline as _inline
    from abicheck.buildsource.inline import BuildConfig, _run_build_query

    seen_remaining: list[float | None] = []

    def fake_run_bounded(*_a, **_k):
        seen_remaining.append(deadline.remaining())
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(_inline.deadline, "run_bounded", fake_run_bounded)
    cfg = BuildConfig(query="my-configure")
    merged, ext = BuildEvidence(), []
    with deadline.deadline_scope(1800.0):  # a generous 30-minute --budget
        _run_build_query(cfg, tmp_path, merged, ext)

    assert seen_remaining
    assert seen_remaining[0] is not None and seen_remaining[0] <= 300.5


def test_trusted_query_without_configured_db_autodiscovers(tmp_path: Path, monkeypatch):
    # Contrast: with no build.compile_db configured, a zero-exit query's
    # conventional compile_commands.json is still auto-discovered (the no-mask rule
    # applies only to an explicitly configured path).
    from abicheck.buildsource import inline as _inline
    from abicheck.buildsource.inline import BuildConfig, _run_build_query

    (tmp_path / "compile_commands.json").write_text("[]")
    cfg = BuildConfig(query="my-configure")  # no compile_db configured
    monkeypatch.setattr(_inline.deadline, "run_bounded", lambda *a, **k: _FakeProc(0))
    merged, ext = BuildEvidence(), []
    out = _run_build_query(cfg, tmp_path, merged, ext)
    assert out is not None and out.name == "compile_commands.json"
    assert ext[-1].status == "ok"


def test_inferred_query_runs_when_no_trusted_query_configured(
    tmp_path: Path, monkeypatch
):
    # Contrast: with no build.query configured, the zero-config inferred query
    # still runs (the fallback is only skipped after a trusted query *attempt*).
    from abicheck.buildsource import build_query as _bqmod
    from abicheck.buildsource.inline import BuildConfig, _resolve_compile_db

    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    cfg = BuildConfig()  # no query
    called = {"infer": False}

    def _infer(*a, **k):
        called["infer"] = True
        return None

    monkeypatch.setattr(_bqmod, "run_inferred_build_query", _infer)
    merged, ext = BuildEvidence(), []
    _resolve_compile_db(None, tmp_path, cfg, True, merged, ext)
    assert called["infer"] is True


def test_autodiscover_skips_stale_abicheck_build_dir(tmp_path: Path):
    # A stale `.abicheck-build/compile_commands.json` (an older in-tree inferred
    # CMake artifact) must NOT be returned by auto-discovery — otherwise a fresh
    # zero-config --sources run replays with stale flags instead of re-querying
    # the build (Codex P2).
    from abicheck.buildsource.build_query import ABICHECK_BUILD_DIR
    from abicheck.buildsource.inline import _autodiscover_compile_db

    stale = tmp_path / ABICHECK_BUILD_DIR
    stale.mkdir()
    (stale / "compile_commands.json").write_text("[]")
    assert _autodiscover_compile_db(tmp_path) is None  # stale dir ignored

    # A real out-of-tree dir is still discovered.
    real = tmp_path / "build"
    real.mkdir()
    (real / "compile_commands.json").write_text("[]")
    found = _autodiscover_compile_db(tmp_path)
    assert found is not None and found.parent.name == "build"


def test_no_inferred_query_after_build_info_miss(tmp_path: Path, monkeypatch):
    # An explicit --build-info that resolves to no compile DB must not be masked
    # by abicheck's default inferred query under different flags (review).
    from abicheck.buildsource import build_query as _bqmod
    from abicheck.buildsource.inline import BuildConfig, _resolve_compile_db

    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    empty_bi = tmp_path / "bi"
    empty_bi.mkdir()  # build-info dir with no compile_commands.json
    called = {"infer": False}

    def _infer(*a, **k):
        called["infer"] = True
        return None

    monkeypatch.setattr(_bqmod, "run_inferred_build_query", _infer)
    merged, ext = BuildEvidence(), []
    out = _resolve_compile_db(empty_bi, tmp_path, BuildConfig(), True, merged, ext)
    assert out is None
    assert called["infer"] is False


def test_no_inferred_query_after_explicit_compile_db_miss(tmp_path: Path, monkeypatch):
    # An *explicit* build.compile_db path (compile_db_explicit=True: CLI
    # --build-compile-db or operator --config) that matches nothing is not masked
    # by the inferred query — even if a stray DB exists to auto-discover (review).
    from abicheck.buildsource import build_query as _bqmod, inline as _inline
    from abicheck.buildsource.inline import BuildConfig, _resolve_compile_db

    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    (tmp_path / "compile_commands.json").write_text("[]")  # stray auto-discoverable DB
    called = {"infer": False, "autodiscover": False}

    def _infer(*a, **k):
        called["infer"] = True
        return None

    def _autodiscover(*a, **k):
        called["autodiscover"] = True
        return tmp_path / "compile_commands.json"

    monkeypatch.setattr(_bqmod, "run_inferred_build_query", _infer)
    monkeypatch.setattr(_inline, "_autodiscover_compile_db", _autodiscover)
    cfg = BuildConfig(compile_db="build/compile_commands.json")  # matches nothing
    merged, ext = BuildEvidence(), []
    out = _resolve_compile_db(
        None, tmp_path, cfg, True, merged, ext, compile_db_explicit=True
    )
    assert out is None  # explicit miss surfaces
    assert called["infer"] is False  # not masked by inferred query
    assert called["autodiscover"] is False  # nor by a stray auto-discovered DB


def test_inferred_query_runs_after_untrusted_compile_db_miss(
    tmp_path: Path, monkeypatch
):
    # A build.compile_db from an AUTO-DISCOVERED (untrusted) .abicheck.yml that
    # matches nothing must NOT suppress the zero-config inferred query — the user
    # didn't explicitly choose that path (review). Contrast with the trusted-config
    # miss above, which does suppress inference.
    from abicheck.buildsource import build_query as _bqmod
    from abicheck.buildsource.inline import BuildConfig, _resolve_compile_db

    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    called = {"infer": False}

    def _infer(*a, **k):
        called["infer"] = True
        return None

    monkeypatch.setattr(_bqmod, "run_inferred_build_query", _infer)
    cfg = BuildConfig(compile_db="build/compile_commands.json")  # matches nothing
    merged, ext = BuildEvidence(), []
    # build_config_trusted_for_query=False → auto-discovered config, not explicit.
    _resolve_compile_db(None, tmp_path, cfg, False, merged, ext)
    assert called["infer"] is True


def test_run_subprocess_error_is_failed(tmp_path: Path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")

    def boom(cmd, **kw):
        raise OSError("no cmake")

    monkeypatch.setattr(_bq.deadline, "run_bounded", boom)
    merged, ext = BuildEvidence(), []
    assert run_inferred_build_query(tmp_path, merged, ext) is None
    assert ext[-1].status == "failed"


def test_run_is_bound_by_active_scan_deadline(tmp_path: Path, monkeypatch):
    """Codex-review-class gap (PR #591): the zero-config cmake/bazel/make query
    had the identical bare-subprocess.run anti-pattern already fixed for
    call_graph.py/type_graph.py/include_graph.py — this runs inside
    run_scan_core's L2-L5 deadline scope (`scan --budget ... --depth source`
    on a project with no --build-info) but only consulted its own local
    600s default, never the active --budget. Must go through
    deadline.run_bounded and degrade to a failed ExtractorRecord + diagnostic,
    not hang past the budget."""
    from abicheck import deadline

    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")

    def _raise(*_a, **_k):
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(_bq.deadline, "run_bounded", _raise)
    merged, ext = BuildEvidence(), []
    out = run_inferred_build_query(tmp_path, merged, ext)
    assert out is None
    assert ext[-1].status == "failed"
    assert "scan deadline exceeded" in ext[-1].detail
    assert any("scan deadline exceeded" in d for d in merged.diagnostics)


def test_run_bounded_by_local_cap_not_full_scan_budget(tmp_path: Path, monkeypatch):
    """Codex review (PR #591), round 8: deadline.run_bounded() honors an
    active outer deadline verbatim (not min(timeout, left)), so a bare
    timeout=timeout (default 600s, INFERRED_QUERY_TIMEOUT_S) on the
    zero-config cmake/bazel/make query alone did nothing once a scan
    --budget was active: the call stayed bound by the FULL remaining scan
    budget instead of this query's own 600s local cap. A hung inferred
    query under a generous --budget could therefore burn the whole
    remaining scan instead of degrading after 600s. Assert the ContextVar
    deadline observed inside run_bounded is capped near the local cap, not
    the much larger outer scan budget."""
    from abicheck import deadline

    (tmp_path / "CMakeLists.txt").write_text("project(x)\n")
    seen_remaining: list[float | None] = []

    def fake_run_bounded(*_a, **_k):
        seen_remaining.append(deadline.remaining())
        raise deadline.DeadlineExceeded(-1.0)

    monkeypatch.setattr(_bq.deadline, "run_bounded", fake_run_bounded)
    merged, ext = BuildEvidence(), []
    with deadline.deadline_scope(1800.0):  # a generous 30-minute --budget
        run_inferred_build_query(tmp_path, merged, ext)

    assert seen_remaining
    assert seen_remaining[0] is not None and seen_remaining[0] <= 600.5


def test_run_make_ingests_dry_run_transcript(tmp_path: Path, monkeypatch):
    # Make is auto-queried by default and scraped through the reduced-confidence
    # MakeAdapter so Make/EPICS-style projects can collect L3 without a manual DB.
    (tmp_path / "Makefile").write_text(
        "all:\n\t$(CXX) -std=c++17 -Iinclude -c src/foo.cc -o foo.o\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src/foo.cc").write_text("int foo;\n")

    def fake_run(cmd, **kw):
        if cmd == ["/usr/bin/make", "--version"]:
            return _FakeProc(0, stdout="GNU Make 4.4\n")
        assert cmd == ["/usr/bin/make", "-B", "-n", "-k", "-w"]
        assert Path(kw["cwd"]) == tmp_path
        assert kw["stderr"] is _bq.subprocess.STDOUT
        return _FakeProc(
            0,
            stdout="c++ -std=c++17 -Iinclude -c src/foo.cc -o foo.o\n",
        )

    monkeypatch.setattr(_bq.deadline, "run_bounded", fake_run)
    merged, ext = BuildEvidence(), []
    assert (
        run_inferred_build_query(
            tmp_path,
            merged,
            ext,
            which=lambda tool: "/usr/bin/make" if tool == "make" else None,
        )
        is None
    )
    assert ext[-1].name == "build_query_auto"
    assert ext[-1].status == "ok"
    assert "1 compile unit" in ext[-1].detail
    assert len(merged.compile_units) == 1
    assert merged.compile_units[0].source == "src/foo.cc"


def test_run_make_keeps_partial_transcript_on_nonzero_exit(tmp_path: Path, monkeypatch):
    (tmp_path / "Makefile").write_text("all:\n\t$(CC) -c ok.c -o ok.o\n")

    def fake_run(cmd, **kw):
        if cmd == ["/usr/bin/make", "--version"]:
            return _FakeProc(0, stdout="GNU Make 4.4\n")
        assert cmd == ["/usr/bin/make", "-B", "-n", "-k", "-w"]
        return _FakeProc(
            2,
            stdout="cc -c ok.c -o ok.o\nmake: later target failed\n",
            stderr=None,
        )

    monkeypatch.setattr(_bq.deadline, "run_bounded", fake_run)
    merged, ext = BuildEvidence(), []
    assert (
        run_inferred_build_query(
            tmp_path,
            merged,
            ext,
            which=lambda tool: "/usr/bin/make" if tool == "make" else None,
        )
        is None
    )
    assert ext[-1].status == "partial"
    assert "make exited 2" in ext[-1].detail
    assert len(merged.compile_units) == 1


def test_run_make_prefers_make_when_gnu(tmp_path: Path, monkeypatch):
    (tmp_path / "Makefile").write_text("all:\n\t$(CC) -c ok.c -o ok.o\n")
    seen: list[list[str]] = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        if cmd == ["/usr/bin/make", "--version"]:
            return _FakeProc(0, stdout="GNU Make 4.4\n")
        assert cmd == ["/usr/bin/make", "-B", "-n", "-k", "-w"]
        return _FakeProc(0, stdout="cc -c ok.c -o ok.o\n")

    monkeypatch.setattr(_bq.deadline, "run_bounded", fake_run)
    merged, ext = BuildEvidence(), []
    assert (
        run_inferred_build_query(
            tmp_path, merged, ext, which=lambda tool: f"/usr/bin/{tool}"
        )
        is None
    )
    assert seen[0] == ["/usr/bin/make", "--version"]
    assert seen[1] == ["/usr/bin/make", "-B", "-n", "-k", "-w"]
    assert ext[-1].status == "ok"


def test_run_make_falls_back_to_gmake_when_make_is_not_gnu(tmp_path: Path, monkeypatch):
    (tmp_path / "Makefile").write_text("all:\n\t$(CC) -c ok.c -o ok.o\n")
    seen: list[list[str]] = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        if cmd == ["/usr/bin/make", "--version"]:
            return _FakeProc(0, stdout="BSD make\n")
        if cmd == ["/opt/bin/gmake", "--version"]:
            return _FakeProc(0, stdout="GNU Make 4.4\n")
        assert cmd == ["/opt/bin/gmake", "-B", "-n", "-k", "-w"]
        return _FakeProc(
            0,
            stdout="gmake[1]: Entering directory '/x'\ncc -c ok.c -o ok.o\n",
        )

    monkeypatch.setattr(_bq.deadline, "run_bounded", fake_run)
    merged, ext = BuildEvidence(), []
    assert (
        run_inferred_build_query(
            tmp_path,
            merged,
            ext,
            which=lambda tool: {
                "make": "/usr/bin/make",
                "gmake": "/opt/bin/gmake",
            }.get(tool),
        )
        is None
    )
    assert seen[:3] == [
        ["/usr/bin/make", "--version"],
        ["/opt/bin/gmake", "--version"],
        ["/opt/bin/gmake", "-B", "-n", "-k", "-w"],
    ]
    assert ext[-1].status == "ok"


def test_run_make_uses_gnumake_when_make_and_gmake_are_absent(
    tmp_path: Path, monkeypatch
):
    (tmp_path / "Makefile").write_text("all:\n\t$(CC) -c ok.c -o ok.o\n")
    seen: list[list[str]] = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        if cmd == ["/usr/local/bin/gnumake", "--version"]:
            return _FakeProc(0, stdout="GNU Make 4.4\n")
        assert cmd == ["/usr/local/bin/gnumake", "-B", "-n", "-k", "-w"]
        return _FakeProc(0, stdout="cc -c ok.c -o ok.o\n")

    monkeypatch.setattr(_bq.deadline, "run_bounded", fake_run)
    merged, ext = BuildEvidence(), []
    assert (
        run_inferred_build_query(
            tmp_path,
            merged,
            ext,
            which=lambda tool: "/usr/local/bin/gnumake" if tool == "gnumake" else None,
        )
        is None
    )
    assert seen == [
        ["/usr/local/bin/gnumake", "--version"],
        ["/usr/local/bin/gnumake", "-B", "-n", "-k", "-w"],
    ]
    assert ext[-1].status == "ok"


def test_run_make_skips_without_gnu_make(tmp_path: Path, monkeypatch):
    (tmp_path / "Makefile").write_text("all:\n\t$(CC) -c ok.c -o ok.o\n")

    def fake_run(cmd, **kw):
        return _FakeProc(0, stdout="BSD make\n")

    monkeypatch.setattr(_bq.deadline, "run_bounded", fake_run)
    merged, ext = BuildEvidence(), []
    assert (
        run_inferred_build_query(
            tmp_path,
            merged,
            ext,
            which=lambda tool: "/usr/bin/make" if tool == "make" else None,
        )
        is None
    )
    assert ext[-1].status == "skipped"
    assert "no GNU Make launcher" in ext[-1].detail
    assert not merged.compile_units


def test_bazelisk_fallback_when_bazel_absent(tmp_path: Path, monkeypatch):
    # Mirror BazelAdapter: when `bazel` isn't on PATH but `bazelisk` is, the
    # inferred query swaps the launcher rather than skipping (Codex/CR).
    (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")
    ran: dict = {}

    def fake_run(cmd, **kw):
        ran["cmd"] = cmd
        return _FakeProc(0, stdout='{"actions": []}')

    monkeypatch.setattr(_bq.deadline, "run_bounded", fake_run)
    merged, ext = BuildEvidence(), []
    out = run_inferred_build_query(
        tmp_path,
        merged,
        ext,
        which=lambda tool: None if tool == "bazel" else "/usr/bin/bazelisk",
    )
    assert out is None  # bazel path merges evidence, returns no DB path
    assert ran["cmd"][0] == "bazelisk"  # launcher swapped from bazel
    assert ext[-1].name == "build_query_auto"


def test_bazel_command_includes_param_files(tmp_path: Path):
    cmd = inferred_query_command("bazel", tmp_path)
    assert cmd is not None
    assert "--include_param_files" in cmd  # expands @...params (Codex review)


def test_bazel_command_queries_compile_and_link_mnemonics(tmp_path: Path):
    # The inferred aquery must cover link/archive actions too, not just compile —
    # else link_units lack version_script/soname and LINK_EXPORT_POLICY_CHANGED
    # can't fire on the inferred Bazel path (review). Derived from the adapter's
    # own mnemonic sets so the query and the ingester cannot drift.
    from abicheck.buildsource.adapters.bazel import (
        _COMPILE_MNEMONICS,
        _LINK_MNEMONICS,
    )

    cmd = inferred_query_command("bazel", tmp_path)
    assert cmd is not None
    expr = cmd[-1]
    for mnem in _COMPILE_MNEMONICS | _LINK_MNEMONICS:
        assert mnem in expr, f"{mnem} missing from inferred aquery expression"
    assert "CppLink" in expr and "CppCompile" in expr  # explicit sanity


def test_inferred_query_diag_yields_partial_l3_coverage():
    # A build_query_auto skipped/failed diagnostic must produce a partial L3 row,
    # not a silent not_collected, so the user learns why source scanning got no L3.
    from abicheck.buildsource.inline import build_inline_coverage
    from abicheck.buildsource.model import CoverageStatus, ExtractorRecord

    rec = ExtractorRecord(
        name="build_query_auto", status="skipped", detail="cmake not installed"
    )
    rows = build_inline_coverage(BuildEvidence(), False, None, None, [rec])
    l3 = next(r for r in rows if r.layer == "L3_build")
    assert l3.status == CoverageStatus.PARTIAL
    assert "cmake not installed" in (l3.detail or "")


def test_run_bazel_empty_action_graph_is_partial(tmp_path: Path, monkeypatch):
    (tmp_path / "MODULE.bazel").write_text("module(name='x')\n")
    monkeypatch.setattr(
        _bq.deadline,
        "run_bounded",
        lambda cmd, **kw: _FakeProc(0, stdout='{"actions":[]}'),
    )
    merged, ext = BuildEvidence(), []
    # Stub `which` so the test is hermetic: without it, a host that lacks a
    # real `bazel` on PATH short-circuits to status "skipped" before the
    # (mocked) query ever runs, and the test flips green/red by environment.
    assert (
        run_inferred_build_query(
            tmp_path, merged, ext, which=lambda tool: f"/usr/bin/{tool}"
        )
        is None
    )
    assert ext[-1].name == "build_query_auto"
    assert ext[-1].status == "partial"  # no CppCompile actions


def test_collect_inline_pack_defers_build_dir_cleanup(tmp_path: Path, monkeypatch):
    # Fast-lane guard for the cleanup-lifetime contract (the real end-to-end check
    # lives in tests/test_scan_levels_integration.py): when a caller passes
    # ``defer_cleanup``, collect_inline_pack must hand the inferred-build-dir cleanup
    # thunks to that list (for the scan to run after S2) rather than firing them
    # itself; without it, it cleans up immediately (e.g. ``dump --sources``).
    from abicheck.buildsource import inline as _inline

    ran = {"n": 0}

    def fake_resolve(
        build_info,
        sources,
        cfg,
        trusted,
        merged,
        extractors,
        cleanup=None,
        compile_db_explicit=False,
        allow_inferred_build_query=True,
    ):
        if cleanup is not None:
            cleanup.append(lambda: ran.__setitem__("n", ran["n"] + 1))
        return None  # no compile DB → minimal downstream work

    monkeypatch.setattr(_inline, "_resolve_compile_db", fake_resolve)

    # Deferred: the thunk lands in the caller's list and is NOT run yet.
    defer: list = []
    _inline.collect_inline_pack(
        sources=tmp_path, build_info=None, layers=("L3",), defer_cleanup=defer
    )
    assert len(defer) == 1 and ran["n"] == 0
    defer[0]()
    assert ran["n"] == 1  # caller runs it after the scan's later phases

    # Immediate: no defer list → collect_inline_pack runs the cleanup itself.
    _inline.collect_inline_pack(sources=tmp_path, build_info=None, layers=("L3",))
    assert ran["n"] == 2

    # Abort path (CodeRabbit): a thunk is registered, then a later collection step
    # raises. The handoff lives in a finally, so the thunk is still deferred to the
    # caller (never lost / never leaked) even though collect_inline_pack re-raises.
    def resolve_returns_db(
        build_info,
        sources,
        cfg,
        trusted,
        merged,
        extractors,
        cleanup=None,
        compile_db_explicit=False,
        allow_inferred_build_query=True,
    ):
        if cleanup is not None:
            cleanup.append(lambda: ran.__setitem__("n", ran["n"] + 1))
        return tmp_path / "compile_commands.json"  # non-None → _run_compile_db runs

    def boom(*a, **k):
        raise RuntimeError("L3 normalization blew up mid-collection")

    monkeypatch.setattr(_inline, "_resolve_compile_db", resolve_returns_db)
    monkeypatch.setattr(_inline, "_run_compile_db", boom)
    defer_on_abort: list = []
    with pytest.raises(RuntimeError):
        _inline.collect_inline_pack(
            sources=tmp_path,
            build_info=None,
            layers=("L3",),
            defer_cleanup=defer_on_abort,
        )
    assert len(defer_on_abort) == 1 and ran["n"] == 2  # deferred, not lost, not run
    defer_on_abort[0]()
    assert ran["n"] == 3  # caller can still drain it


def test_drain_build_dir_cleanups_is_best_effort():
    # A raising cleanup thunk must NOT abort the remaining thunks (which would leak
    # the other build dirs/locks) and must not propagate out of the drain — the
    # contract the scan/dump finally blocks rely on (review).
    from abicheck.buildsource.build_query import drain_build_dir_cleanups

    ran: list[int] = []

    def boom():
        raise OSError("flock LOCK_UN on a churned fd")

    drain_build_dir_cleanups(
        [lambda: ran.append(1), boom, lambda: ran.append(3)]
    )  # must not raise
    assert ran == [1, 3]  # the thunk after the raising one still ran
