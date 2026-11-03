# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``storage.atomic_file.replace_file`` rides out Windows' transient refusal.

Windows fails a rename onto a file another handle has open, or another
writer is replacing, with ``PermissionError``; POSIX never does. Two
publishers of one package ``manifest.json`` racing without the cross-process
lock (tests/test_package_immutable_publication.py's ``lock-unavailable`` row)
lost one of them that way on windows-latest. Oracle: the destination's bytes,
read back here, and the number of ``os.replace`` attempts counted here.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from abicheck.storage import atomic_file


def _flaky_replace(monkeypatch: pytest.MonkeyPatch, refusals: int) -> list[int]:
    """``os.replace`` refuses the first *refusals* calls, then behaves."""
    calls: list[int] = []
    real = os.replace

    def replace(src: str, dst: str) -> None:
        calls.append(1)
        if len(calls) <= refusals:
            raise PermissionError(13, "Access is denied")
        real(src, dst)

    monkeypatch.setattr(atomic_file.os, "replace", replace)
    monkeypatch.setattr(atomic_file.time, "sleep", lambda _s: None)
    return calls


@pytest.mark.parametrize(
    "refusals", [0, 1, 3, len(atomic_file._WINDOWS_REPLACE_BACKOFF_S)]
)
def test_windows_retries_until_the_refusal_clears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusals: int
) -> None:
    monkeypatch.setattr(atomic_file.sys, "platform", "win32")
    calls = _flaky_replace(monkeypatch, refusals)
    dst = tmp_path / "manifest.json"
    dst.write_bytes(b"old")
    atomic_file.atomic_write(dst, b"new")
    assert dst.read_bytes() == b"new"
    assert len(calls) == refusals + 1
    assert [p.name for p in tmp_path.iterdir()] == ["manifest.json"]


def test_windows_raises_a_refusal_that_outlasts_the_back_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(atomic_file.sys, "platform", "win32")
    attempts = len(atomic_file._WINDOWS_REPLACE_BACKOFF_S) + 1
    calls = _flaky_replace(monkeypatch, attempts)
    dst = tmp_path / "manifest.json"
    dst.write_bytes(b"old")
    with pytest.raises(PermissionError):
        atomic_file.atomic_write(dst, b"new")
    assert len(calls) == attempts
    # The previous content stays and the temp file is cleaned up.
    assert dst.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["manifest.json"]


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_elsewhere_a_permission_error_is_raised_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    monkeypatch.setattr(atomic_file.sys, "platform", platform)
    calls = _flaky_replace(monkeypatch, 1)
    with pytest.raises(PermissionError):
        atomic_file.replace_file(tmp_path / "a", tmp_path / "b")
    assert len(calls) == 1
