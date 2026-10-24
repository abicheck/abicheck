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

"""Live and stored release operands render the same verdict through the CLI.

The same old/new release pair is compared twice: once as two directories
(the live fan-out) and once with the old side as a stored ``BundleFacts``
document (the stored operand). For each format both runs must agree on the
exit code, the verdict and the member list. The oracles are the fixtures
themselves (a compatible addition exits 0, a signature break does not), not
either renderer's output.

The shared fixtures build real ELF shared objects with ``gcc``, so the module
is ``integration`` and Linux-only, like its sibling
``test_cli_compare_bundle_facts.py``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.cli_resolve import _resolve_input
from abicheck.serialization import save_bundle_facts
from abicheck.workflows.bundle_facts_capture import capture_bundle_facts

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "linux",
        reason="Builds ELF shared objects with GNU ld's -Wl,-soname (Linux only).",
    ),
]

_V1_ADD = "int add(int a, int b) { return a + b; }\n"
_V1_ADD_PLUS_SUB = _V1_ADD + "int sub(int a, int b) { return a - b; }\n"
_V2_ADD3 = "int add(int a, int b, int c) { return a + b + c; }\n"

#: name -> (old members, new members). Member bodies are C sources; a key
#: present only on the old side is a removed member.
_CASES = {
    "compatible": (
        {"libcalc.so": _V1_ADD},
        {"libcalc.so": _V1_ADD_PLUS_SUB},
    ),
    "breaking": (
        {"libcalc.so": _V1_ADD},
        {"libcalc.so": _V2_ADD3},
    ),
    "removed_member": (
        {"libcalc.so": _V1_ADD, "libgone.so": _V1_ADD_PLUS_SUB},
        {"libcalc.so": _V1_ADD},
    ),
}

#: The oracle for each fixture's exit, independent of either renderer. A
#: member missing from NEW is unmatched, not removed (ADR-065): without
#: inventory evidence it is accepted under the default ``scope.on_incomplete:
#: warn``, so that fixture exits 0 while its scope note stays visible.
_EXPECTED_EXIT_NONZERO = {
    "compatible": False,
    "breaking": True,
    "removed_member": False,
}


def _build_so(directory: Path, name: str, body: str) -> Path:
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc is not available")
    src = directory / f"{name}.c"
    src.write_text(body)
    out = directory / name
    res = subprocess.run(
        [
            gcc,
            "-shared",
            "-fPIC",
            "-g",
            "-O0",
            str(src),
            "-o",
            str(out),
            f"-Wl,-soname,{name}",
        ],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        pytest.fail(f"gcc failed: {res.stderr}")
    return out


def _release_dirs(
    tmp_path: Path, old_members: dict[str, str], new_members: dict[str, str]
) -> tuple[Path, Path]:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    for directory, members in ((old_dir, old_members), (new_dir, new_members)):
        for name, body in members.items():
            _build_so(directory, name, body)
    return old_dir, new_dir


def _stored_old_facts(tmp_path: Path, old_dir: Path, members: dict[str, str]) -> Path:
    """The old release's stored ``BundleFacts`` -- every member captured the
    way ``test_cli_compare_bundle_facts.py`` captures a single one."""
    snapshots = {
        name: _resolve_input(
            old_dir / name, [], [], "old", "c++", include_dependencies=True
        )
        for name in members
    }
    facts_path = tmp_path / "old.bundlefacts.json"
    save_bundle_facts(capture_bundle_facts(snapshots), facts_path)
    return facts_path


def _run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.stdout


def _junit_cases(xml_text: str) -> list[tuple[str, str, bool]]:
    """Every testcase as ``(suite, name, failed)``, in document order."""
    root = ET.fromstring(xml_text)
    cases: list[tuple[str, str, bool]] = []
    for suite in root.iter("testsuite"):
        for case in suite.findall("testcase"):
            failed = any(child.tag in ("failure", "error") for child in case)
            cases.append((suite.get("name", ""), case.get("name", ""), failed))
    return cases


def _suite_names(xml_text: str) -> set[str]:
    """Every ``<testsuite>`` name, including a suite with no testcases: a
    member with nothing to report still has a suite, and reading members off
    testcases would silently drop it."""
    root = ET.fromstring(xml_text)
    return {suite.get("name", "") for suite in root.iter("testsuite")}


@pytest.mark.parametrize("case", sorted(_CASES))
def test_live_and_stored_operands_agree_through_the_cli(
    case: str, tmp_path: Path
) -> None:
    old_members, new_members = _CASES[case]
    old_dir, new_dir = _release_dirs(tmp_path, old_members, new_members)
    facts_path = _stored_old_facts(tmp_path, old_dir, old_members)

    live_code, live_oneline = _run(
        "compare", str(old_dir), str(new_dir), "-o", "oneline=-"
    )
    stored_code, stored_oneline = _run(
        "compare", str(facts_path), str(new_dir), "-o", "oneline=-"
    )

    assert live_code == stored_code
    assert live_code != 64, live_oneline
    assert live_oneline.strip() == stored_oneline.strip()
    assert (live_code != 0) == _EXPECTED_EXIT_NONZERO[case]


@pytest.mark.parametrize("case", sorted(_CASES))
def test_live_and_stored_junit_agree_on_verdict_and_members(
    case: str, tmp_path: Path
) -> None:
    old_members, new_members = _CASES[case]
    old_dir, new_dir = _release_dirs(tmp_path, old_members, new_members)
    facts_path = _stored_old_facts(tmp_path, old_dir, old_members)

    live_code, live_xml = _run("compare", str(old_dir), str(new_dir), "-o", "junit=-")
    stored_code, stored_xml = _run(
        "compare", str(facts_path), str(new_dir), "-o", "junit=-"
    )

    assert live_code == stored_code
    live_cases = _junit_cases(live_xml)
    stored_cases = _junit_cases(stored_xml)
    # Members: the same set of library suites on both operands.
    assert _suite_names(live_xml) == _suite_names(stored_xml)
    # Findings: the same failing testcases, by suite and name.
    failing = lambda cases: sorted(  # noqa: E731
        (suite, name) for suite, name, failed in cases if failed
    )
    assert failing(live_cases) == failing(stored_cases)
    assert bool(failing(stored_cases)) == _EXPECTED_EXIT_NONZERO[case]


def test_stored_oneline_names_the_stored_verdict(tmp_path: Path) -> None:
    """Oracle for the parity above: a stored breaking comparison says so in
    its own words, not only as an agreement with the live run."""
    old_members, new_members = _CASES["breaking"]
    old_dir, new_dir = _release_dirs(tmp_path, old_members, new_members)
    facts_path = _stored_old_facts(tmp_path, old_dir, old_members)

    _code, oneline = _run("compare", str(facts_path), str(new_dir), "-o", "oneline=-")

    assert re.search(r"— BREAKING\b", oneline), oneline
