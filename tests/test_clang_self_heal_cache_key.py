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

"""``dumper._clang_header_dump``'s C-to-C++ self-heal cache-write key
(Codex review, fresh evidence).

Split out of ``test_dumper_clang.py`` (already at its ADR-061 file-size debt
cap, see ``architecture/debt.yaml``) rather than added there. Its sibling
self-heal tests (``test_clang_header_dump_retries_cpp_on_missing_cpp_stdlib_header``
and others) mock ``_cache_path`` to a constant path, which cannot exercise
the write/lookup key divergence this fix closes -- every write lands at the
same mocked path regardless of which key produced it. This module instead
redirects ``XDG_CACHE_HOME`` to a temp dir and leaves the real
``_cache_key``/``_cache_path`` in place, so a genuinely mismatched
write-vs-lookup key shows up as a real behavioral difference (a second call
either wrongly cache-hits stale content, or correctly misses and re-heals).

Background: a self-healed dump's cache LOOKUP key is computed from the
pre-retry (C-mode) inputs, before clang even runs -- self-heal cannot be
known in advance, since it only fires after a real "missing C++ stdlib
header" compile failure. `_clang_header_dump`'s own docstring documents why
the lookup key deliberately cannot distinguish a genuine C-mode success from
an initially-C-mode call that self-healed into C++. Writing the cache entry
under that same stale key, as this code once did, meant a later IDENTICAL
call would cache-HIT it and report `resolved_force_cpp=False` (the pre-retry
guess) alongside genuinely self-healed C++ content -- exactly what a
downstream consumer of that bit (e.g. `dumper_clang._ClangAstParser`'s
`is_cxx` gate on the asm-label extern-C exclusion) must never see. The fix:
write under a key/path recomputed from the mode that ACTUALLY produced the
result, so a later identical call simply misses the now-unused stale key and
re-runs the self-heal fresh -- always correct, at the cost of caching's
benefit for this one narrow input shape rather than a wrong answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck import dumper, dumper_ast_config, dumper_cache, dumper_clang
from abicheck.dumper import _clang_header_dump
from abicheck.dumper_ast_config import _cache_key


def _fake_proc(stdout: str = "", stderr: str = "", returncode: int = 0):
    class _P:
        pass

    p = _P()
    p.stdout = stdout
    p.stderr = stderr
    p.returncode = returncode
    return p


def _write_stdout_file(kwargs: dict, text: str) -> None:
    fobj = kwargs.get("stdout")
    if fobj is not None:
        fobj.write(text.encode("utf-8"))


def test_self_healed_dump_never_returns_a_stale_cache_hit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A pure-#include umbrella header self-heals from C to C++ on both of
    two IDENTICAL calls -- neither ever serves the other's cache entry with
    the wrong `resolved_force_cpp` bit, which would show up here as either
    call reporting `False` or as only 2 total commands (a false cache hit
    skipping the second call's own self-heal) instead of 4."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_detect_cpp_headers", lambda *a, **k: False)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")
    header = tmp_path / "umbrella.h"
    header.write_text('#include "detail/impl.h"\n')
    ast_json = '{"kind": "TranslationUnitDecl", "inner": []}'
    cmds: list[list[str]] = []

    def _run(cmd, **kwargs):
        cmds.append(list(cmd))
        if cmds[-1][cmds[-1].index("-x") + 1] == "c":
            return _fake_proc(
                stderr="fatal error: 'cstddef' file not found", returncode=1
            )
        _write_stdout_file(kwargs, ast_json)
        return _fake_proc(returncode=0)

    monkeypatch.setattr(dumper.deadline, "run_bounded", _run)
    for _ in range(2):
        root, _resolved_kind, resolved_force_cpp = _clang_header_dump([header], [])
        assert root == {"kind": "TranslationUnitDecl", "inner": []}
        assert resolved_force_cpp is True
    assert len(cmds) == 4  # one C attempt + one C++ retry, TWICE independently


def test_self_heal_preserves_the_memo_handoff_under_the_lookup_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review, fresh evidence, P1: the in-process memo (``dumper_cache.
    store_cached_ast``) is a one-shot, same-thread HANDOFF to
    ``service._attach_header_graph``'s own follow-up call, which
    independently recomputes the identical PRE-retry lookup key from the
    same original inputs -- it has no way to know a self-heal happened.
    Storing the memo entry under the corrected post-retry key (matching the
    disk-cache write fixed above) would make that follow-up lookup MISS,
    repeating both clang attempts and leaking the handed-off AST in this
    thread's slot forever (its own call uses ``memoize=False``, so nothing
    would ever pop it). The memo must stay keyed by the pre-retry `key` --
    safe, since that consumer discards `resolved_force_cpp` entirely."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(dumper_clang, "_clang_available", lambda *a, **k: True)
    monkeypatch.setattr(dumper, "_detect_cpp_headers", lambda *a, **k: False)
    monkeypatch.setenv("ABICHECK_AUTO_SYSTEM_INCLUDES", "0")
    header = tmp_path / "umbrella.h"
    header.write_text('#include "detail/impl.h"\n')
    ast_json = '{"kind": "TranslationUnitDecl", "inner": []}'

    def _run(cmd, **kwargs):
        if cmd[cmd.index("-x") + 1] == "c":
            return _fake_proc(
                stderr="fatal error: 'cstddef' file not found", returncode=1
            )
        _write_stdout_file(kwargs, ast_json)
        return _fake_proc(returncode=0)

    calls = {"n": 0}
    real_run = _run

    def _counted_run(cmd, **kwargs):
        calls["n"] += 1
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(dumper.deadline, "run_bounded", _counted_run)
    with dumper_cache.ast_memoize_scope():
        # The primary snapshot pass (`service_dump_native.py` wraps both this
        # call and the follow-up below in one `ast_memoize_scope()`).
        root, _resolved_kind, resolved_force_cpp = _clang_header_dump(
            [header], [], memoize=True
        )
        assert resolved_force_cpp is True
        assert calls["n"] == 2  # one C attempt + one C++ retry
        # Mirrors `service._attach_header_graph`'s own follow-up: identical
        # inputs, its own fresh (pre-retry) `key` computation, memoize=False.
        # A working handoff pops the memo slot without running clang again;
        # a broken one (the memo stored under the corrected post-retry key
        # instead) would miss and redo the whole two-step self-heal here.
        graph_root, _rk2, _rfc2 = _clang_header_dump([header], [], memoize=False)
    assert graph_root == root
    assert calls["n"] == 2  # unchanged: the follow-up call ran no subprocess


def test_clang_cache_schema_version_actually_changes_the_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Codex review, fresh evidence, P2: without a schema bump, a pre-
    existing on-disk entry an OLDER binary wrote for a self-healed dump
    (stored under the pre-retry key, which this hash still computes for the
    identical input) would stay silently reachable, reintroducing the exact
    stale `resolved_force_cpp=False` bug the write-side key fix closes only
    for entries written from here on. Monkeypatching the version constant
    down by one simulates exactly that "older binary" -- if the key were
    unaffected by it (e.g. the line were accidentally deleted or the
    constant stopped being read), this would fail, which is the real
    property this fix depends on: bumping the constant is what invalidates
    every previously-written clang entry. Verified independent of `backend`
    ever mattering for castxml, which never had this problem and must stay
    unaffected by a clang-only constant."""
    header = tmp_path / "h.h"
    header.write_text("void f(void);\n")
    kwargs = dict(headers=[header], extra_includes=[], compiler="c++", force_cpp=False)
    clang_key_v2 = _cache_key(**kwargs, backend="clang")
    castxml_key = _cache_key(**kwargs, backend="castxml")
    monkeypatch.setattr(dumper_ast_config, "_CLANG_CACHE_SCHEMA_VERSION", 1)
    assert _cache_key(**kwargs, backend="clang") != clang_key_v2
    assert _cache_key(**kwargs, backend="castxml") == castxml_key
