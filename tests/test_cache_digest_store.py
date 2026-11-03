"""The on-disk file-digest memo never changes a digest, only its cost.

Oracle: ``hashlib.sha256`` over the file bytes, computed here, not the
module's own helper.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from abicheck.extract import cache_digest_store, cache_input_digest as cid

_OLD = 1_000_000_000  # ns since epoch: far outside the racy window


@pytest.fixture
def fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # backend_cache_dir reads LOCALAPPDATA on Windows and XDG_CACHE_HOME
    # elsewhere: point both at the same place, never the real user cache.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "xdg"))
    monkeypatch.setattr(cid, "_FILE_MEMO", {})
    monkeypatch.setattr(cid, "_DIRTY", {})
    monkeypatch.setattr(cid, "_STORE_STATE", {"loaded": False})
    return tmp_path


def _new_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cid, "_FILE_MEMO", {})
    monkeypatch.setattr(cid, "_DIRTY", {})
    monkeypatch.setattr(cid, "_STORE_STATE", {"loaded": False})


def _write(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    os.utime(path, ns=(_OLD, _OLD))


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows st_ctime is the creation time, so the store is off there "
    "(test_without_a_change_time_the_store_is_neither_read_nor_written)",
)
def test_a_new_process_reuses_stored_digests_without_reading(
    fresh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = fresh / "inc"
    root.mkdir()
    for i in range(5):
        _write(root / f"h{i}.h", f"int f{i};".encode())
    first = cid.tree_digest(root)
    _new_process(monkeypatch)
    reads: list[str] = []
    real_fdopen = os.fdopen
    monkeypatch.setattr(
        cid.os,
        "fdopen",
        lambda fd, *a, **k: reads.append("r") or real_fdopen(fd, *a, **k),
    )
    assert cid.tree_digest(root) == first
    assert reads == []


@pytest.mark.parametrize("edit", ["content", "same_size_same_mtime", "truncate"])
def test_an_edited_file_is_re_read_in_a_new_process(
    fresh: Path, monkeypatch: pytest.MonkeyPatch, edit: str
) -> None:
    f = fresh / "a.h"
    _write(f, b"int a;")
    cid.file_digest(f)
    cid.flush_digest_store()
    _new_process(monkeypatch)
    new = {
        "content": b"long b; /* x */",
        "same_size_same_mtime": b"int b;",
        "truncate": b"",
    }[edit]
    _write(f, new)  # ctime moves even when size and mtime are restored
    assert cid.file_digest(f) == hashlib.sha256(new).hexdigest()


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not json",
        b"[]",
        b'{"format": 99, "entries": {}}',
        b'{"format": 1, "entries": {"x": [1, 2]}}',
    ],
)
def test_a_corrupt_store_is_ignored(
    fresh: Path, monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    store = fresh / "xdg" / "abi_check" / "digests" / "file_digests.json"
    store.parent.mkdir(parents=True)
    store.write_bytes(payload)
    f = fresh / "a.h"
    _write(f, b"int a;")
    assert cid.file_digest(f) == hashlib.sha256(b"int a;").hexdigest()


def test_a_poisoned_entry_for_a_stale_signature_is_never_served(
    fresh: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = fresh / "a.h"
    _write(f, b"int a;")
    st = f.stat()
    stale = [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns - 1]
    cache_digest_store.save_entries({str(f): (tuple(stale), "0" * 64)})
    assert cid.file_digest(f) == hashlib.sha256(b"int a;").hexdigest()


def test_the_store_is_bounded(fresh: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_digest_store, "MAX_ENTRIES", 3)
    cache_digest_store.save_entries({f"/p{i}": ((i,), "d") for i in range(3)})
    cache_digest_store.save_entries({"/new": ((9,), "d")})
    doc = json.loads(
        (fresh / "xdg" / "abi_check" / "digests" / "file_digests.json").read_bytes()
    )
    assert set(doc["entries"]) == {"/new"}


def _windows_like_signature(st: os.stat_result) -> tuple[int, ...]:
    """A signature without a change time, as Windows' stat gives one."""
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


@pytest.mark.parametrize("trusted", [True, False])
def test_without_a_change_time_the_store_is_neither_read_nor_written(
    fresh: Path, monkeypatch: pytest.MonkeyPatch, trusted: bool
) -> None:
    """A rewrite that restores size and mtime is invisible to a signature with
    no change time. Persisting such signatures serves the old digest to the
    next process (the ``trusted`` row shows the hazard); with the store off
    the next process re-reads the bytes."""
    monkeypatch.setattr(cid, "_signature", _windows_like_signature)
    monkeypatch.setattr(cid, "_STORE_SIGNATURES_TRUSTED", trusted)
    f = fresh / "a.h"
    _write(f, b"int a;")
    cid.file_digest(f)
    cid.flush_digest_store()
    store = fresh / "xdg" / "abi_check" / "digests" / "file_digests.json"
    assert store.exists() is trusted
    if not trusted:
        # A store an earlier (trusting) run left behind must still be ignored.
        stale = (
            _windows_like_signature(f.stat()),
            hashlib.sha256(b"int a;").hexdigest(),
        )
        cache_digest_store.save_entries({str(f): stale})
    _new_process(monkeypatch)
    _write(f, b"int b;")  # same size, same mtime
    served = cid.file_digest(f)
    if trusted:
        assert served == hashlib.sha256(b"int a;").hexdigest()  # the hazard
    else:
        assert served == hashlib.sha256(b"int b;").hexdigest()
