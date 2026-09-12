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

"""ADR-065 S3/S4, through the public workflow (the ``compare`` CLI).

The root ``AGENTS.md`` rule this file answers: *validate the user-facing
result* -- prove the change through the public workflow and the rendered
report, not only through the internal primitives (which
``test_package_component_inventory.py`` covers directly).

The comparison pair under test is a **package archive against a package
archive**, because that is the operand shape S3 changes: a container this
build unpacks in full now carries a declared component inventory, so the
side that lacks a member can *prove* the absence -- which is what D2 requires
before an unmatched member becomes a removal. The same content laid out as
two plain directories must still refuse to prove it; that pair is the
control, and it is run against the identical snapshots so the only
difference is the operand container.
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _snap(library: str = "libfoo.so") -> AbiSnapshot:
    return AbiSnapshot(
        library=library,
        version="1.0",
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ],
        from_headers=True,
    )


def _tree(root: Path, members: dict[str, AbiSnapshot]) -> Path:
    root.mkdir(parents=True)
    for name, snap in members.items():
        (root / name).write_text(snapshot_to_json(snap), encoding="utf-8")
    return root


def _archive(tree: Path, archive: Path) -> Path:
    with tarfile.open(archive, "w:gz") as tf:
        for entry in sorted(tree.iterdir()):
            tf.add(entry, arcname=entry.name)
    return archive


def _pair(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """OLD ships two components, NEW ships one -- as directories and as the
    identical content packed into two archives."""
    old_tree = _tree(
        tmp_path / "old", {"libfoo.json": _snap(), "libgone.json": _snap("libgone.so")}
    )
    new_tree = _tree(tmp_path / "new", {"libfoo.json": _snap()})
    return (
        old_tree,
        new_tree,
        _archive(old_tree, tmp_path / "old.tar.gz"),
        _archive(new_tree, tmp_path / "new.tar.gz"),
    )


def _run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


def _report(*args: str) -> tuple[int, dict]:
    code, out = _run(*args, "-o", "json=-")
    return code, json.loads(out)


def _support_promise_config(tmp_path: Path, value: str) -> list[str]:
    """``--config`` args selecting ``release.support_promise: <value>``.

    one-comparison-product.md Phase 7i demoted ``compare --support-promise``
    to this project-config key (ADR-065 D1/D6 already called it a
    contract-policy field), so the CLI-boundary tests below drive it the one
    way that is left.
    """
    cfg = tmp_path / f"abicheck-{value}.yml"
    # Quoted deliberately: bare ``off`` is a YAML *boolean*, and the schema
    # rejects a bool here rather than coercing it -- the same trap (and the
    # same answer) as ``python.abi3_floor``'s bare ``3.9`` YAML float.
    cfg.write_text(f'release:\n  support_promise: "{value}"\n', encoding="utf-8")
    return ["--config", str(cfg)]


class TestPackageArchiveInventoryProvesAbsence:
    def test_an_archive_pair_proves_the_removal_and_a_directory_pair_does_not(
        self, tmp_path: Path
    ) -> None:
        """The one behaviour change S3 makes, stated as the contrast that
        isolates it: identical content, two operand shapes, two answers -- and
        the *directory* answer is the pre-S3 one, unchanged."""
        old_dir, new_dir, old_pkg, new_pkg = _pair(tmp_path)

        code, report = _report("compare", str(old_pkg), str(new_pkg))
        scope = report["comparison_scope"]
        assert scope["new_inventory"]["completeness"] == "proven"
        gone = next(m for m in scope["members"] if m["member"] == "libgone.json")
        assert gone["state"] == "not_supplied"
        # Proven complete on NEW -> the member is answered, so the scope is
        # not "incompletely checked" and the run still exits cleanly.
        assert scope["completeness"] == "complete"
        assert code == 0

        code, report = _report("compare", str(old_dir), str(new_dir))
        scope = report["comparison_scope"]
        assert scope["new_inventory"]["completeness"] == "unproven"
        assert scope["completeness"] == "incomplete"

    def test_exit_8_needs_the_archive_proof(self, tmp_path: Path) -> None:
        """``gate.fail_on_removed_library`` reads the *proven* removal set
        (Phase 7d, one-comparison-product.md §4.1: the former
        ``--fail-on-removed-library`` flag, gone from the CLI). Before S3
        no live operand could ever produce one; the directory pair still
        cannot, and that is the migration boundary."""
        old_dir, new_dir, old_pkg, new_pkg = _pair(tmp_path)
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("gate:\n  fail_on_removed_library: true\n")
        code, _ = _run(
            "compare", str(old_pkg), str(new_pkg), "--config", str(cfg)
        )
        assert code == 8
        code, _ = _run(
            "compare", str(old_dir), str(new_dir), "--config", str(cfg)
        )
        assert code == 0

    def test_unmatched_old_is_reported_for_both_shapes(self, tmp_path: Path) -> None:
        """ADR-065 S4: the JSON key means "no counterpart" and is read off the
        acquisition record, so it lists the member whether or not the absence
        was proven -- the raw set difference it used to be computed from is
        gone, not its reporting."""
        old_dir, new_dir, old_pkg, new_pkg = _pair(tmp_path)
        for old, new in ((old_pkg, new_pkg), (old_dir, new_dir)):
            _code, report = _report("compare", str(old), str(new))
            assert report["unmatched_old"] == ["libgone.json"]
            assert report["unmatched_new"] == []

    def test_the_stderr_notice_distinguishes_removed_from_unmatched(
        self, tmp_path: Path
    ) -> None:
        """The other S4 holdout. One wording per state, not one wording for
        both -- and the proven one names its own proof."""
        old_dir, new_dir, old_pkg, new_pkg = _pair(tmp_path)
        _code, out = _run("compare", str(old_pkg), str(new_pkg))
        assert "library removed: libgone.json" in out
        assert "unpacked in full" in out
        _code, out = _run("compare", str(old_dir), str(new_dir))
        assert "library unmatched (no counterpart on NEW): libgone.json" in out
        assert "library removed" not in out


class TestSupportPromiseFindingsCli:
    @pytest.mark.parametrize("configured", [False, True])
    def test_off_by_default_emits_no_finding(
        self, tmp_path: Path, configured: bool
    ) -> None:
        """D1: a support-promise change is emitted *under a policy*, never
        inferred -- so the proven removal above changes nothing on its own."""
        _old_dir, _new_dir, old_pkg, new_pkg = _pair(tmp_path)
        flag = _support_promise_config(tmp_path, "off") if configured else []
        _code, report = _report("compare", str(old_pkg), str(new_pkg), *flag)
        assert all("support_promise" not in lib for lib in report["libraries"]), report[
            "libraries"
        ]

    def test_declared_reports_the_retired_component_and_gates(
        self, tmp_path: Path
    ) -> None:
        _old_dir, _new_dir, old_pkg, new_pkg = _pair(tmp_path)
        code, report = _report(
            "compare", str(old_pkg), str(new_pkg),
            *_support_promise_config(tmp_path, "declared"),
        )
        entry = next(lib for lib in report["libraries"] if lib.get("support_promise"))
        assert entry["library"] == "libgone.json"
        assert entry["support_promise"] == "retired"
        assert entry["verdict"] == "BREAKING"
        # It is a real finding, so it moves the release verdict and the exit
        # code -- unlike --fail-on-removed-library's exit 8, which is a gate
        # with no finding behind it.
        assert report["verdict"] == "BREAKING"
        assert code == 4

    def test_declared_emits_nothing_without_a_proof(self, tmp_path: Path) -> None:
        """The safety property at the CLI boundary: the same policy over the
        same content, laid out as directories, invents no contract change."""
        old_dir, new_dir, _old_pkg, _new_pkg = _pair(tmp_path)
        code, report = _report(
            "compare", str(old_dir), str(new_dir),
            *_support_promise_config(tmp_path, "declared"),
        )
        assert all("support_promise" not in lib for lib in report["libraries"])
        assert code == 0

    def test_a_proven_addition_is_compatible(self, tmp_path: Path) -> None:
        """The symmetric rule, against a proven-complete OLD inventory."""
        _old_dir, _new_dir, old_pkg, new_pkg = _pair(tmp_path)
        code, report = _report(
            "compare", str(new_pkg), str(old_pkg),
            *_support_promise_config(tmp_path, "declared"),
        )
        entry = next(lib for lib in report["libraries"] if lib.get("support_promise"))
        assert entry["support_promise"] == "introduced"
        assert entry["verdict"] == "NO_CHANGE"
        assert code == 0

    def test_an_invalid_value_is_a_usage_error_not_a_crash(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1180: the removed ``--support-promise`` Click
        option enforced its ``off``/``declared`` enum itself; once the value
        moved to ``.abicheck.yml`` alone, an out-of-enum value must still
        fail as a usage error at config-load time -- not survive all the way
        to ``support_promise_changes()``'s own uncaught ``ValueError``."""
        _old_dir, _new_dir, old_pkg, new_pkg = _pair(tmp_path)
        code, out = _run(
            "compare", str(old_pkg), str(new_pkg),
            *_support_promise_config(tmp_path, "typo"),
        )
        assert code == 64, out
        assert "release.support_promise" in out
