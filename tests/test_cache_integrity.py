# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""AST cache entries carry a content digest; a changed entry is never used."""

from __future__ import annotations

import json
import random

from abicheck.dumper_cache import load_cached_ast, read_cached_castxml
from abicheck.storage.cache_integrity import (
    digest_file,
    record_digest,
    sidecar_path,
    verify_entry,
)
from abicheck.storage.json_compact import CompactedAst

_DOC = {
    "kind": "TranslationUnitDecl",
    "inner": [{"name": f"n{i}", "v": i} for i in range(50)],
}


def _store(tmp_path, name="e.json"):
    """An entry published the way the clang store path publishes one."""
    src = tmp_path / "src.json"
    src.write_text(json.dumps(_DOC, separators=(",", ":")))
    cached = tmp_path / name
    CompactedAst(src, None).publish(cached)
    return cached


def test_publish_records_a_matching_digest(tmp_path):
    cached = _store(tmp_path)
    assert sidecar_path(cached).read_text().strip() == digest_file(cached)
    assert verify_entry(cached) is True
    assert load_cached_ast("k", "clang", cached, memoize=False) == _DOC


def test_any_single_byte_change_is_rejected_and_evicted(tmp_path):
    rng = random.Random(3)
    for i in range(40):
        cached = _store(tmp_path, f"e{i}.json")
        raw = bytearray(cached.read_bytes())
        pos = rng.randrange(len(raw))
        raw[pos] = (raw[pos] + rng.randrange(1, 256)) % 256
        cached.write_bytes(bytes(raw))  # a hand edit: sidecar untouched
        assert load_cached_ast("k", "clang", cached, memoize=False) is None
        assert not cached.exists() and not sidecar_path(cached).exists()


def test_legacy_entry_is_trusted_once_then_protected(tmp_path):
    cached = tmp_path / "legacy.json"
    cached.write_text(json.dumps(_DOC, indent=2))  # pre-digest, pretty
    assert verify_entry(cached) is None
    assert load_cached_ast("k", "clang", cached, memoize=False) == _DOC
    assert verify_entry(cached) is True  # migrated and recorded
    cached.write_text(json.dumps({**_DOC, "kind": "X"}, indent=2))
    # Verification runs before migration, so the pretty edit is not laundered.
    assert load_cached_ast("k", "clang", cached, memoize=False) is None


def test_unusable_sidecar_counts_as_unrecorded(tmp_path):
    cached = _store(tmp_path)
    sidecar_path(cached).write_text("not-a-digest\n")
    assert verify_entry(cached) is None
    assert load_cached_ast("k", "clang", cached, memoize=False) == _DOC
    assert verify_entry(cached) is True


def test_castxml_entry_is_protected_too(tmp_path):
    cached = tmp_path / "c.xml"
    cached.write_text(
        '<?xml version="1.0"?><CastXML><Namespace id="_1" name="::"/></CastXML>'
    )
    assert read_cached_castxml(cached) is not None
    assert verify_entry(cached) is True
    cached.write_text(
        '<?xml version="1.0"?><CastXML><Namespace id="_1" name="x"/></CastXML>'
    )
    assert read_cached_castxml(cached) is None
    assert not cached.exists() and not sidecar_path(cached).exists()


def test_record_digest_on_missing_entry_is_a_no_op(tmp_path):
    record_digest(tmp_path / "absent.json")
    assert list(tmp_path.iterdir()) == []


def test_non_hex_sidecar_is_unrecorded_not_a_mismatch(tmp_path):
    cached = _store(tmp_path)
    sidecar_path(cached).write_text("z" * 64 + "\n")
    assert verify_entry(cached) is None
    assert load_cached_ast("k", "clang", cached, memoize=False) == _DOC
    assert verify_entry(cached) is True


def test_failed_digest_write_drops_the_stale_sidecar(tmp_path, monkeypatch):
    import abicheck.storage.cache_integrity as ci

    cached = _store(tmp_path)
    cached.write_text(json.dumps({"new": 1}))  # a republished entry

    def refuse(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(ci.tempfile, "mkstemp", refuse)
    record_digest(cached)
    assert not sidecar_path(cached).exists()
    monkeypatch.undo()
    assert load_cached_ast("k", "clang", cached, memoize=False) == {"new": 1}


def test_castxml_store_records_its_digest(tmp_path, monkeypatch):
    import abicheck.dumper as dumper

    out = tmp_path / "out.xml"
    out.write_text("<CastXML/>")
    cached = tmp_path / "c.xml"
    monkeypatch.setattr(dumper, "_tool_identity", lambda _b: "id")
    dumper._write_castxml_cache(
        cached,
        out,
        castxml_bin="castxml",
        cc_bin="cc",
        frontend_identity="id",
        compiler_identity="id",
    )
    assert verify_entry(cached) is True
