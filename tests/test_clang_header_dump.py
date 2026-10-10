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

"""Tests of the clang ``-ast-dump=json`` run itself:
``clang_header_dump`` driven through an injected ``run_ast=`` runner (AST
cache and memo, deadline rechecks, no-output/bad-JSON/nonzero-exit/timeout
failures, the C->C++ self-heal retry, and DPC++ host/device frontend
context selection), plus two live-clang checks on its real output. The
parser over the resulting JSON is covered in ``test_dumper_clang.py``.
"""

from __future__ import annotations

import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from abicheck import deadline, dumper, dumper_ast_config, dumper_clang
from abicheck.dumper_clang import _ClangAstParser
from abicheck.errors import SnapshotError
from abicheck.extract.headers.clang import backend as clang_backend
from abicheck.extract.headers.clang.backend import clang_header_dump
from abicheck.storage import header_ast_cache as dumper_cache
from tests._clang_runner_fakes import _as_runner, _fake_proc, _write_stdout_file

# The live-clang tests below are Linux/ELF-scoped, matching
# ``test_clang_header_backend_integration.py``'s module ``pytestmark``.
_LINUX_ONLY = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="live clang L2 backend check is ELF/Linux-scoped",
)


def _stub_clang_self_heal(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Make clang_header_dump fail once on a missing <cstddef> then succeed.

    Mocks the clang availability/system-include probe and subprocess so the
    C→C++ self-heal branch runs without a real compiler: the first parse exits
    nonzero with a missing C++ stdlib header, the C++ retry returns a minimal AST.
    """
    import subprocess as _sp

    monkeypatch.setattr("abicheck.dumper_clang._clang_available", lambda *a, **k: True)
    monkeypatch.setattr(
        "abicheck.extract.headers.clang.backend._resolve_clang_system_includes",
        lambda *a, **k: (),
    )
    fail = _sp.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="fatal error: 'cstddef' file not found"
    )
    ok = _sp.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    calls = {"n": 0}

    def _run(*a: object, **k: object) -> _sp.CompletedProcess[str]:
        calls["n"] += 1
        if calls["n"] == 1:
            return fail
        _write_stdout_file(k, '{"kind": "TranslationUnitDecl", "inner": []}')
        return ok

    return _as_runner(_run)


def test_clang_self_heal_explicit_c_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog
) -> None:
    # Explicit --lang c that self-heals to C++ overrides the user's request, so
    # it stays a visible warning (Codex review).
    import logging

    runner = _stub_clang_self_heal(monkeypatch)
    header = tmp_path / "umbrella.h"
    header.write_text("int foo(void);\n")
    with caplog.at_level(logging.DEBUG, logger="abicheck.dumper"):
        root, _resolved_kind, _ = clang_header_dump(
            [header], [], lang="c", run_ast=runner
        )
    assert root["kind"] == "TranslationUnitDecl"
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("asked for C" in r.message for r in warns), [r.message for r in warns]


def test_clang_self_heal_auto_detected_is_debug(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog
) -> None:
    # Auto-detected C (lang=None, no inline C++ syntax) self-healing to C++ is
    # just noise → demoted to debug, no warning (the P6 fix this guards).
    import logging

    runner = _stub_clang_self_heal(monkeypatch)
    header = tmp_path / "umbrella.h"
    header.write_text("int foo(void);\n")
    with caplog.at_level(logging.DEBUG, logger="abicheck.dumper"):
        root, _resolved_kind, _ = clang_header_dump(
            [header], [], run_ast=runner
        )  # lang=None → auto-detect
    assert root["kind"] == "TranslationUnitDecl"
    assert not any(r.levelno == logging.WARNING for r in caplog.records)
    assert any(
        r.levelno == logging.DEBUG and "self-healed to C++" in r.message
        for r in caplog.records
    )


def test_clang_header_dump_success_and_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "cache.json"
    ast_json = '{"kind": "TranslationUnitDecl", "inner": []}'

    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(clang_backend, "_cache_path", lambda *a, **k: cache)
    # Isolate the single clang AST-dump call: disable the castxml↔clang
    # system-include probe (itself a separate, best-effort subprocess).
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")
    calls = {"n": 0}

    def _run(cmd, **kwargs):
        calls["n"] += 1
        _write_stdout_file(kwargs, ast_json)
        return _fake_proc()

    runner = _as_runner(_run)

    root, resolved_kind, _ = clang_header_dump([header], [], run_ast=runner)
    assert root == {"kind": "TranslationUnitDecl", "inner": []}
    assert resolved_kind is None
    assert cache.exists()  # result was cached
    # Second call hits the cache — subprocess is not invoked again.
    root2, resolved_kind2, _ = clang_header_dump([header], [], run_ast=runner)
    assert root2 == root
    assert resolved_kind2 is None
    assert calls["n"] == 1


def test_clang_header_dump_skips_memo_write_on_toolchain_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CodeRabbit review: a compiler-identity change mid-parse already skips
    the on-disk cache write (``cache_write=identities_stable``) -- the
    in-process memo write must honor the same safeguard, or a result
    produced by the replacement toolchain could be served back under the
    original tool's cache key on a later same-process call."""
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(clang_backend, "_cache_path", lambda *a, **k: cache)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")
    # First call (frontend_identity) returns "v1"; every later call (the
    # post-parse identities_stable check, plus this same probe on the second
    # clang_header_dump call below) returns "v2" -- simulating the
    # toolchain changing under us mid-execution.
    identities = iter(["v1"])
    monkeypatch.setattr(
        clang_backend, "_tool_identity", lambda *a, **k: next(identities, "v2")
    )
    calls = {"n": 0}

    def _run(cmd: list[str], **kwargs: Any) -> Any:
        calls["n"] += 1
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    runner = _as_runner(_run)

    clang_header_dump([header], [], run_ast=runner)
    assert not cache.exists()  # disk write skipped, as before this PR

    # A second call must NOT hit the memo either -- it should re-invoke the
    # subprocess rather than serve the unstable-toolchain result back.
    clang_header_dump([header], [], run_ast=runner)
    assert calls["n"] == 2


def test_clang_header_dump_memoize_false_never_writes_the_memo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review: ``_attach_header_graph`` is the *final* consumer of its
    own ``clang_header_dump`` call when the primary snapshot pass used
    castxml (never wrote a memo entry of its own) -- passing
    ``memoize=False`` there must mean neither a disk-cache hit nor a fresh
    parse populates the in-process memo, since no further same-process
    reader will ever pop it. Otherwise a long-lived process (the MCP
    server, ``scan`` over many libraries) accumulates dead, potentially
    multi-GB entries with no consumer."""
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(clang_backend, "_cache_path", lambda *a, **k: cache)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")

    def _run(cmd: list[str], **kwargs: Any) -> Any:
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    runner = _as_runner(_run)

    # Fresh-parse path: memoize=False must leave this thread's slot unset.
    clang_header_dump([header], [], memoize=False, run_ast=runner)
    assert cache.exists()  # the disk cache is still populated, as always
    assert dumper_cache._ast_memo_slot.get() is None

    # Disk-cache-hit path: a second memoize=False call reads the file this
    # test just warmed on disk, and must still leave the slot unset.
    clang_header_dump([header], [], memoize=False, run_ast=runner)
    assert dumper_cache._ast_memo_slot.get() is None


def test_clang_header_dump_direct_caller_never_memoizes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review: a direct ``clang_header_dump`` caller with no
    ``service.run_dump``-style downstream ``_attach_header_graph`` consumer
    (``appcompat.check_app_compatibility``, a direct Python-API/MCP caller
    selecting the clang backend) must not populate the in-process memo at
    all -- outside ``dumper_cache.ast_memoize_scope()``, ``memoize=None``
    (the default every such caller uses) resolves to ``False``, so the
    entry is dead weight nothing will ever pop."""
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(clang_backend, "_cache_path", lambda *a, **k: cache)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")

    def _run(cmd: list[str], **kwargs: Any) -> Any:
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    runner = _as_runner(_run)

    assert not dumper_cache.ast_memoize_active()
    clang_header_dump([header], [], run_ast=runner)  # no ast_memoize_scope() active
    assert cache.exists()  # the disk cache is still populated, as always
    assert dumper_cache._ast_memo_slot.get() is None


def test_clang_header_dump_memoizes_inside_ast_memoize_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other half: a caller inside ``service.run_dump``'s
    ``ast_memoize_scope()`` (its primary-dump call) does get the memo
    write, exactly the handoff ``_attach_header_graph`` then consumes."""
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(clang_backend, "_cache_path", lambda *a, **k: cache)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")

    def _run(cmd: list[str], **kwargs: Any) -> Any:
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    runner = _as_runner(_run)

    with dumper_cache.ast_memoize_scope():
        clang_header_dump([header], [], run_ast=runner)
        assert dumper_cache._ast_memo_slot.get() is not None  # written
    # The scope exiting doesn't itself clear the slot -- only a subsequent
    # pop (the header-graph attach step) does; still present right after.
    assert dumper_cache._ast_memo_slot.get() is not None


def test_ast_memoize_scope_cleans_up_its_own_writes_on_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review: a primary dump that successfully parses/memoizes an AST
    but then fails *later* in the same scoped call (e.g. snapshot
    construction raises after the AST parse succeeded) never reaches
    _attach_header_graph to pop that entry -- ast_memoize_scope must clear
    this thread's slot when the scoped operation raises, or it sits set
    for however long this thread lives afterward in a long-lived process."""
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(clang_backend, "_cache_path", lambda *a, **k: cache)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")

    def _run(cmd: list[str], **kwargs: Any) -> Any:
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    runner = _as_runner(_run)

    with pytest.raises(RuntimeError, match="snapshot construction failed"):
        with dumper_cache.ast_memoize_scope():
            clang_header_dump([header], [], run_ast=runner)
            assert dumper_cache._ast_memo_slot.get() is not None  # written
            raise RuntimeError("snapshot construction failed")
    assert dumper_cache._ast_memo_slot.get() is None  # cleaned up on the way out


def test_clang_only_dump_does_not_require_gxx_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(clang_backend, "_cache_path", lambda *a, **k: cache)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")
    # The clang header pass lives in ``abicheck.extract.headers.clang.backend``
    # (which does not import ``_resolve_compiler_binary``); guard both the
    # legacy ``dumper`` binding and the defining module so any route into a
    # g++ resolution fails the test.
    for _mod in (dumper, dumper_ast_config):
        monkeypatch.setattr(
            _mod,
            "_resolve_compiler_binary",
            lambda *_a, **_k: pytest.fail("clang-only dump resolved g++"),
        )

    def _run(cmd, **kwargs):
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    runner = _as_runner(_run)
    root, resolved_kind, _ = clang_header_dump([header], [], run_ast=runner)
    assert root == {"kind": "TranslationUnitDecl", "inner": []}
    assert resolved_kind is None


def test_clang_header_dump_rechecks_deadline_on_cache_hit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review (PR #591): a warm AST cache hit still costs real time
    reading/parsing a potentially huge cached AST — deadline.check() must
    fire on that path too, not just on the cache-miss subprocess path."""
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(clang_backend, "_cache_path", lambda *a, **k: cache)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")
    calls = {"n": 0}

    def _run(cmd, **kwargs):
        calls["n"] += 1
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    runner = _as_runner(_run)
    clang_header_dump([header], [], run_ast=runner)  # warms the cache
    assert cache.exists() and calls["n"] == 1

    with deadline.deadline_scope(-1):  # already expired
        with pytest.raises(deadline.DeadlineExceeded):
            clang_header_dump([header], [], run_ast=runner)
    assert calls["n"] == 1  # never reached the subprocess path — cache hit


def test_clang_header_dump_rechecks_deadline_after_cache_load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review (PR #591, round 3): json.loads() on a warm cache hit can
    itself consume the rest of the budget for a huge cached AST -- the
    existing pre-load deadline.check() doesn't catch that; must re-check
    again after the load before handing the root to the AST walker."""
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "cache.json"
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(clang_backend, "_cache_path", lambda *a, **k: cache)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")

    def _run(cmd, **kwargs):
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    runner = _as_runner(_run)
    clang_header_dump([header], [], run_ast=runner)  # warms the cache
    assert cache.exists()
    # Force the second call past the in-process AST memo (G31 Phase C reuse)
    # onto the on-disk read this test is actually about -- a memo hit would
    # skip json.loads (and the slow-patched cost below) entirely, which is
    # exactly the intended optimization but not what this test exercises.
    dumper_cache._ast_memo_slot.set(None)

    # The disk-cache read (json.loads) now lives in dumper_cache.py, not
    # dumper.py itself (G31 Phase C AST reuse split the read into
    # dumper_cache.load_cached_ast) -- patch the module that actually calls it.
    real_json_loads = dumper_cache.json.loads

    def _slow_loads(text: str) -> Any:
        time.sleep(0.05)
        return real_json_loads(text)

    monkeypatch.setattr(dumper_cache.json, "loads", _slow_loads)
    with deadline.deadline_scope(0.03):
        with pytest.raises(deadline.DeadlineExceeded):
            clang_header_dump([header], [], run_ast=runner)


def test_clang_header_dump_no_output_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(
        clang_backend, "_cache_path", lambda *a, **k: tmp_path / "c.json"
    )
    # Exit 0 but empty stdout → the "no AST" path (a nonzero exit is the
    # earlier branch, covered by test_clang_header_dump_nonzero_exit_raises).
    runner = _as_runner(
        lambda *a, **k: _fake_proc(stdout="", stderr="boom", returncode=0)
    )
    with pytest.raises(SnapshotError, match="no AST"):
        clang_header_dump([header], [], run_ast=runner)


def test_clang_header_dump_rechecks_deadline_before_loading_ast(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review (PR #591): a --budget that expires exactly as clang exits
    successfully must not silently let the (potentially huge) AST JSON load +
    walk run well past it. deadline.check() must fire again right after the
    subprocess returns, before json.load — not just once before it was
    spawned."""
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(
        clang_backend, "_cache_path", lambda *a, **k: tmp_path / "c.json"
    )
    # Isolate the single clang AST-dump call from the setup/prep work leading
    # up to it (same as test_clang_header_dump_success_and_cache): disable the
    # castxml↔clang system-include probe, a separate best-effort subprocess
    # whose own variable cost would otherwise eat into the tight budget below
    # before the call under test even starts.
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")

    def _run(cmd, **kwargs):
        # Simulate the budget running out while clang was still parsing: by
        # the time it exits successfully, the deadline has already passed.
        time.sleep(0.05)
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    runner = _as_runner(_run)
    with deadline.deadline_scope(0.03):
        with pytest.raises(deadline.DeadlineExceeded):
            clang_header_dump([header], [], run_ast=runner)


def test_clang_header_dump_bad_json_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(
        clang_backend, "_cache_path", lambda *a, **k: tmp_path / "c.json"
    )

    def _run(*a, **k):
        _write_stdout_file(k, "not json")
        return _fake_proc(returncode=0)

    runner = _as_runner(_run)
    with pytest.raises(SnapshotError, match="not valid JSON"):
        clang_header_dump([header], [], run_ast=runner)


def test_clang_header_dump_retries_cpp_on_missing_cpp_stdlib_header(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A pure-#include umbrella header has no inline C++ syntax, so it is parsed in
    # C mode first; the missing-<cstddef> failure must trigger one C++-mode retry
    # (with -x c++ in the rebuilt command) rather than hard-failing.
    header = tmp_path / "umbrella.h"
    header.write_text('#include "detail/impl.h"\n')
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(
        clang_backend, "_cache_path", lambda *a, **k: tmp_path / "c.json"
    )
    monkeypatch.setattr(dumper, "_detect_cpp_headers", lambda *a, **k: False)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")
    ast_json = '{"kind": "TranslationUnitDecl", "inner": []}'
    cmds: list[list[str]] = []

    def _run(cmd, **kwargs):
        cmds.append(list(cmd))
        if len(cmds) == 1:
            return _fake_proc(
                stderr="fatal error: 'cstddef' file not found", returncode=1
            )
        _write_stdout_file(kwargs, ast_json)
        return _fake_proc(returncode=0)

    runner = _as_runner(_run)
    root, _resolved_kind, resolved_force_cpp = clang_header_dump(
        [header], [], run_ast=runner
    )
    assert root == {"kind": "TranslationUnitDecl", "inner": []}
    assert len(cmds) == 2  # one C attempt + one C++ retry
    assert "c" in cmds[0] and cmds[0][cmds[0].index("-x") + 1] == "c"
    assert cmds[1][cmds[1].index("-x") + 1] == "c++"
    # Codex review, fresh evidence: the self-heal's real, post-retry language
    # mode must be reported back, not the pre-retry "c" guess -- this is what
    # the provenance probe (dumper_toolchain._ast_compile_provenance) relies
    # on instead of silently re-deriving a stale answer.
    assert resolved_force_cpp is True


def test_clang_header_dump_no_retry_on_other_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A non-"missing C++ stdlib header" failure must NOT retry — it surfaces as-is.
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(
        clang_backend, "_cache_path", lambda *a, **k: tmp_path / "c.json"
    )
    monkeypatch.setattr(dumper, "_detect_cpp_headers", lambda *a, **k: False)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")
    calls = {"n": 0}

    def _run(cmd, **kwargs):
        calls["n"] += 1
        return _fake_proc(stderr="error: undeclared identifier 'x'", returncode=1)

    runner = _as_runner(_run)
    with pytest.raises(SnapshotError, match="failed to parse"):
        clang_header_dump([header], [], run_ast=runner)
    assert calls["n"] == 1  # no retry


# ── ADR-050 D5 (G32 Phase D): SYCL/DPC++ host/device context wiring ─────────


_G32_DPCPP_DIR = Path(__file__).parent / "fixtures" / "g32" / "dpcpp"


def _dpcpp_fixture_texts() -> tuple[str, str]:
    """Real captured (stdout, stderr) pair from ``icpx -fsycl`` (Phase 0's
    fixture, see ``tests/fixtures/g32/README.md``) -- not synthesized."""
    stdout = (_G32_DPCPP_DIR / "ast_dump.json").read_text(encoding="utf-8")
    stderr = (_G32_DPCPP_DIR / "compiler_invocation.log").read_text(encoding="utf-8")
    return stdout, stderr


def test_clang_header_dump_device_context_selects_real_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end wiring: a DPC++-capable clang_bin + frontend_context="device"
    decodes the real two-document capture and selects the spir64 device AST,
    proving sycl_context.py is actually reached from clang_header_dump, not
    just exercised in isolation (Codex P1 review)."""
    header = tmp_path / "foo.h"
    header.write_text("struct Point { int x, y; };\nint add(int, int);\n")
    stdout_text, stderr_text = _dpcpp_fixture_texts()
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(
        clang_backend, "_cache_path", lambda *a, **k: tmp_path / "c.json"
    )
    monkeypatch.setattr(
        clang_backend, "_resolve_clang_bin", lambda *a, **k: "/opt/intel/icpx"
    )
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")

    def _run(cmd, **kwargs):
        _write_stdout_file(kwargs, stdout_text)
        return _fake_proc(stderr=stderr_text, returncode=0)

    runner = _as_runner(_run)
    root, resolved_kind, _ = clang_header_dump(
        [header], [], frontend_context="device", run_ast=runner
    )
    assert resolved_kind == "device"
    assert root["kind"] == "TranslationUnitDecl"


def test_clang_header_dump_host_context_selects_real_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Companion to the device-selection test above: the same DPC++-capable
    invocation with the default frontend_context="host" selects the OTHER
    (x86_64) document from the identical real capture."""
    header = tmp_path / "foo.h"
    header.write_text("struct Point { int x, y; };\nint add(int, int);\n")
    stdout_text, stderr_text = _dpcpp_fixture_texts()
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(
        clang_backend, "_cache_path", lambda *a, **k: tmp_path / "c.json"
    )
    monkeypatch.setattr(
        clang_backend, "_resolve_dpcpp_acquisition", lambda *a: (True, False)
    )
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")

    def _run(cmd, **kwargs):
        _write_stdout_file(kwargs, stdout_text)
        return _fake_proc(stderr=stderr_text, returncode=0)

    runner = _as_runner(_run)
    root, resolved_kind, _ = clang_header_dump([header], [], run_ast=runner)
    assert resolved_kind == "host"
    assert root["kind"] == "TranslationUnitDecl"


def test_clang_header_dump_dpcpp_empty_stream_raises_ast_context_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fallback-gating (ADR-050 D5 acceptance criterion): a DPC++-capable
    invocation whose decoded stream comes back empty (broken toolchain
    invocation, truncated output) must raise AstContextMissingError -- never
    silently degrade to a single-context path, which is reserved for an
    invocation that was never positively identified as DPC++-capable at all."""
    from abicheck.errors import AstContextMissingError

    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(
        clang_backend, "_cache_path", lambda *a, **k: tmp_path / "c.json"
    )
    monkeypatch.setattr(
        clang_backend, "_resolve_clang_bin", lambda *a, **k: "/opt/intel/icpx"
    )
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")

    def _run(cmd, **kwargs):
        # No document-boundary markers at all -- an empty/malformed stream,
        # not "this wasn't a multi-document invocation."
        _write_stdout_file(kwargs, "")
        return _fake_proc(stderr="", returncode=0)

    runner = _as_runner(_run)
    with pytest.raises((AstContextMissingError, SnapshotError)):
        clang_header_dump([header], [], frontend_context="device", run_ast=runner)


def test_clang_header_dump_fno_sycl_skips_multi_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review (P2): an explicit ``-fno-sycl`` in gcc_options on a
    DPC++-capable compiler must not be silently overridden by
    dpcpp_multi_context's unconditional ``-fsycl`` append -- the actual
    clang invocation must not re-enable SYCL, and the decode falls back to
    the ordinary single-document path (resolved_kind is None)."""
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(
        clang_backend, "_cache_path", lambda *a, **k: tmp_path / "c.json"
    )
    monkeypatch.setattr(
        clang_backend, "_resolve_clang_bin", lambda *a, **k: "/opt/intel/icpx"
    )
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")
    captured_cmds: list[list[str]] = []

    def _run(cmd, **kwargs):
        captured_cmds.append(cmd)
        _write_stdout_file(kwargs, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    runner = _as_runner(_run)
    root, resolved_kind, _ = clang_header_dump(
        [header], [], gcc_options="-fno-sycl", run_ast=runner
    )
    assert resolved_kind is None
    assert root["kind"] == "TranslationUnitDecl"
    assert captured_cmds and all("-fsycl" not in cmd for cmd in captured_cmds)


def test_clang_header_dump_device_context_with_fno_sycl_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Companion: requesting --frontend-context device while gcc_options
    explicitly disables SYCL is a contradictory combination that must fail
    fast with AstContextMissingError -- never silently re-enable SYCL to
    honor frontend_context, nor silently ignore -fno-sycl (Codex review,
    P2)."""
    from abicheck.errors import AstContextMissingError

    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(
        clang_backend, "_resolve_clang_bin", lambda *a, **k: "/opt/intel/icpx"
    )
    calls = {"n": 0}

    def _run(cmd, **kwargs):
        calls["n"] += 1
        return _fake_proc(returncode=0)

    runner = _as_runner(_run)
    with pytest.raises(AstContextMissingError, match="-fno-sycl"):
        clang_header_dump(
            [header],
            [],
            gcc_options="-fno-sycl",
            frontend_context="device",
            run_ast=runner,
        )
    assert calls["n"] == 0  # fails before any subprocess is invoked


def test_clang_header_dump_non_host_on_plain_frontend_raises_immediately(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A non-"host" frontend_context against a plain, non-DPC++-capable
    clang_bin fails immediately with AstContextMissingError, before any
    subprocess is invoked -- a user who explicitly requests "device" on a
    frontend that cannot produce one must get a clear failure, never the
    ordinary single-context host AST silently standing in for it."""
    from abicheck.errors import AstContextMissingError

    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(
        clang_backend, "_resolve_clang_bin", lambda *a, **k: "/usr/bin/clang++"
    )
    calls = {"n": 0}

    def _run(cmd, **kwargs):
        calls["n"] += 1
        return _fake_proc(returncode=0)

    runner = _as_runner(_run)
    with pytest.raises(AstContextMissingError):
        clang_header_dump([header], [], frontend_context="device", run_ast=runner)
    assert calls["n"] == 0  # never spent a subprocess invocation on it


def test_clang_header_dump_nonzero_exit_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A hard parse error (nonzero exit) must fail, even if clang emitted some
    # JSON — the L2 header AST must be complete to be authoritative.
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(
        clang_backend, "_cache_path", lambda *a, **k: tmp_path / "c.json"
    )

    class _P:
        stdout = '{"kind": "TranslationUnitDecl", "inner": []}'
        stderr = "error: use of undeclared identifier"
        returncode = 1

    runner = _as_runner(lambda *a, **k: _P())
    with pytest.raises(SnapshotError, match="failed to parse"):
        clang_header_dump([header], [], run_ast=runner)


def test_clang_header_dump_timeout_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess as _sp

    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(
        clang_backend, "_cache_path", lambda *a, **k: tmp_path / "c.json"
    )

    def _boom(*a, **k):
        raise _sp.TimeoutExpired(cmd="clang", timeout=120)

    runner = _as_runner(_boom)
    with pytest.raises(SnapshotError, match="timed out"):
        clang_header_dump([header], [], run_ast=runner)


def test_clang_header_dump_corrupt_cache_is_discarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    header = tmp_path / "foo.h"
    header.write_text("int foo(void);\n")
    cache = tmp_path / "c.json"
    cache.write_text("{ this is not valid json")  # corrupt prior cache entry
    ast = '{"kind": "TranslationUnitDecl", "inner": []}'

    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(clang_backend, "_cache_path", lambda *a, **k: cache)

    def _run(*a, **k):
        _write_stdout_file(k, ast)
        return _fake_proc(returncode=0)

    runner = _as_runner(_run)
    # The corrupt cache is unlinked and the fresh clang run repopulates it.
    root, _resolved_kind, _ = clang_header_dump([header], [], run_ast=runner)
    assert root == {"kind": "TranslationUnitDecl", "inner": []}


@_LINUX_ONLY
def test_clang_ast_does_not_assign_returned_callback_abi_to_factory(
    tmp_path: Path,
) -> None:
    """Use Clang's real normalized AST spelling, not a hand-written fixture."""
    if shutil.which("clang") is None or platform.machine().lower() not in {
        "x86_64",
        "amd64",
    }:
        pytest.skip("requires an x86-64 clang frontend")
    header = tmp_path / "api.h"
    header.write_text(
        "void (__attribute__((ms_abi)) *factory(void))(int);\n", encoding="utf-8"
    )

    root, _, _ = clang_header_dump([header], [], compiler="clang", lang="C")
    (factory,) = _ClangAstParser(root, {"factory"}, set()).parse_functions()

    assert factory.contract_attributes == []


@_LINUX_ONLY
def test_clang_ast_strips_lambda_location_from_instantiated_param_type(
    tmp_path: Path,
) -> None:
    """Use Clang's real ``qualType`` spelling for a lambda closure type, not a
    hand-written fixture -- confirms the fix at `dumper_clang._qualtype`
    against real Clang 18 output, not a guessed AST shape.

    A function template instantiated with a lambda argument prints that
    instantiation's own parameter `type.qualType` as ``"(lambda at
    <path>:<line>:<col>)"`` (confirmed empirically: unlike a `decltype(...)`-
    or typedef-sugared spelling, which clang keeps sugared in `qualType` and
    only desugars into this form in the separate `desugaredQualType` key, a
    template parameter substituted directly with the deduced lambda type has
    no sugar to keep). The absolute header path leaking into a parameter's
    own recorded type would make two checkouts of the identical, unchanged
    declaration disagree.
    """
    if shutil.which("clang") is None or platform.machine().lower() not in {
        "x86_64",
        "amd64",
    }:
        pytest.skip("requires an x86-64 clang frontend")
    header = tmp_path / "call_with.h"
    header.write_text(
        "template <typename F>\n"
        "inline void call_with(F f) {}\n"
        "inline void invoke() { call_with([]{}); }\n",
        encoding="utf-8",
    )

    root, _, _ = clang_header_dump(
        [header], [], compiler="clang", lang="c++", gcc_options="-std=c++20"
    )
    funcs = _ClangAstParser(root, {"invoke"}, set()).parse_functions()
    (specialization,) = [
        f for f in funcs if f.name == "call_with" and f.mangled != "call_with"
    ]
    assert str(tmp_path) not in specialization.params[0].type
    assert specialization.params[0].type.startswith("(lambda")
