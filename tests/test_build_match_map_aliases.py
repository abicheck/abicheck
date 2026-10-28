"""``build_match_map`` warns only for distinct files, not filesystem aliases.

A ``libfoo.so -> libfoo.so.2`` dev symlink (or a hard link) names one
library twice; the selection is unambiguous, so warning about it is noise
(MKL blacs/scalapack fixture: 34 such warnings per run).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from abicheck.binary_utils import build_match_map


def _alias(kind: str, target: Path, link: Path) -> None:
    if kind == "symlink":
        link.symlink_to(target.name)
    else:
        os.link(target, link)


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
@pytest.mark.parametrize(
    ("real", "aliases"),
    [
        ("libfoo.so.2", ["libfoo.so"]),
        ("libmkl_blacs_intelmpi_lp64.so.2", ["libmkl_blacs_intelmpi_lp64.so"]),
        ("libbar.so.1.2.3", ["libbar.so", "libbar.so.1"]),
    ],
)
def test_aliases_of_one_file_do_not_warn(
    tmp_path: Path, kind: str, real: str, aliases: list[str]
) -> None:
    target = tmp_path / real
    target.write_bytes(b"\x7fELF")
    for a in aliases:
        _alias(kind, target, tmp_path / a)
    paths = sorted(tmp_path.iterdir())
    mapping, warnings = build_match_map(paths)
    assert warnings == []
    (selected,) = mapping.values()
    assert selected.resolve() == target.resolve()


def test_distinct_files_still_warn(tmp_path: Path) -> None:
    (tmp_path / "libfoo.so").write_bytes(b"old")
    (tmp_path / "libfoo.so.2").write_bytes(b"new")
    mapping, warnings = build_match_map(sorted(tmp_path.iterdir()))
    assert len(warnings) == 1 and "Ambiguous match" in warnings[0]
    assert next(iter(mapping.values())).name == "libfoo.so.2"


def test_alias_plus_distinct_file_still_warns(tmp_path: Path) -> None:
    (tmp_path / "libfoo.so.2").write_bytes(b"v2")
    (tmp_path / "libfoo.so").symlink_to("libfoo.so.2")
    (tmp_path / "libfoo.so.1").write_bytes(b"v1")
    _, warnings = build_match_map(sorted(tmp_path.iterdir()))
    assert len(warnings) == 1
