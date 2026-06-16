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

"""Unit tests for the ``deep-compare`` orchestrator (G21.9, cli_max.py).

These exercise the orchestration glue without a compiler: snapshot inputs pass
straight through to ``compare`` (a native binary would need castxml; covered by
the integration lane). They verify the no-evidence guard, the snapshot
pass-through + ignored-evidence warning, and that ``compare``'s verdict exit
code propagates unchanged.
"""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function
from abicheck.serialization import save_snapshot


def _snap(tmp_path: Path, name: str, *, with_foo: bool) -> Path:
    snap = AbiSnapshot(library="libfoo.so", version="1", from_headers=True)
    if with_foo:
        snap.functions.append(
            Function(name="foo", mangled="foo", return_type="void", is_extern_c=True)
        )
    out = tmp_path / name
    save_snapshot(snap, out)
    return out


def test_deep_compare_requires_evidence(tmp_path: Path) -> None:
    # P09: with no source/build inputs there is nothing deeper to collect, so the
    # command refuses and points at plain `compare` rather than silently doing it.
    old = _snap(tmp_path, "old.json", with_foo=True)
    new = _snap(tmp_path, "new.json", with_foo=True)
    res = CliRunner().invoke(main, ["deep-compare", str(old), str(new)])
    assert res.exit_code != 0
    assert "needs explicit evidence" in res.output
    assert "compare" in res.output


def test_deep_compare_snapshot_passthrough_compatible(tmp_path: Path) -> None:
    # Snapshot inputs can't be re-dumped; --sources is reported as ignored and
    # the two identical surfaces compare as compatible (exit 0).
    old = _snap(tmp_path, "old.json", with_foo=True)
    new = _snap(tmp_path, "new.json", with_foo=True)
    srcs = tmp_path / "src"
    srcs.mkdir()
    res = CliRunner().invoke(
        main, ["deep-compare", str(old), str(new), "--sources", str(srcs), "--depth", "headers"]
    )
    assert res.exit_code == 0, res.output
    # Both sides warned as snapshot pass-through with ignored evidence flags.
    assert res.output.count("is a snapshot") == 2


def test_deep_compare_propagates_break_exit_code(tmp_path: Path) -> None:
    # A removed exported function is a binary ABI break; deep-compare must surface
    # compare's verdict exit code unchanged (4 = BREAKING in the legacy scheme).
    old = _snap(tmp_path, "old.json", with_foo=True)
    new = _snap(tmp_path, "new.json", with_foo=False)
    srcs = tmp_path / "src"
    srcs.mkdir()
    res = CliRunner().invoke(
        main, ["deep-compare", str(old), str(new), "--sources", str(srcs)]
    )
    assert res.exit_code == 4, (res.exit_code, res.output)


def test_deep_compare_keep_snapshots_dir_for_passthrough(tmp_path: Path) -> None:
    # --keep-snapshots is created even when both inputs pass through (no dumps
    # land in it, but the directory is honoured rather than a temp dir).
    old = _snap(tmp_path, "old.json", with_foo=True)
    new = _snap(tmp_path, "new.json", with_foo=True)
    srcs = tmp_path / "src"
    srcs.mkdir()
    keep = tmp_path / "keep"
    res = CliRunner().invoke(
        main,
        ["deep-compare", str(old), str(new), "--sources", str(srcs),
         "--keep-snapshots", str(keep), "--depth", "headers"],
    )
    assert res.exit_code == 0, res.output
    assert keep.is_dir()
