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

"""One removed export is one BREAKING finding, and one SONAME advisory count.

The bug class: a single export loss reached the report twice. A header-aware
``func_visibility_changed`` (the declaration survives, the export is gone) and
an L0 symbols-only ``func_removed_elf_only`` (appended to the header-scoped
diff without reading it) named the same symbol, and ``soname_bump_recommended``
then counted both: "2 binary-incompatible change(s)" for one removal.

The invariant, over real gcc-built libraries and the public ``compare`` CLI:

* every removed symbol carries exactly one BREAKING finding, and no other
  symbol carries one;
* the SONAME advisory's count equals the number of distinct removed symbols.

Every expected value below is written out by hand for its scenario. None is
derived from the detectors, so a regression cannot pass by agreeing with
itself.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="ELF tests require Linux"),
    pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc"),
]

_KEEP = "int keep(int a) { return a; }\n"
_ADD = "int add(int a, int b) { return a + b; }\n"
_SUB = "int sub(int a, int b) { return a - b; }\n"
_PROTECTED = '__attribute__((visibility("protected"))) '
# Same name, internal linkage: still in .symtab, gone from .dynsym.
_HIDDEN_ADD = "static int add(int a, int b) { return a + b; }\n"
_COUNTER = "int counter = 1;\n"
_HIDDEN_COUNTER = "static int counter = 1;\n"

_HDR_ADD_SUB_KEEP = "int add(int a, int b);\nint sub(int a, int b);\nint keep(int a);\n"
_HDR_ADD_KEEP = "int add(int a, int b);\nint keep(int a);\n"
_HDR_KEEP = "int keep(int a);\n"
_HDR_COUNTER_KEEP = "extern int counter;\nint keep(int a);\n"

# Version script: ``add`` belongs to VERS_1 on OLD and is dropped on NEW.
_VERS_OLD = "VERS_1 { global: add; keep; local: *; };\n"
_VERS_NEW = "VERS_1 { global: keep; local: *; };\n"


@dataclass(frozen=True)
class _Scenario:
    old_src: str
    new_src: str
    # Expected BREAKING findings per symbol, written out by hand.
    expected_breaking: dict[str, int]
    old_header: str | None = None
    new_header: str | None = None
    old_version_script: str | None = None
    new_version_script: str | None = None


SCENARIOS: dict[str, _Scenario] = {
    # Function removed from the export table, no header anywhere.
    "func_removed_elf_only": _Scenario(
        old_src=_ADD + _KEEP,
        new_src=_KEEP,
        expected_breaking={"add": 1},
    ),
    # Function hidden on NEW (declaration survives): the header-aware
    # visibility finding and the ELF-only fold both see the same loss.
    "func_hidden_with_header": _Scenario(
        old_src=_ADD + _KEEP,
        new_src=_HIDDEN_ADD + _KEEP,
        old_header=_HDR_ADD_KEEP,
        new_header=_HDR_ADD_KEEP,
        expected_breaking={"add": 1},
    ),
    # Function removed and its declaration removed too.
    "func_removed_with_header": _Scenario(
        old_src=_ADD + _KEEP,
        new_src=_KEEP,
        old_header=_HDR_ADD_KEEP,
        new_header=_HDR_KEEP,
        expected_breaking={"add": 1},
    ),
    # Versioned export, removed, no header.
    "func_versioned_removed_elf_only": _Scenario(
        old_src=_ADD + _KEEP,
        new_src=_KEEP,
        old_version_script=_VERS_OLD,
        new_version_script=_VERS_NEW,
        expected_breaking={"add": 1},
    ),
    # Versioned export, hidden on NEW, declaration survives.
    "func_versioned_hidden_with_header": _Scenario(
        old_src=_ADD + _KEEP,
        new_src=_HIDDEN_ADD + _KEEP,
        old_header=_HDR_ADD_KEEP,
        new_header=_HDR_ADD_KEEP,
        old_version_script=_VERS_OLD,
        new_version_script=_VERS_NEW,
        expected_breaking={"add": 1},
    ),
    # Default-visibility export removed (no header).
    "func_default_removed_elf_only": _Scenario(
        old_src=_ADD + _KEEP,
        new_src=_KEEP,
        expected_breaking={"add": 1},
    ),
    # Protected-visibility export removed (no header).
    "func_protected_removed_elf_only": _Scenario(
        old_src=_PROTECTED + _ADD + _KEEP,
        new_src=_KEEP,
        expected_breaking={"add": 1},
    ),
    # Protected-visibility export hidden on NEW, declaration survives.
    "func_protected_hidden_with_header": _Scenario(
        old_src=_PROTECTED + _ADD + _KEEP,
        new_src=_HIDDEN_ADD + _KEEP,
        old_header=_HDR_ADD_KEEP,
        new_header=_HDR_ADD_KEEP,
        expected_breaking={"add": 1},
    ),
    # Variable removed from the export table, no header.
    "var_removed_elf_only": _Scenario(
        old_src=_COUNTER + _KEEP,
        new_src=_KEEP,
        expected_breaking={"counter": 1},
    ),
    # Variable hidden on NEW, declaration survives.
    "var_hidden_with_header": _Scenario(
        old_src=_COUNTER + _KEEP,
        new_src=_HIDDEN_COUNTER + _KEEP,
        old_header=_HDR_COUNTER_KEEP,
        new_header=_HDR_COUNTER_KEEP,
        expected_breaking={"counter": 1},
    ),
    # Variable removed together with its declaration.
    "var_removed_with_header": _Scenario(
        old_src=_COUNTER + _KEEP,
        new_src=_KEEP,
        old_header=_HDR_COUNTER_KEEP,
        new_header=_HDR_KEEP,
        expected_breaking={"counter": 1},
    ),
    # Two removed symbols in one release: one hidden with its declaration
    # kept (the duplicate-prone path), one removed with its declaration. The
    # SONAME count must be the two symbols, not the three findings behind them.
    "two_symbols_mixed_paths": _Scenario(
        old_src=_ADD + _SUB + _KEEP,
        new_src=_HIDDEN_ADD + _KEEP,
        old_header=_HDR_ADD_SUB_KEEP,
        new_header=_HDR_ADD_KEEP,
        expected_breaking={"add": 1, "sub": 1},
    ),
}


def _compile(src: str, out: Path, flags: tuple[str, ...]) -> None:
    c_file = out.with_suffix(".c")
    c_file.write_text(src, encoding="utf-8")
    result = subprocess.run(
        ["gcc", "-shared", "-fPIC", "-O0", *flags, "-o", str(out), str(c_file)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    # A configured compiler that fails is a failure, not a skip: a skipped
    # invariant would prove nothing about the detector.
    assert result.returncode == 0, f"gcc failed for {out.name}: {result.stderr}"


def _compare_scenario(directory: Path, scenario: _Scenario) -> dict:
    old_so = directory / "libold.so"
    new_so = directory / "libnew.so"
    soname = "-Wl,-soname,libx.so.1"
    old_flags = [soname]
    new_flags = [soname]
    if scenario.old_version_script:
        script = directory / "old.map"
        script.write_text(scenario.old_version_script, encoding="utf-8")
        old_flags.append(f"-Wl,--version-script={script}")
    if scenario.new_version_script:
        script = directory / "new.map"
        script.write_text(scenario.new_version_script, encoding="utf-8")
        new_flags.append(f"-Wl,--version-script={script}")
    _compile(scenario.old_src, old_so, tuple(old_flags))
    _compile(scenario.new_src, new_so, tuple(new_flags))

    args = ["compare", str(old_so), str(new_so)]
    if scenario.old_header is not None and scenario.new_header is not None:
        old_h = directory / "old.h"
        new_h = directory / "new.h"
        old_h.write_text(scenario.old_header, encoding="utf-8")
        new_h.write_text(scenario.new_header, encoding="utf-8")
        args += ["--header", f"old={old_h}", "--header", f"new={new_h}"]
    out_json = directory / "report.json"
    args += ["-o", f"json={out_json}"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 4, result.output
    return json.loads(out_json.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_each_removed_symbol_has_exactly_one_breaking_finding(
    tmp_path: Path, name: str
) -> None:
    scenario = SCENARIOS[name]
    report = _compare_scenario(tmp_path, scenario)
    breaking = Counter(
        c["symbol"] for c in report["changes"] if c["severity"] == "breaking"
    )
    assert breaking == Counter(scenario.expected_breaking), (
        f"{name}: breaking findings per symbol {dict(breaking)} "
        f"!= expected {scenario.expected_breaking}"
    )


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_soname_advisory_counts_distinct_removed_symbols(
    tmp_path: Path, name: str
) -> None:
    scenario = SCENARIOS[name]
    report = _compare_scenario(tmp_path, scenario)
    advisories = [
        c for c in report["changes"] if c["kind"] == "soname_bump_recommended"
    ]
    expected_count = len(scenario.expected_breaking)
    assert len(advisories) == 1, f"{name}: expected one SONAME advisory"
    assert advisories[0]["description"].startswith(
        f"{expected_count} binary-incompatible change(s)"
    ), f"{name}: advisory was {advisories[0]['description']!r}"


def test_dropped_duplicate_stays_in_disposition_audit(tmp_path: Path) -> None:
    """Dropping the ELF-only duplicate is a recorded disposition, not a silent one."""
    report = _compare_scenario(tmp_path, SCENARIOS["func_hidden_with_header"])
    assert report["disposition_audit"]["counts"]["deduplicated"] == 1
