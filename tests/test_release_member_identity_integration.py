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

"""A release pairs one library across every storage form of either side.

Known gap "Release fan-out cannot pair a stored snapshot directory with a
live one": a directory of stored dumps (``libalpha.json``) against the live
directory they were dumped from paired nothing and exited 1. The class is
"a member's identity depends on how its side is stored", so this crosses
every OLD form with every NEW form -- live binaries, stored dumps under
three naming schemes (the library's stem, an unrelated name, the dump's
default compressed name), a stored ``ProjectSnapshot`` package, and (OLD
only) a ``BundleFacts`` document -- over three gcc-built libraries with
versioned and unversioned names and three different outcomes.

Oracle: the live-directory/live-directory run of the same binaries. Every
cell must pair all three libraries (no unmatched member) and reach its
verdicts and exit code. A wrong pairing moves a verdict, because the three
libraries' outcomes (break, addition, no change) differ.
"""

from __future__ import annotations

import itertools
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.project_snapshot_store import DirectoryObjectStore, write_project_manifest
from abicheck.serialization import load_snapshot
from abicheck.storage.bundle_facts_package import write_bundle_facts_package
from abicheck.workflows.bundle_facts_capture import capture_bundle_facts

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="builds ELF shared objects"),
    pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc required"),
]

#: file name -> (OLD source, NEW source). Break, addition, no change.
_LIBRARIES: dict[str, tuple[str, str]] = {
    "libalpha.so.1": (
        "int alpha(void){return 1;}\nint alpha_gone(void){return 2;}\n",
        "int alpha(void){return 1;}\n",
    ),
    "libbeta.so.2.3": (
        "int beta(int x){return x;}\n",
        "int beta(int x){return x;}\nint beta_new(int x){return x+1;}\n",
    ),
    "libgamma.so": ("int gamma_(void){return 3;}\n", "int gamma_(void){return 3;}\n"),
}

#: How a stored dump of ``libalpha.so.1`` is named, per form.
_STORED_NAMES = {
    "stored_stem": lambda lib, i: f"{lib.split('.', 1)[0]}.json",
    "stored_unrelated": lambda lib, i: f"member-{i}.json",
    "stored_gzip": lambda lib, i: f"{lib}.abicheck.json.gz",
}
OLD_FORMS = ("live", *_STORED_NAMES, "package", "bundle_facts")
NEW_FORMS = ("live", *_STORED_NAMES, "package")


def _invoke(argv: list[str]) -> tuple[int, str]:
    result = CliRunner().invoke(main, argv)
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result.exit_code, result.output


@pytest.fixture(scope="module")
def operands(tmp_path_factory: pytest.TempPathFactory) -> dict[tuple[str, str], Path]:
    """``(side, form) -> operand``, each built once per module."""
    root = tmp_path_factory.mktemp("member_identity")
    built: dict[tuple[str, str], Path] = {}
    for side_index, side in enumerate(("old", "new")):
        live = root / side / "live"
        live.mkdir(parents=True)
        for name, sources in _LIBRARIES.items():
            src = root / side / f"{name}.c"
            src.write_text(sources[side_index], encoding="utf-8")
            subprocess.run(
                ["gcc", "-shared", "-fPIC", "-g", "-o", str(live / name), str(src)],
                check=True,
                capture_output=True,
                text=True,
            )
        built[side, "live"] = live
        dumps: dict[str, Path] = {}
        for form, namer in _STORED_NAMES.items():
            directory = root / side / form
            directory.mkdir()
            for i, name in enumerate(_LIBRARIES):
                out = directory / namer(name, i)
                code, output = _invoke(["dump", str(live / name), "-o", str(out)])
                assert code == 0, output
                dumps.setdefault(name, out)
            built[side, form] = directory
        package = root / side / "package"
        facts = capture_bundle_facts(
            {name: load_snapshot(path) for name, path in dumps.items()},
            variant_fingerprint="identity-test",
        )
        facts = type(facts)(
            variant_fingerprint=facts.variant_fingerprint,
            per_library_snapshots=facts.per_library_snapshots,
            manifest=facts.manifest,
            filesystem_aliases=facts.filesystem_aliases,
            library_filenames={name: name for name in facts.per_library_snapshots},
        )
        write_project_manifest(
            package,
            write_bundle_facts_package(facts, store=DirectoryObjectStore(package)),
        )
        built[side, "package"] = package
        if side == "old":
            document = root / "old.bundle-facts.json"
            code, output = _invoke(
                ["dump", str(live), "--bundle-facts-out", str(document)]
            )
            assert code == 0, output
            built[side, "bundle_facts"] = document
    return built


def _run(old: Path, new: Path, out: Path) -> tuple[int, dict]:
    code, output = _invoke(["compare", str(old), str(new), "-o", f"json={out}"])
    assert out.is_file(), output
    return code, json.loads(out.read_text(encoding="utf-8"))


def _decision(code: int, report: dict) -> dict:
    return {
        "exit": code,
        "verdicts": sorted(entry["verdict"] for entry in report["libraries"]),
        "unmatched_old": report["unmatched_old"],
        "unmatched_new": report["unmatched_new"],
        "operational": report["run_outcome"]["operational"],
    }


@pytest.fixture(scope="module")
def reference(
    operands: dict[tuple[str, str], Path], tmp_path_factory: pytest.TempPathFactory
) -> dict:
    out = tmp_path_factory.mktemp("reference") / "live.json"
    decision = _decision(*_run(operands["old", "live"], operands["new", "live"], out))
    # The oracle itself must be the three-way outcome, or nothing is tested.
    assert decision["verdicts"] == ["BREAKING", "COMPATIBLE", "NO_CHANGE"]
    assert decision["unmatched_old"] == decision["unmatched_new"] == []
    return decision


@pytest.mark.parametrize(
    ("old_form", "new_form"),
    [
        cell
        for cell in itertools.product(OLD_FORMS, NEW_FORMS)
        if cell != ("live", "live")
    ],
)
def test_every_storage_form_pairs_every_library(
    operands: dict[tuple[str, str], Path],
    reference: dict,
    tmp_path: Path,
    old_form: str,
    new_form: str,
) -> None:
    got = _decision(
        *_run(operands["old", old_form], operands["new", new_form], tmp_path / "r.json")
    )

    assert got == reference
