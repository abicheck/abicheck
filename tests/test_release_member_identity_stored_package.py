# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A stored ``BundleFacts`` OLD pairs with a stored ``ProjectSnapshot`` NEW.

Unit-lane twin of the ``bundle_facts x package`` cell of
``tests/test_release_member_identity_integration.py``: the same route over
synthetic snapshots, so it needs no compiler. Before the fix,
``compare OLD.bundle-facts.json NEW_PACKAGE_DIR`` listed the package's object
store as live files and paired nothing (exit 1, ``no_comparison_completed``).

Oracle: the same two snapshot sets compared as two loose directories of
stored documents (``compare OLD_DIR NEW_DIR``), which pair by file name.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Param, Visibility
from abicheck.model.elf_facts import ElfMetadata
from abicheck.project_snapshot_store import DirectoryObjectStore, write_project_manifest
from abicheck.serialization import save_bundle_facts, snapshot_to_json
from abicheck.storage.bundle_facts_package import write_bundle_facts_package
from abicheck.workflows.bundle_facts_capture import capture_bundle_facts

#: member -> (OLD functions, NEW functions): a break, an addition, no change.
_MEMBERS = {
    "libcore.so.1": (["core_a", "core_gone"], ["core_a"]),
    "libmid.so.2": (["mid"], ["mid", "mid_new"]),
    "libtop.so": (["top"], ["top"]),
}


def _snap(member: str, funcs: list[str]) -> AbiSnapshot:
    return AbiSnapshot(
        library=member,
        version="1",
        functions=[
            Function(
                name=f,
                mangled=f,
                return_type="int",
                params=[Param(name="x", type="int")],
                visibility=Visibility.PUBLIC,
            )
            for f in funcs
        ],
        elf=ElfMetadata(soname=member),
    )


def _side(index: int) -> dict[str, AbiSnapshot]:
    return {m: _snap(m, funcs[index]) for m, funcs in _MEMBERS.items()}


def _facts(snapshots: dict[str, AbiSnapshot], **extra: object):
    facts = capture_bundle_facts(snapshots, variant_fingerprint="identity-unit")
    return dataclasses.replace(
        facts, library_filenames={name: name for name in snapshots}, **extra
    )


def _package(root: Path, snapshots: dict[str, AbiSnapshot], **extra: object) -> Path:
    write_project_manifest(
        root,
        write_bundle_facts_package(
            _facts(snapshots, **extra), store=DirectoryObjectStore(root)
        ),
    )
    return root


def _directory(root: Path, snapshots: dict[str, AbiSnapshot]) -> Path:
    root.mkdir(parents=True)
    for name, snap in snapshots.items():
        (root / name).write_text(snapshot_to_json(snap), encoding="utf-8")
    return root


def _compare(old: Path, new: Path, out: Path) -> tuple[int, dict, str]:
    result = CliRunner().invoke(
        main, ["compare", str(old), str(new), "-o", f"json={out}"]
    )
    report = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
    return result.exit_code, report, result.output


def _decision(code: int, report: dict) -> dict:
    return {
        "exit": code,
        "verdicts": sorted(e["verdict"] for e in report["libraries"]),
        "unmatched_old": report["unmatched_old"],
        "unmatched_new": report["unmatched_new"],
    }


def test_a_bundle_facts_old_pairs_every_member_of_a_stored_package_new(
    tmp_path: Path,
) -> None:
    old_facts = tmp_path / "old.bundle-facts.json"
    save_bundle_facts(_facts(_side(0)), old_facts)
    package = _package(tmp_path / "new-package", _side(1))
    oracle = _decision(
        *_compare(
            _directory(tmp_path / "old-dir", _side(0)),
            _directory(tmp_path / "new-dir", _side(1)),
            tmp_path / "oracle.json",
        )[:2]
    )
    assert oracle["verdicts"] == ["BREAKING", "COMPATIBLE", "NO_CHANGE"]

    code, report, output = _compare(old_facts, package, tmp_path / "r.json")

    assert report, output
    assert _decision(code, report) == oracle


def test_a_stored_package_new_recording_degraded_members_is_refused(
    tmp_path: Path,
) -> None:
    """The stored-OLD driver cannot mark a NEW member `failed`, so a package
    whose capture recorded a degraded member is a usage error, not a
    comparison of its ELF-only stand-in."""
    old_facts = tmp_path / "old.bundle-facts.json"
    save_bundle_facts(_facts(_side(0)), old_facts)
    package = _package(
        tmp_path / "new-package",
        _side(1),
        degraded_members={"libmid.so.2": "header parse failed"},
    )

    code, _report, output = _compare(old_facts, package, tmp_path / "r.json")

    assert code == 64, output
    assert "degraded members" in output
    assert "libmid.so" in output


@pytest.mark.parametrize("shape", ["directory", "bundle_facts_document"])
def test_a_non_package_new_operand_is_not_read_as_a_package(
    tmp_path: Path, shape: str
) -> None:
    """Negative control: only a `ProjectSnapshot` package directory takes the
    stored-package branch; a loose directory still pairs by discovery."""
    old_facts = tmp_path / "old.bundle-facts.json"
    save_bundle_facts(_facts(_side(0)), old_facts)
    new = _directory(tmp_path / "new-dir", _side(1))
    if shape == "bundle_facts_document":
        new = tmp_path / "new.bundle-facts.json"
        save_bundle_facts(_facts(_side(1)), new)

    code, report, output = _compare(old_facts, new, tmp_path / "r.json")

    assert report, output
    assert sorted(e["verdict"] for e in report["libraries"]) == [
        "BREAKING",
        "COMPATIBLE",
        "NO_CHANGE",
    ]
    assert code == 4
