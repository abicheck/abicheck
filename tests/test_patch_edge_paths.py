# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Failure and fallback branches of the cache-integrity, migration,
isolation and template-index changes."""

from __future__ import annotations

import os
import sys

import pytest

from abicheck.errors import SnapshotError
from abicheck.extract.header_ast_backend import lang_to_profile
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import save_snapshot
from abicheck.storage import cache_integrity as ci
from abicheck.storage.json_compact import migrate_legacy_entry

linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="isolation forks; Linux only"
)


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        (None, None),
        ("c", "c"),
        ("C++", "cpp"),
        ("cpp", "cpp"),
        ("rust", None),
        ("", None),
    ],
)
def test_lang_to_profile(lang, expected):
    assert lang_to_profile(lang) == expected


def test_record_digest_replace_failure_removes_temp_and_sidecar(tmp_path, monkeypatch):
    entry = tmp_path / "e.json"
    entry.write_text("{}")
    ci.sidecar_path(entry).write_text("0" * 64 + "\n")  # stale digest

    def refuse(*_a, **_k):
        raise OSError("replace refused")

    monkeypatch.setattr(ci.os, "replace", refuse)
    ci.record_digest(entry)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["e.json"]


def test_unreadable_entry_fails_verification(tmp_path, monkeypatch):
    entry = tmp_path / "e.json"
    entry.write_text("{}")
    ci.record_digest(entry)

    def refuse(_p):
        raise OSError("gone")

    monkeypatch.setattr(ci, "digest_file", refuse)
    assert ci.verify_entry(entry) is False


def test_migration_failure_before_temp_file_leaves_entry(tmp_path, monkeypatch):
    import abicheck.storage.json_compact as jc

    entry = tmp_path / "e.json"
    text = '{\n  "a": 1\n}'
    entry.write_text(text)

    def refuse(*_a, **_k):
        raise OSError("no space")

    monkeypatch.setattr(jc.tempfile, "mkstemp", refuse)
    assert migrate_legacy_entry(entry) is False
    assert entry.read_text() == text
    assert sorted(p.name for p in tmp_path.iterdir()) == ["e.json"]


@linux_only
def test_child_that_dies_without_reporting_is_an_error(monkeypatch):
    from abicheck.workflows.side_isolation import run_isolated

    monkeypatch.setenv("ABICHECK_EXTRACTION_ISOLATION", "process")
    with pytest.raises(SnapshotError):
        run_isolated([lambda: os._exit(3), lambda: 1], concurrent=False)


def _snap(path, version, fns):
    snap = AbiSnapshot(library="libx.so", version=version)
    snap.functions = [
        Function(name=n, mangled=n, return_type="int", visibility=Visibility.PUBLIC)
        for n in fns
    ]
    save_snapshot(snap, path)
    return path


@linux_only
def test_compare_resolves_sides_in_children_with_same_verdict(tmp_path, monkeypatch):
    from abicheck.service import CompareRequest, InputSpec, run_compare_request

    old = _snap(tmp_path / "old.json", "1", ["f", "g"])
    new = _snap(tmp_path / "new.json", "2", ["f"])
    request = CompareRequest(old=InputSpec(path=old), new=InputSpec(path=new))

    monkeypatch.delenv("ABICHECK_EXTRACTION_ISOLATION", raising=False)
    inproc = run_compare_request(request)
    monkeypatch.setenv("ABICHECK_EXTRACTION_ISOLATION", "process")
    isolated = run_compare_request(request)

    kinds = lambda r: sorted((c.kind.value, c.symbol) for c in r.diff.changes)  # noqa: E731
    assert isolated.diff.verdict == inproc.diff.verdict
    assert kinds(isolated) == kinds(inproc)
    assert ("func_removed", "g") in kinds(inproc)


@linux_only
def test_truncated_result_transfer_is_a_snapshot_error(monkeypatch):
    import multiprocessing
    import multiprocessing.connection as mpc

    from abicheck.workflows.side_isolation import run_isolated

    def truncated(_self):
        raise OSError("short read")

    monkeypatch.setenv("ABICHECK_EXTRACTION_ISOLATION", "process")
    monkeypatch.setattr(mpc.Connection, "recv", truncated)
    with pytest.raises(SnapshotError, match="transfer failed"):
        run_isolated([lambda: 1, lambda: 2], concurrent=True)
    assert multiprocessing.active_children() == []


def test_record_digest_survives_a_read_only_sidecar(tmp_path, monkeypatch):
    from pathlib import Path

    entry = tmp_path / "e.json"
    entry.write_text("{}")

    def refuse(*_a, **_k):
        raise OSError("read-only")

    monkeypatch.setattr(ci.tempfile, "mkstemp", refuse)
    monkeypatch.setattr(Path, "unlink", refuse)
    ci.record_digest(entry)  # must not raise
