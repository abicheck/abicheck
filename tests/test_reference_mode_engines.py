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

"""``ABICHECK_REFERENCE_MODE`` reaches the caches whose storage stays with
their owner (design-hardening plan, Phase 4): each one computes or misses in
reference mode, counts the bypass, and answers exactly what it answers with
caching on. The H5 cells cannot reach these engines (no L5 graph, no build
evidence), so their switch is stated here directly."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from abicheck.model.execution_cache import REFERENCE_MODE_ENV_VAR, cache_stats


@pytest.fixture
def ref(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv(REFERENCE_MODE_ENV_VAR, "1")
    return monkeypatch


def _bypasses(name: str) -> int:
    return cache_stats()[name]["bypasses"]


def test_spelling_caches_compute_in_reference_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from abicheck.compare import spelling_match_cache as smc

    pattern = re.compile(r"\bfoo\b")
    text = "int foo; foo bar"

    def compute():  # type: ignore[no-untyped-def]
        return pattern.finditer(text)

    cached = smc.matches_for(pattern, text, 0, len(text), compute)
    vocab_cached = smc.VOCABULARY_CACHE.get_or_compile(["foo"], lambda s: pattern)
    monkeypatch.setenv(REFERENCE_MODE_ENV_VAR, "1")
    m0 = _bypasses("abicheck.compare.spelling_match_cache.match")
    v0 = _bypasses("abicheck.compare.spelling_match_cache.vocabulary")
    calls: list[frozenset[str]] = []
    assert smc.matches_for(pattern, text, 0, len(text), compute) == cached
    assert (
        smc.VOCABULARY_CACHE.get_or_compile(
            ["foo"], lambda s: calls.append(s) or pattern
        )
        is vocab_cached
    )
    assert calls == [frozenset({"foo"})]  # compiled, not served
    assert _bypasses("abicheck.compare.spelling_match_cache.match") == m0 + 1
    assert _bypasses("abicheck.compare.spelling_match_cache.vocabulary") == v0 + 1


def test_build_evidence_cache_neither_reads_nor_writes(
    ref: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from abicheck.buildsource.build_cache import BuildEvidenceCache
    from abicheck.buildsource.build_evidence import BuildEvidence

    ref.delenv(REFERENCE_MODE_ENV_VAR)
    cache = BuildEvidenceCache(tmp_path)
    cache.put("k", BuildEvidence())
    assert cache.get("k") is not None
    ref.setenv(REFERENCE_MODE_ENV_VAR, "1")
    before = _bypasses("abicheck.buildsource.build_cache.disk")
    assert cache.get("k") is None
    cache.put("k2", BuildEvidence())
    assert not (tmp_path / "build-k2.json").exists()
    assert _bypasses("abicheck.buildsource.build_cache.disk") == before + 2


def test_source_abi_cache_is_not_wired_in_reference_mode(
    ref: pytest.MonkeyPatch,
) -> None:
    from abicheck.buildsource.build_cache import SOURCE_ABI_DISK_CACHE

    before = SOURCE_ABI_DISK_CACHE.stats.bypasses
    assert not SOURCE_ABI_DISK_CACHE.permits()
    assert SOURCE_ABI_DISK_CACHE.stats.bypasses == before + 1


def test_include_probe_pool_is_inline_in_reference_mode(
    ref: pytest.MonkeyPatch,
) -> None:
    import threading

    from abicheck.buildsource import include_graph_workers as igw

    pool = igw._shared_pool()
    assert pool is not igw._SHARED_POOL
    assert pool.granted_threads == 0
    assert pool.submit(threading.get_ident).result() == threading.get_ident()


def test_ast_cache_path_is_fresh_scratch_in_reference_mode(
    ref: pytest.MonkeyPatch,
) -> None:
    from abicheck.storage.ast_cache_location import ast_cache_entry_path

    a = ast_cache_entry_path("same-key", "clang")
    b = ast_cache_entry_path("same-key", "clang")
    assert a != b and a.name == b.name == "same-key.json"
    assert not a.exists() and a.parent.is_dir()


def test_reference_scratch_lives_as_long_as_the_scoped_call(
    ref: pytest.MonkeyPatch,
) -> None:
    from abicheck.storage.ast_cache_location import (
        ast_cache_entry_path,
        reference_scratch_scoped,
    )

    seen: list[Path] = []

    @reference_scratch_scoped
    def extraction(nested: bool) -> None:
        path = ast_cache_entry_path("k", "castxml")
        path.write_text("<ast/>")
        seen.append(path)
        if nested:
            extraction(False)  # shares the outer root
            assert all(p.exists() for p in seen[-2:])

    for _ in range(3):
        before = len(seen)
        extraction(True)
        mine = seen[before:]
        assert len({p.parents[1] for p in mine}) == 1
        assert not mine[0].parents[1].exists()


def test_reference_scratch_scope_is_inert_outside_reference_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from abicheck.storage import ast_cache_location as loc

    monkeypatch.delenv(REFERENCE_MODE_ENV_VAR, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert loc.reference_scratch_scoped(lambda: loc._SCOPE_ROOT.get())() is None


def test_ast_memo_slot_and_acquisition_scope_bypass(ref: pytest.MonkeyPatch) -> None:
    from abicheck.storage import header_ast_cache as dumper_cache

    with dumper_cache.ast_acquisition_scope():
        assert not dumper_cache.ast_acquisition_active()
        assert dumper_cache.run_ast_acquisition("clang", "k", lambda: 7) == 7
    dumper_cache.store_cached_ast("k", "clang", object())
    assert dumper_cache._ast_memo_slot.get() is None
