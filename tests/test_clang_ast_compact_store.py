"""The cold clang AST path reads, offers and caches the *compacted* document.

``_parse_clang_ast_result`` routes clang's output through
``storage.json_compact.compacted_ast``. These pin the branches a plain parse
does not reach: a derived-AST consumer taking the document instead of a tree,
the streaming pruner, and the size report both paths must agree on (the
release admission compares a cold run's size with a warm run's).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from abicheck import dumper_clang_errors
from abicheck.dumper_clang_errors import _parse_clang_ast_result
from abicheck.storage.ast_size_observer import observe_ast_sizes

_DOC = {
    "kind": "TranslationUnitDecl",
    "inner": [{"kind": "FunctionDecl", "name": "f—", "inner": []}],
}


def _proc() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["clang"], returncode=0, stdout="", stderr=""
    )


def _write_pretty(tmp_path: Path) -> Path:
    ast_path = tmp_path / "ast.json"
    ast_path.write_text(
        json.dumps(_DOC, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return ast_path


def test_cold_parse_caches_compact_ascii_and_reports_its_size(tmp_path):
    ast_path = _write_pretty(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    cached = cache / "entry.json"
    sizes: list[int] = []
    with observe_ast_sizes(sizes.append):
        root = _parse_clang_ast_result(_proc(), cached, ast_path)
    assert root["inner"][0]["name"] == "f—"
    raw = cached.read_bytes()
    assert raw.isascii() and b"\n" not in raw
    assert json.loads(raw) == _DOC
    # The reported size is the document the warm path will read.
    assert sizes == [len(raw)]
    assert sorted(p.name for p in cache.iterdir()) == ["entry.json"]


def test_derived_consumer_is_offered_the_compact_document(tmp_path, monkeypatch):
    ast_path = _write_pretty(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    cached = cache / "entry.json"
    offered: list[bytes] = []
    marker = object()

    def _offer(path, **_kw):
        offered.append(Path(path).read_bytes())
        return marker

    monkeypatch.setattr(dumper_clang_errors, "offer_derived_ast_source", _offer)
    result = _parse_clang_ast_result(_proc(), cached, ast_path)
    assert result is marker
    assert offered and offered[0].isascii() and json.loads(offered[0]) == _DOC
    # Skipping the parse still leaves a warm (compact) entry.
    assert cached.read_bytes() == offered[0]


def test_derived_consumer_path_survives_an_unwritable_cache(tmp_path, monkeypatch):
    ast_path = _write_pretty(tmp_path)
    marker = object()
    monkeypatch.setattr(
        dumper_clang_errors, "offer_derived_ast_source", lambda *_a, **_k: marker
    )

    def _boom(_self, _cached):
        raise OSError("read-only cache")

    monkeypatch.setattr(dumper_clang_errors.CompactedAst, "publish", _boom)
    assert _parse_clang_ast_result(_proc(), tmp_path / "entry.json", ast_path) is marker


def test_streaming_pruner_reads_the_compact_document(tmp_path, monkeypatch):
    ast_path = _write_pretty(tmp_path)
    seen: list[bytes] = []

    def _pruned(fh, *, header_roots):
        data = fh.read()
        seen.append(data)
        return json.loads(data), 1

    monkeypatch.setattr(dumper_clang_errors, "_streaming_prune_enabled", lambda: True)
    monkeypatch.setattr(dumper_clang_errors, "load_pruned_clang_ast", _pruned)
    root = _parse_clang_ast_result(
        _proc(), tmp_path / "entry.json", ast_path, header_roots=("/x",)
    )
    assert root["kind"] == "TranslationUnitDecl"
    assert seen and seen[0].isascii() and b"\n" not in seen[0]


def test_no_cache_write_leaves_no_temp_file(tmp_path):
    ast_path = _write_pretty(tmp_path)
    before = set(tmp_path.iterdir())
    _parse_clang_ast_result(
        _proc(), tmp_path / "entry.json", ast_path, cache_write=False
    )
    assert set(tmp_path.iterdir()) == before


@pytest.fixture(autouse=True)
def _no_ambient_prune(monkeypatch):
    monkeypatch.delenv(
        dumper_clang_errors.STREAM_PRUNE_DEPENDENCY_DECLS_ENV_VAR, raising=False
    )
