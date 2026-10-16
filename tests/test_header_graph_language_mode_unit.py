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

"""The header graph follows the primary pass's language decision, without a
compiler (the live suite is ``tests/test_header_graph_language_mode.py``).

Two seams: ``clang_header_dump``'s ``resolved_lang_mode`` (promotes an
auto-detected C parse to C++, never demotes, never overrides an explicit
``lang``), and ``_attach_header_graph``'s re-parse of a prefetched result that
ran in another mode than the primary pass resolved.
"""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from typing import Any

import pytest

from abicheck import service_header_graph_attach as attach
from abicheck.extract.headers.clang.backend import clang_header_dump
from abicheck.model import AbiSnapshot
from tests._clang_runner_fakes import _as_runner, _fake_proc, _write_stdout_file

C_LOOKING_CXX = "struct Slot { int v; };\nint get(Slot& s);\n"
CXX_MARKED = "namespace ns { int f(int); }\n"


def _parse_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    header_text: str,
    **kwargs: Any,
) -> tuple[bool, list[str]]:
    """``(resolved_force_cpp, the clang command)`` for one real call with the
    clang run replaced."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(
        "abicheck.extract.headers.clang.backend._resolve_clang_system_includes",
        lambda *a, **k: (),
    )
    monkeypatch.setattr(
        "abicheck.extract.headers.clang.backend.resolve_clang_bin",
        lambda *a, **k: "clang",
    )
    header = tmp_path / "api.h"
    header.write_text(header_text, encoding="utf-8")
    seen: list[list[str]] = []

    def _run(cmd: list[str], **k: Any) -> Any:
        seen.append(list(cmd))
        _write_stdout_file(k, '{"kind": "TranslationUnitDecl", "inner": []}')
        return _fake_proc()

    _root, _kind, force_cpp = clang_header_dump(
        [header], [], memoize=False, run_ast=_as_runner(_run), **kwargs
    )
    return force_cpp, seen[0]


@pytest.mark.parametrize(
    ("header_text", "kwargs", "expected_cpp"),
    [
        # The gap: header text alone reads C; the primary pass knew better.
        (C_LOOKING_CXX, {}, False),
        (C_LOOKING_CXX, {"resolved_lang_mode": "c++"}, True),
        # Never demotes header text that already proves C++.
        (CXX_MARKED, {"resolved_lang_mode": "c"}, True),
        (CXX_MARKED, {}, True),
        # An explicit request wins over an earlier pass's decision.
        (C_LOOKING_CXX, {"lang": "c", "resolved_lang_mode": "c++"}, False),
        # An unknown or mixed decision changes nothing.
        (C_LOOKING_CXX, {"resolved_lang_mode": "heterogeneous"}, False),
    ],
    ids=["text-only", "promoted", "no-demotion", "marked", "explicit-c", "mixed"],
)
def test_resolved_lang_mode_promotes_an_auto_detected_c_parse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    header_text: str,
    kwargs: dict[str, str],
    expected_cpp: bool,
) -> None:
    force_cpp, cmd = _parse_mode(monkeypatch, tmp_path, header_text, **kwargs)
    assert force_cpp is expected_cpp
    # The command clang actually ran agrees with the reported mode.
    assert cmd[-1].endswith(".hpp" if expected_cpp else ".h")


def _acquired(resolved_force_cpp: bool | None, tag: str) -> attach.HeaderGraphAst:
    return attach.HeaderGraphAst(None, f"{tag} failed", [], [], (), resolved_force_cpp)


def _attach_with(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    primary_mode: str | dict[str, str] | None,
    prefetched_cpp: bool | None,
    lang: str | None = None,
) -> list[str | None]:
    """Run the attach over a prefetched result; the modes it re-acquired with."""
    calls: list[str | None] = []

    def _acquire(headers, includes, lang_, compile_, resolved_lang_mode=None):  # noqa: ANN001
        calls.append(resolved_lang_mode)
        return _acquired(True, "re-acquired")

    monkeypatch.setattr(attach, "acquire_header_graph_ast", _acquire)
    header = tmp_path / "api.h"
    header.write_text(C_LOOKING_CXX, encoding="utf-8")
    future: Future[attach.HeaderGraphAst] = Future()
    future.set_result(_acquired(prefetched_cpp, "prefetched"))
    snap = AbiSnapshot(library="l", version="1")
    if isinstance(primary_mode, dict):
        snap.ast_toolchain = primary_mode
    elif primary_mode is not None:
        snap.ast_toolchain = {"resolved_lang_mode": primary_mode}
    attach._attach_header_graph(
        snap,
        header_graph=True,
        header_graph_includes=False,
        headers=[header],
        includes=[],
        lang=lang,
        compile=None,
        public_headers=None,
        public_header_dirs=None,
        prefetched=future,
    )
    return calls


@pytest.mark.parametrize(
    ("primary_mode", "prefetched_cpp", "lang", "reparsed_with"),
    [
        # The gap: a C (or failed) prefetch under a C++ primary decision.
        ("c++", False, None, ["c++"]),
        ("c++", None, None, ["c++"]),
        # Already parsed as C++: the prefetch is used as is.
        ("c++", True, None, []),
        # The primary pass also chose C: nothing to correct.
        ("c", False, None, []),
        (None, False, None, []),
        # An explicit --lang c is the caller's decision, not overridden.
        ("c++", False, "c", []),
    ],
    ids=[
        "c-prefetch",
        "failed-prefetch",
        "cxx-prefetch",
        "primary-c",
        "unrecorded",
        "explicit-c",
    ],
)
def test_a_prefetch_in_the_wrong_mode_is_reparsed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    primary_mode: str | None,
    prefetched_cpp: bool | None,
    lang: str | None,
    reparsed_with: list[str | None],
) -> None:
    calls = _attach_with(
        monkeypatch,
        tmp_path,
        primary_mode=primary_mode,
        prefetched_cpp=prefetched_cpp,
        lang=lang,
    )
    assert calls == reparsed_with


def _hybrid(castxml: str | None, clang: str | None) -> dict[str, str]:
    """A hybrid merge's toolchain: each leg's keys prefixed, no bare key."""
    legs = {"castxml": castxml, "clang": clang}
    return {f"{leg}_resolved_lang_mode": m for leg, m in legs.items() if m}


@pytest.mark.parametrize(
    ("toolchain", "reparsed_with"),
    [
        # Both legs resolved C++: the merge's C prefetch is re-parsed as C++.
        (_hybrid("c++", "c++"), ["c++"]),
        # One leg recorded a mode: that is the decision.
        (_hybrid("c++", None), ["c++"]),
        (_hybrid("c", "c"), []),
        # Legs disagree: no decision to follow, the prefetch stands.
        (_hybrid("c++", "c"), []),
    ],
    ids=["agree-cxx", "one-leg", "agree-c", "disagree"],
)
def test_a_hybrid_merge_follows_its_legs_decision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    toolchain: dict[str, str],
    reparsed_with: list[str | None],
) -> None:
    calls = _attach_with(
        monkeypatch, tmp_path, primary_mode=toolchain, prefetched_cpp=False
    )
    assert calls == reparsed_with


@pytest.mark.parametrize(
    ("toolchain", "expected"),
    [
        (None, None),
        ({}, None),
        ({"resolved_lang_mode": "c"}, "c"),
        ({"resolved_lang_mode": "heterogeneous"}, "heterogeneous"),
        # The bare key is the snapshot's own decision; legs never override it.
        ({"resolved_lang_mode": "c", **_hybrid("c++", "c++")}, "c"),
        (_hybrid("c++", "c++"), "c++"),
        (_hybrid(None, "c"), "c"),
        (_hybrid("c++", "c"), None),
    ],
)
def test_recorded_lang_mode_reads_a_plain_or_hybrid_toolchain(
    toolchain: dict[str, str] | None, expected: str | None
) -> None:
    from abicheck.model.header_lang_mode import recorded_lang_mode

    assert recorded_lang_mode(toolchain) == expected


def test_no_prefetch_acquires_once_with_the_primary_decision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str | None] = []

    def _acquire(headers, includes, lang_, compile_, resolved_lang_mode=None):  # noqa: ANN001
        calls.append(resolved_lang_mode)
        return _acquired(True, "acquired")

    monkeypatch.setattr(attach, "acquire_header_graph_ast", _acquire)
    header = tmp_path / "api.h"
    header.write_text(C_LOOKING_CXX, encoding="utf-8")
    snap = AbiSnapshot(library="l", version="1")
    snap.ast_toolchain = {"resolved_lang_mode": "c++"}
    attach._attach_header_graph(
        snap, header_graph=True, header_graph_includes=False, headers=[header],
        includes=[], lang=None, compile=None, public_headers=None,
        public_header_dirs=None,
    )  # fmt: skip
    assert calls == ["c++"]


@pytest.mark.parametrize(
    ("header_text", "resolved_lang_mode", "attempted_cpp"),
    [
        (C_LOOKING_CXX, None, False),
        (C_LOOKING_CXX, "c++", True),
        (CXX_MARKED, None, True),
    ],
    ids=["text-only", "promoted", "marked"],
)
def test_a_failed_parse_records_the_mode_it_attempted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    header_text: str,
    resolved_lang_mode: str | None,
    attempted_cpp: bool,
) -> None:
    """So the attach re-parses a failed prefetch only when it tried C: a
    header set that fails as C++ too is not parsed a second time."""
    from abicheck.errors import SnapshotError
    from abicheck.extract.headers.clang import backend

    def _fail(*_a: Any, **_k: Any) -> Any:
        raise SnapshotError("clang failed to parse the header(s)")

    monkeypatch.setattr(backend, "clang_header_dump", _fail)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    header = tmp_path / "api.h"
    header.write_text(header_text, encoding="utf-8")
    acquired = attach.acquire_header_graph_ast(
        [header], [], None, None, resolved_lang_mode=resolved_lang_mode
    )
    assert acquired.ast_failure
    assert acquired.resolved_force_cpp is attempted_cpp


def test_a_memoized_c_failure_is_not_replayed_for_a_cxx_decision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Libraries of one request share a failed header-graph parse's outcome
    (the bisection memo). A sibling whose primary pass resolved C++ runs a
    different parse of the same headers, so it must not inherit the C one's
    failure."""
    from abicheck.buildsource.header_graph_ast_projection import (
        header_graph_bisection_scope,
    )
    from abicheck.errors import SnapshotError
    from abicheck.extract.headers.clang import backend

    headers = []
    for i in range(3):
        header = tmp_path / f"h{i}.h"
        header.write_text(f"struct S{i} {{ int v; }};\nint get{i}(S{i}& s);\n")
        headers.append(header)
    modes: list[str | None] = []

    def _dump(group: list[Path], *_a: Any, **k: Any) -> Any:
        modes.append(k.get("resolved_lang_mode"))
        if k.get("resolved_lang_mode") != "c++":  # these headers are C++ only
            raise SnapshotError("clang failed to parse the header(s)")
        return {"kind": "TranslationUnitDecl", "inner": []}, "c++", True

    monkeypatch.setattr(backend, "clang_header_dump", _dump)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    with header_graph_bisection_scope():
        as_c = attach.acquire_header_graph_ast(headers, [], None, None)
        parses_as_c = len(modes)
        as_cxx = attach.acquire_header_graph_ast(
            headers, [], None, None, resolved_lang_mode="c++"
        )
    assert as_c.ast_failure and parses_as_c > 1  # failed, then bisected
    assert as_cxx.ast_failure is None and as_cxx.projection is not None
    assert modes[parses_as_c:] == ["c++"]  # one whole-tree C++ parse
