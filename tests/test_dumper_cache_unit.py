# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the best-effort AST cache-path helper (``dumper_cache``).

``_cache_path`` picks a per-backend cache directory following the platform
convention (XDG on POSIX, ``LOCALAPPDATA`` on Windows) and degrades to the
system temp dir when the preferred location cannot be created. These cover the
Windows branch (unreachable on the Linux CI host without monkeypatching
``sys.platform``) and the OSError fallback, since both are pure-Python paths.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from abicheck.dumper_cache import _atomic_copy, _atomic_write, _cache_path


def test_posix_uses_xdg_cache_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    path = _cache_path("abcd", backend="castxml")

    assert path == tmp_path / "abi_check" / "castxml" / "abcd.xml"
    assert path.parent.is_dir()  # directory was created


def test_posix_falls_back_to_home_cache_without_xdg(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    path = _cache_path("k", backend="clang")

    assert path == tmp_path / ".cache" / "abi_check" / "clang" / "k.json"


def test_windows_uses_localappdata(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    path = _cache_path("k", backend="clang")

    # Backend sub-directory + JSON extension for the clang backend.
    assert path == tmp_path / "abi_check" / "clang" / "k.json"
    assert path.parent.is_dir()


def test_windows_without_localappdata_uses_home_appdata(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    path = _cache_path("k", backend="castxml")

    assert path == tmp_path / "AppData" / "Local" / "abi_check" / "castxml" / "k.xml"


def test_unwritable_cache_dir_falls_back_to_tempdir(monkeypatch, tmp_path) -> None:
    # If the preferred cache dir cannot be created, the helper must degrade to
    # the system temp dir rather than raising — caching is best-effort.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path / "fallback"))

    real_mkdir = Path.mkdir

    def flaky_mkdir(self, *args, **kwargs):
        # Only the XDG-rooted preferred dir fails; the temp fallback succeeds.
        if "xdg" in str(self):
            raise OSError("read-only file system")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", flaky_mkdir)

    path = _cache_path("k", backend="clang")

    assert path == tmp_path / "fallback" / "abi_check" / "clang" / "k.json"
    assert path.parent.is_dir()


# ── _atomic_write() ──────────────────────────────────────────────────────────
#
# Replaces a plain shutil.copy2()/write_text() cache write: two concurrent
# extractions (e.g. old/new sides resolved in parallel, service.py) racing to
# populate the same cache key must never leave a torn/partial file behind.


def test_writes_content_and_leaves_no_temp_file(tmp_path: Path) -> None:
    target = tmp_path / "cache" / "abcd.xml"
    target.parent.mkdir(parents=True)

    _atomic_write(target, b"<xml>hello</xml>")

    assert target.read_bytes() == b"<xml>hello</xml>"
    # No leftover .tmp sibling from the mkstemp() staging file.
    assert list(target.parent.iterdir()) == [target]


def test_overwrites_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "cache" / "abcd.xml"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old content")

    _atomic_write(target, b"new content")

    assert target.read_bytes() == b"new content"


def test_replace_failure_cleans_up_temp_file_and_reraises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the final os.replace() fails (e.g. a cross-device rename), the
    staging temp file must not be left behind, and the OSError must still
    propagate to the caller (both dumper.py/dumper_clang_errors.py call sites
    catch OSError themselves and log a warning — they must actually see it)."""
    target = tmp_path / "cache" / "abcd.xml"
    target.parent.mkdir(parents=True)

    def _raise_oserror(*_args, **_kwargs):
        raise OSError("simulated cross-device rename failure")

    monkeypatch.setattr(os, "replace", _raise_oserror)

    with pytest.raises(OSError, match="simulated cross-device rename failure"):
        _atomic_write(target, b"data")

    # The staging file (a hidden `.<name>.<random>.tmp` sibling) was cleaned
    # up rather than left behind, and the real target was never touched.
    assert list(target.parent.iterdir()) == []


def test_write_failure_cleans_up_temp_file_and_reraises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same cleanup guarantee when the failure happens while writing the
    staging file itself (e.g. disk full), before os.replace() is even
    reached."""
    target = tmp_path / "cache" / "abcd.xml"
    target.parent.mkdir(parents=True)

    real_fdopen = os.fdopen

    def _flaky_fdopen(fd, *args, **kwargs):
        f = real_fdopen(fd, *args, **kwargs)

        def _boom(_data):
            raise OSError("simulated disk full")

        f.write = _boom
        return f

    monkeypatch.setattr(os, "fdopen", _flaky_fdopen)

    with pytest.raises(OSError, match="simulated disk full"):
        _atomic_write(target, b"data")

    assert list(target.parent.iterdir()) == []
    assert not target.exists()


# ── _atomic_copy() ────────────────────────────────────────────────────────────
#
# Streams src -> dst via a same-directory temp file, like _atomic_write but for
# an already-on-disk source (the L2 clang AST cache write, P0 SVS memory fix):
# same atomicity/no-torn-file guarantee, without a second full in-memory copy.


def test_atomic_copy_copies_content_and_leaves_no_temp_file(tmp_path: Path) -> None:
    src = tmp_path / "src.json"
    src.write_bytes(b'{"a": 1}')
    dst = tmp_path / "cache" / "abcd.json"
    dst.parent.mkdir(parents=True)

    _atomic_copy(src, dst)

    assert dst.read_bytes() == b'{"a": 1}'
    assert list(dst.parent.iterdir()) == [dst]


def test_atomic_copy_replace_failure_cleans_up_temp_file_and_reraises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "src.json"
    src.write_bytes(b"data")
    dst = tmp_path / "cache" / "abcd.json"
    dst.parent.mkdir(parents=True)

    def _raise_oserror(*_args, **_kwargs):
        raise OSError("simulated cross-device rename failure")

    monkeypatch.setattr(os, "replace", _raise_oserror)

    with pytest.raises(OSError, match="simulated cross-device rename failure"):
        _atomic_copy(src, dst)

    assert list(dst.parent.iterdir()) == []


def test_atomic_copy_swallows_unlink_failure_during_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The doubly-defensive path: os.replace() fails, and the cleanup
    # os.unlink() of the staging file *also* fails. The unlink failure must be
    # swallowed — the caller still sees the original replace error, not a
    # masking unlink error.
    src = tmp_path / "src.json"
    src.write_bytes(b"data")
    dst = tmp_path / "cache" / "abcd.json"
    dst.parent.mkdir(parents=True)

    monkeypatch.setattr(
        os, "replace", lambda *_a, **_k: (_ for _ in ()).throw(OSError("replace failed"))
    )
    monkeypatch.setattr(
        os, "unlink", lambda *_a, **_k: (_ for _ in ()).throw(OSError("unlink failed"))
    )

    with pytest.raises(OSError, match="replace failed"):
        _atomic_copy(src, dst)


def test_atomic_copy_write_failure_cleans_up_temp_file_and_reraises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "src.json"
    src.write_bytes(b"data")
    dst = tmp_path / "cache" / "abcd.json"
    dst.parent.mkdir(parents=True)

    real_fdopen = os.fdopen

    def _flaky_fdopen(fd, *args, **kwargs):
        f = real_fdopen(fd, *args, **kwargs)

        def _boom(_data):
            raise OSError("simulated disk full")

        f.write = _boom
        return f

    monkeypatch.setattr(os, "fdopen", _flaky_fdopen)

    with pytest.raises(OSError, match="simulated disk full"):
        _atomic_copy(src, dst)

    assert list(dst.parent.iterdir()) == []
    assert not dst.exists()
