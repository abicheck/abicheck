"""A whole-snapshot cache hit reports the identity a cold dump of its own path reports.

The cache keys on the binary's content, so one entry serves every path holding
those bytes. The oracle here is an uncached ``run_dump`` of the path that hit,
not the cache's own helper: the hit must agree with what a cold dump writes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from abicheck.model import AbiSnapshot
from abicheck.service_dump_cache import cached_run_dump
from abicheck.workflows.dump.native import run_dump

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler"),
]

_IDENTITY_FIELDS = (
    "library",
    "source_path",
    "source_mtime",
    "source_mtime_epoch",
    "source_size",
)


def _identity(snap: AbiSnapshot) -> dict[str, Any]:
    return {name: getattr(snap, name) for name in _IDENTITY_FIELDS}


def _build_library(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    src = path.parent / f"{path.name}.c"
    src.write_text("int exported_fn(int x) { return x + 1; }\n")
    subprocess.run(["cc", "-shared", "-fPIC", "-o", str(path), str(src)], check=True)
    src.unlink()
    return path


def _alias(kind: str, warm: Path, root: Path) -> Path:
    """A path holding the same bytes as *warm*, reached by one kind of rename."""
    if kind == "same-dir-new-basename":
        alias = warm.parent / "libbaz.so.2"
        shutil.copyfile(warm, alias)
        return alias
    if kind == "other-dir":
        alias = root / "elsewhere" / "libbar.so.1"
        alias.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(warm, alias)
        return alias
    if kind == "symlink":
        link = root / "links" / "libsym.so"
        link.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(warm, link)
        return link
    raise AssertionError(kind)


@pytest.mark.parametrize("kind", ["same-dir-new-basename", "other-dir", "symlink"])
def test_hit_identity_matches_a_cold_dump_of_the_same_path(
    tmp_path: Path, kind: str
) -> None:
    warm = _build_library(tmp_path / "warm" / "libfoo.so.1")
    alias = _alias(kind, warm, tmp_path / kind)

    calls: list[Path] = []

    def counting(path: Path, *args: Any, **kwargs: Any) -> AbiSnapshot:
        calls.append(path)
        return run_dump(path, *args, **kwargs)

    cached_run_dump(counting, warm, "elf")
    hit = cached_run_dump(counting, alias, "elf")
    cold = run_dump(alias, "elf")

    assert calls == [warm], "the second call must be served from the cache"
    # Must-not-merge: the hit names the requested path, not the one that populated it.
    assert hit.library == alias.name != warm.name
    assert _identity(hit) == _identity(cold)
