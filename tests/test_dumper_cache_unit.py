# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the best-effort AST cache-path helper (``dumper_cache``).

``_cache_path`` picks a per-backend cache directory following the platform
convention (XDG on POSIX, ``LOCALAPPDATA`` on Windows) and degrades to the
system temp dir when the preferred location cannot be created. These cover the
Windows branch (unreachable on the Linux CI host without monkeypatching
``sys.platform``) and the OSError fallback, since both are pure-Python paths.
"""
from __future__ import annotations

import sys
from pathlib import Path

from abicheck.dumper_cache import _cache_path


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
