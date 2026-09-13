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

"""The *public-workflow* layer of bug class
`comparability.header_inventory_growth_is_not_profile_drift` — a release that
adds a public header must produce an ABI verdict through the real
``abicheck compare`` CLI, not a "not comparable" abort.

`tests/test_comparability_header_inventory_growth.py` owns the invariant
itself (exhaustively, against an independent oracle) at the
`compute_extraction_contract`/`check_contracts_comparable` level. That is
deliberately not enough on its own (AGENTS.md, "Validate the user-facing
result": prove a change through the public workflow and the rendered report,
not only through an internal detector). The reported failure was a *CLI*
failure — `abicheck compare OLD.so NEW.so --header old=<dir> --header
new=<dir>` exiting 16 with no verdict — and the mechanism that caused it,
`header_utils.iter_directory_headers` returning its result **sorted**, lives
in the *directory-expansion* wiring that a hand-built `declared_headers` list
bypasses entirely. A regression anywhere in that path (expansion order,
live extraction, the `except (ProfileMismatchError, ScopeMismatchError)`
branch in `cli_compare_helpers.run_compare`, report rendering) would leave
the original command broken while a gate-level test stayed green.

So both layers are here, and each asserts the *rendered* result:

* `TestStoredSnapshotPair` — no toolchain required, so it runs in the default
  fast lane: two real on-disk snapshots carrying genuine contracts whose
  header inventories differ by one interior addition, driven through the real
  Click command, asserting the exit code and the emitted JSON report.
* `TestLiveDirectoryOperands` (``integration``) — the reported invocation
  verbatim: two real compiled ``.so`` files, ``--header old=<dir> --header
  new=<dir>`` directory operands expanded by the real CLI, live L2
  extraction, and the added function asserted present in the rendered report.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.comparability import compute_extraction_contract
from abicheck.model import AbiSnapshot, Function
from abicheck.serialization import snapshot_to_json

# The reported inventory: an added `json.h` sorts BETWEEN `data.h` and
# `log.h`, so directory expansion places it interior to the sequence. That
# is the whole bug -- a fixture that only ever appends cannot reach it.
_OLD_HEADERS = ("client.h", "data.h", "log.h", "source.h")
_ADDED_HEADER = "json.h"
_NEW_HEADERS = tuple(sorted([*_OLD_HEADERS, _ADDED_HEADER]))


def _write_headers(root: Path, names: tuple[str, ...]) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)
    written = []
    for name in names:
        path = root / name
        path.write_text(f"int pvxs_{path.stem}(int);\n", encoding="utf-8")
        written.append(path)
    return written


class TestStoredSnapshotPair:
    """Real CLI, real on-disk snapshots, no toolchain -- so the wiring this
    bug broke is covered in the default fast lane rather than only behind the
    ``integration`` marker."""

    def _snapshot(self, root: Path, names: tuple[str, ...]) -> AbiSnapshot:
        headers = _write_headers(root, names)
        return AbiSnapshot(
            library="libpvxs.so.1.5",
            version="1.5",
            from_headers=True,
            functions=[
                Function(
                    name=f"pvxs_{Path(n).stem}",
                    mangled=f"_Z9pvxs_{Path(n).stem}i",
                    return_type="int",
                )
                for n in names
            ],
            contract=compute_extraction_contract(
                declared_headers=headers, l2_frontend_ran=True
            ),
        )

    def _write_pair(self, tmp_path: Path) -> tuple[Path, Path]:
        old = self._snapshot(tmp_path / "old" / "pvxs", _OLD_HEADERS)
        new = self._snapshot(tmp_path / "new" / "pvxs", _NEW_HEADERS)
        # The bug's own precondition, asserted rather than assumed: these two
        # genuinely differ on profile_fingerprint / header_sequence, so the
        # gate really is the thing being exercised.
        assert old.contract.profile_fingerprint != new.contract.profile_fingerprint
        assert (
            old.contract.profile_fields["header_sequence"]
            != new.contract.profile_fields["header_sequence"]
        )
        old_p = tmp_path / "old.abi.json"
        new_p = tmp_path / "new.abi.json"
        old_p.write_text(snapshot_to_json(old), encoding="utf-8")
        new_p.write_text(snapshot_to_json(new), encoding="utf-8")
        return old_p, new_p

    def test_added_public_header_produces_a_verdict_not_a_gate_abort(
        self, tmp_path: Path
    ) -> None:
        old_p, new_p = self._write_pair(tmp_path)
        out = tmp_path / "report.json"
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "-o", f"json={out}"]
        )
        # Exit 16 is the comparability abort this bug produced; any of the
        # ordinary compatibility codes means a verdict was actually reached.
        assert result.exit_code != 16, result.output
        assert "not comparable" not in result.output
        assert "profile_fingerprint" not in result.output

        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["verdict"] is not None
        # The rendered report, not just the exit code: the added API is
        # REPORTED, which is the product rule the gate was violating.
        assert any(
            _ADDED_HEADER.removesuffix(".h") in str(change.get("symbol", ""))
            for change in report.get("changes", [])
        ), report.get("changes")

    def test_reordered_existing_headers_still_aborts_through_the_cli(
        self, tmp_path: Path
    ) -> None:
        """The negative control at the same public surface: a genuine reorder
        of the existing headers must still reach the user as exit 16."""
        old = self._snapshot(tmp_path / "old" / "pvxs", _OLD_HEADERS)
        new = self._snapshot(tmp_path / "new" / "pvxs", _OLD_HEADERS[::-1])
        old_p = tmp_path / "old.abi.json"
        new_p = tmp_path / "new.abi.json"
        old_p.write_text(snapshot_to_json(old), encoding="utf-8")
        new_p.write_text(snapshot_to_json(new), encoding="utf-8")

        result = CliRunner().invoke(main, ["compare", str(old_p), str(new_p)])
        assert result.exit_code == 16, result.output
        assert "not comparable" in result.output


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform == "win32", reason="builds an ELF .so pair, linux/macos only"
)
@pytest.mark.skipif(
    shutil.which("g++") is None, reason="g++ required to build fixtures"
)
class TestLiveDirectoryOperands:
    """The reported invocation verbatim: real binaries, real `--header
    old=<dir> --header new=<dir>` directory operands, live L2 extraction.

    This is the layer that covers `header_utils.iter_directory_headers`'s
    sorted expansion -- the mechanism that made the added header land
    interior in the first place, and which no hand-built `declared_headers`
    list reaches.
    """

    def _build(self, root: Path, names: tuple[str, ...]) -> Path:
        _write_headers(root / "pvxs", names)
        stems = [Path(n).stem for n in names]
        source = "\n".join(
            [
                *(f'#include "pvxs/{n}"' for n in names),
                *(f"int pvxs_{s}(int x) {{ return x; }}" for s in stems),
            ]
        )
        cpp = root / "lib.cpp"
        cpp.write_text(source + "\n", encoding="utf-8")
        so = root / "libpvxs.so.1.5"
        subprocess.run(
            [
                "g++",
                "-std=gnu++11",
                "-fPIC",
                "-shared",
                "-I",
                str(root),
                "-o",
                str(so),
                str(cpp),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return so

    def test_reported_invocation_reports_the_added_api(self, tmp_path: Path) -> None:
        old_so = self._build(tmp_path / "old", _OLD_HEADERS)
        new_so = self._build(tmp_path / "new", _NEW_HEADERS)
        out = tmp_path / "report.json"

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_so),
                str(new_so),
                "--header",
                f"old={tmp_path / 'old' / 'pvxs'}",
                "--header",
                f"new={tmp_path / 'new' / 'pvxs'}",
                "-o",
                f"json={out}",
            ],
        )
        # Pre-fix this was exit 16 with
        # "profile_fingerprint mismatch; differing fields: header_sequence"
        # and no verdict at all -- the exact reported failure.
        assert result.exit_code == 0, result.output
        assert "not comparable" not in result.output

        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["verdict"] == "COMPATIBLE", report["verdict"]
        added = [c for c in report.get("changes", []) if c.get("kind") == "func_added"]
        assert any("pvxs_json" in str(c.get("symbol", "")) for c in added), report.get(
            "changes"
        )
