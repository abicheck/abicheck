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

"""Field-for-field parity between `compare` and `scan --against` (ADR-049 Phase 5 §6.4 Gate).

`scan --against` now shares `compare`'s policy/suppression/scope config
surface (`abicheck/l0_export_delta.py`, `cli_scan.py`'s `@policy_options`/
`@scope_options`/`--strict-suppressions`/`--public-symbol`/
`--public-symbols-list`/`--pattern-verdicts`/`--env-matrix`). These tests
assert the two commands actually agree end to end on the same JSON snapshot
inputs under identical suppression/policy/scope flags — not just that each
command's own kwargs are threaded correctly in isolation (that's what
`test_scan_baseline_headers.py`'s kwarg-capture test already covers).

Deliberately narrow: this covers the concrete case ADR-049 Phase 5 names
(a suppression rule silencing a hard breaking removal), not an exhaustive
matrix over every flag combination -- see the Phase 5 progress note in
`docs/contribute/plans/public-contract-default.md` for what's still open.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    ScopeOrigin,
    Visibility,
)
from abicheck.serialization import snapshot_to_json


def _write_snapshot(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _elf(*names: str) -> ElfMetadata:
    return ElfMetadata(symbols=[ElfSymbol(name=n) for n in names])


def _func(name: str, mangled: str, *, origin=ScopeOrigin.PUBLIC_HEADER) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        visibility=Visibility.PUBLIC,
        access=AccessLevel.PUBLIC,
        origin=origin,
    )


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def old_snap(tmp_path: Path) -> Path:
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        functions=[_func("foo", "_Z3foov"), _func("bar", "_Z3barv")],
        elf=_elf("_Z3foov", "_Z3barv"),
    )
    return _write_snapshot(tmp_path / "old.abi.json", snap)


@pytest.fixture
def new_snap_breaking(tmp_path: Path) -> Path:
    # `bar` removed -> a removed exported symbol is a hard ABI break.
    snap = AbiSnapshot(
        library="libfoo.so",
        version="2.0",
        from_headers=True,
        functions=[_func("foo", "_Z3foov")],
        elf=_elf("_Z3foov"),
    )
    return _write_snapshot(tmp_path / "new_break.abi.json", snap)


@pytest.fixture
def suppress_bar(tmp_path: Path) -> Path:
    supp = tmp_path / "suppress.yml"
    supp.write_text(
        "version: 1\nsuppressions:\n"
        "  - symbol: '_Z3barv'\n"
        "    change_kind: func_removed\n"
        "    reason: 'intentionally removed, see MIGRATION.md'\n",
        encoding="utf-8",
    )
    return supp


def test_unsuppressed_removal_breaks_both_commands_identically(
    runner: CliRunner, old_snap: Path, new_snap_breaking: Path
) -> None:
    compare_res = runner.invoke(
        main, ["compare", str(old_snap), str(new_snap_breaking)]
    )
    scan_res = runner.invoke(
        main, ["scan", str(new_snap_breaking), "--against", str(old_snap)]
    )
    assert compare_res.exit_code == 4, compare_res.output
    assert scan_res.exit_code == 4, scan_res.output


def test_suppressed_removal_is_compatible_in_both_commands_identically(
    runner: CliRunner,
    old_snap: Path,
    new_snap_breaking: Path,
    suppress_bar: Path,
) -> None:
    # Same suppression file, same inputs: `compare --suppress` and
    # `scan --against --suppress` must agree that the removal is no longer
    # gating (ADR-049 Phase 5's whole point -- one shared config surface).
    compare_res = runner.invoke(
        main,
        [
            "compare",
            str(old_snap),
            str(new_snap_breaking),
            "--suppress",
            str(suppress_bar),
        ],
    )
    scan_res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_breaking),
            "--against",
            str(old_snap),
            "--suppress",
            str(suppress_bar),
        ],
    )
    assert compare_res.exit_code == 0, compare_res.output
    assert scan_res.exit_code == 0, scan_res.output


def test_no_scope_public_headers_agrees_across_both_commands(
    runner: CliRunner, old_snap: Path, new_snap_breaking: Path
) -> None:
    # --no-scope-public-headers is a no-op here (the removed symbol is already
    # public-header-scoped either way) but exercises the flag reaching
    # compare_snapshots identically on both sides rather than being silently
    # dropped by `scan --against` (the pre-Phase-5 hardcoded True).
    compare_res = runner.invoke(
        main,
        ["compare", str(old_snap), str(new_snap_breaking), "--no-scope-public-headers"],
    )
    scan_res = runner.invoke(
        main,
        [
            "scan",
            str(new_snap_breaking),
            "--against",
            str(old_snap),
            "--no-scope-public-headers",
        ],
    )
    assert compare_res.exit_code == 4, compare_res.output
    assert scan_res.exit_code == 4, scan_res.output
