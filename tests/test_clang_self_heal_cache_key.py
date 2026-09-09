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

from abicheck import dumper, dumper_clang
from abicheck.dumper import _clang_header_dump


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
