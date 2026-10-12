# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""`workflows.compare_finalize.attach_operand_metadata`: one owner for the
operand metadata and the same-binary note.

The native CLI (`frontends.cli.runtime._finalize_compare_result`) and the
typed API (`finalize_classified_pair`) each used to carry their own copy of
this step. Both now call the workflow owner, so the property stated here is
two-fold: the owner's answer matches an independent oracle (`hashlib` over
the operand bytes) across every operand kind a compare accepts, and the CLI
finalization produces exactly the same fields as the owner for each.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from abicheck.checker_types import DiffResult
from abicheck.confidence import SAME_BINARY_WARNING_MARKER
from abicheck.frontends.cli.runtime import _finalize_compare_result
from abicheck.model import AbiSnapshot
from abicheck.workflows.compare_finalize import attach_operand_metadata

_ELF = b"\x7fELF" + b"\x00" * 60


def _binary(payload: bytes) -> Callable[[Path, str], Path]:
    def make(root: Path, name: str) -> Path:
        p = root / f"{name}.so"
        p.write_bytes(_ELF + payload)
        return p

    return make


def _snapshot_json(root: Path, name: str) -> Path:
    p = root / f"{name}.abi.json"
    p.write_text(json.dumps({"library": "libfoo.so", "version": "1.0"}))
    return p


def _symvers(root: Path, name: str) -> Path:
    p = root / f"{name}.symvers"
    p.write_text("0x12345678\tfoo\tvmlinux\tEXPORT_SYMBOL\n")
    return p


def _directory(root: Path, name: str) -> Path:
    p = root / name
    p.mkdir()
    return p


def _linker_script_to_target(root: Path, name: str) -> Path:
    """A GNU ld script whose INPUT() names a DSO with fixed content."""
    target = root / f"lib{name}target.so.1"
    target.write_bytes(_ELF + b"same")
    script = root / f"{name}.so"
    script.write_text(f"INPUT ( {target.name} )\n")
    return script


def _expected_metadata(path: Path) -> tuple[str, int] | None:
    """Oracle: the hashable bytes behind *path*, or ``None`` for a non-binary."""
    if path.is_dir() or path.suffix in (".json", ".symvers"):
        return None
    if path.read_bytes().startswith(b"INPUT"):
        target = path.parent / path.read_text().split()[2]
        data = target.read_bytes()
    else:
        data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


#: (id, old maker, new maker, snapshots identical?, expect the note?)
CASES = [
    ("identical-binaries", _binary(b"a"), _binary(b"a"), True, True),
    ("distinct-binaries", _binary(b"a"), _binary(b"b"), True, False),
    ("distinct-binaries-same-length", _binary(b"ab"), _binary(b"ba"), True, False),
    ("identical-json-snapshots", _snapshot_json, _snapshot_json, True, True),
    ("distinct-json-snapshots", _snapshot_json, _snapshot_json, False, False),
    ("identical-symvers-snapshots-differ", _symvers, _symvers, False, False),
    ("package-directories-snapshots-differ", _directory, _directory, False, False),
    ("script-vs-its-target", _linker_script_to_target, _binary(b"same"), True, True),
    ("mixed-binary-vs-json", _binary(b"a"), _snapshot_json, True, False),
]


def _snapshots(identical: bool) -> tuple[AbiSnapshot, AbiSnapshot]:
    old = AbiSnapshot(library="libfoo.so", version="1.0")
    new = AbiSnapshot(library="libfoo.so", version="1.0" if identical else "2.0")
    return old, new


def _fresh() -> DiffResult:
    return DiffResult(old_version="1.0", new_version="1.0", library="libfoo.so")


def _identity(result: DiffResult) -> tuple[object, object, list[str]]:
    return result.old_metadata, result.new_metadata, list(result.coverage_warnings)


@pytest.mark.parametrize(
    ("make_old", "make_new", "same_snapshots", "expect_note"),
    [c[1:] for c in CASES],
    ids=[c[0] for c in CASES],
)
def test_owner_matches_hash_oracle_and_cli_matches_owner(
    tmp_path: Path,
    make_old: Callable[[Path, str], Path],
    make_new: Callable[[Path, str], Path],
    same_snapshots: bool,
    expect_note: bool,
) -> None:
    (tmp_path / "o").mkdir()
    (tmp_path / "n").mkdir()
    old_path = make_old(tmp_path / "o", "old")
    new_path = make_new(tmp_path / "n", "new")
    old_snap, new_snap = _snapshots(same_snapshots)

    owner = _fresh()
    attach_operand_metadata(
        owner, old_path, new_path, old_snapshot=old_snap, new_snapshot=new_snap
    )

    for path, meta in ((old_path, owner.old_metadata), (new_path, owner.new_metadata)):
        expected = _expected_metadata(path)
        if expected is None:
            assert meta is None
        else:
            assert meta is not None
            assert (meta.sha256, meta.size_bytes) == expected

    noted = any(SAME_BINARY_WARNING_MARKER in w for w in owner.coverage_warnings)
    assert noted is expect_note, owner.coverage_warnings

    cli = _fresh()
    _finalize_compare_result(
        cli,
        old_path,
        new_path,
        show_redundant=False,
        old_snapshot=old_snap,
        new_snapshot=new_snap,
    )
    assert _identity(cli) == _identity(owner)


def test_digest_fallback_needs_both_snapshots(tmp_path: Path) -> None:
    """Without snapshot objects there is no content digest, so two identical
    JSON snapshot paths make no same-binary claim (weaker evidence narrows
    the conclusion rather than asserting one)."""
    old_path = _snapshot_json(tmp_path, "old")
    new_path = _snapshot_json(tmp_path, "new")
    old_snap, _ = _snapshots(True)

    for kwargs in ({}, {"old_snapshot": old_snap}, {"new_snapshot": old_snap}):
        result = _fresh()
        attach_operand_metadata(result, old_path, new_path, **kwargs)
        assert result.old_metadata is None and result.new_metadata is None
        assert result.coverage_warnings == []
