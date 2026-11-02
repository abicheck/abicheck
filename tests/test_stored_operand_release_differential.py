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

"""Differential harness: a release compare decides the same whatever form
each operand takes.

Every corpus pair is compared with every operand-form combination
{live directory, stored-snapshot directory, stored ``BundleFacts``} x
{old, new} that the CLI accepts. The oracle is the live/live run of the same
pair: every other cell must reproduce its exit code, its release verdict and
its per-member (verdict, breaking, compatible-addition) rows, keyed by the
member's canonical library identity -- a stored operand records a different
file name for the same library, which is operand provenance, not a result.

The corpus crosses versioned and unversioned member names with a
compatible, a breaking and a two-member change, because the divergences this
pins were all member-identity bugs:

* a stored ``BundleFacts`` keyed ``libX.so.1`` against a live directory keyed
  ``libX.so`` paired nothing (known gap: a stored BundleFacts operand pairs
  versioned member names on one side only);
* a stored ``libX.json`` directory against a live ``libX.so`` directory paired
  nothing (known gap: release fan-out cannot pair a stored snapshot directory
  with a live one).

Bug class ``evidence.stored_live_route_member_divergence``. Needs gcc, so the
module is ``integration`` and Linux-only.
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

from abicheck.binary_utils import _canonical_library_key
from abicheck.cli import main
from abicheck.serialization import load_snapshot, save_bundle_facts
from abicheck.workflows.bundle_facts_capture import capture_bundle_facts

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "linux", reason="Builds ELF shared objects with gcc."
    ),
]

_ADD = "int add(int a, int b) { return a + b; }\n"
_ADD_SUB = _ADD + "int sub(int a, int b) { return a - b; }\n"
_ADD3 = "int add(int a, int b, int c) { return a + b + c; }\n"
_MUL = "int mul(int a, int b) { return a * b; }\n"
_MUL_DIV = _MUL + "int dv(int a, int b) { return a / b; }\n"

#: change -> {member stem: (old body, new body)}
_CHANGES = {
    "compatible": {"calc": (_ADD, _ADD_SUB)},
    "breaking": {"calc": (_ADD, _ADD3)},
    "two_members": {"calc": (_ADD, _ADD3), "mul": (_MUL, _MUL_DIV)},
}
#: naming -> file-name suffix of a member
_NAMINGS = {"unversioned": ".so", "versioned": ".so.1"}

_FORMS = ("live", "stored_dir", "bundle_facts")
#: A stored BundleFacts NEW operand is accepted only against a stored
#: BundleFacts OLD operand (stored/stored); every other combination is a cell.
_CELLS = [
    (o, n)
    for o, n in itertools.product(_FORMS, _FORMS)
    if n != "bundle_facts" or o == "bundle_facts"
]


def _build(directory: Path, name: str, body: str) -> Path:
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc is not available")
    directory.mkdir(parents=True, exist_ok=True)
    src = directory.parent / f"{directory.name}-{name}.c"
    src.write_text(body)
    out = directory / name
    subprocess.run(
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
        check=True,
    )
    return out


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.stdout


def _materialize(root: Path, change: str, naming: str) -> dict[str, dict[str, Path]]:
    """Build both sides and every stored form; ``{side: {form: path}}``."""
    forms: dict[str, dict[str, Path]] = {}
    for idx, side in enumerate(("old", "new")):
        live = root / side
        stored = root / f"{side}-stored"
        stored.mkdir(parents=True)
        snaps = {}
        for stem, bodies in _CHANGES[change].items():
            name = f"lib{stem}{_NAMINGS[naming]}"
            so = _build(live, name, bodies[idx])
            # `abicheck dump -o lib<stem>.json`: a snapshot named for its stem,
            # not for the library it represents.
            snap_path = stored / f"lib{stem}.json"
            code, out = _invoke("dump", str(so), "-o", str(snap_path))
            assert code == 0, out
            snaps[name] = load_snapshot(snap_path)
        facts = root / f"{side}.bundlefacts.json"
        save_bundle_facts(capture_bundle_facts(snaps), facts)
        forms[side] = {"live": live, "stored_dir": stored, "bundle_facts": facts}
    return forms


def _member_identity(name: str) -> str:
    """The member's library identity with operand provenance removed: a
    stored-snapshot directory reports its member by the file name
    (``libcalc.json``), a live one by the library (``libcalc.so.1``), and the
    release fan-out pairs both under one canonical key (``libcalc.so``)."""
    key = _canonical_library_key(Path(name))
    return key.removesuffix(".json").removesuffix(".so")


def _decision(old: Path, new: Path, out: Path) -> tuple[int, str, dict]:
    code, stdout = _invoke("compare", str(old), str(new), "-o", f"json={out}")
    assert code != 64, stdout
    doc = json.loads(out.read_text(encoding="utf-8"))
    rows = {
        _member_identity(lib["library"]): (
            lib["verdict"],
            lib.get("breaking"),
            lib.get("compatible_additions"),
        )
        for lib in doc["libraries"]
    }
    return code, doc["verdict"], rows


@pytest.mark.parametrize("naming", sorted(_NAMINGS))
@pytest.mark.parametrize("change", sorted(_CHANGES))
def test_every_operand_form_decides_like_live(
    change: str, naming: str, tmp_path: Path
) -> None:
    forms = _materialize(tmp_path, change, naming)
    oracle = _decision(
        forms["old"]["live"], forms["new"]["live"], tmp_path / "live-live.json"
    )
    # The oracle itself compared every member: a zero-comparison oracle
    # would make every cell agree vacuously.
    assert len(oracle[2]) == len(_CHANGES[change]), oracle
    mismatches = {}
    for old_form, new_form in _CELLS:
        if (old_form, new_form) == ("live", "live"):
            continue
        got = _decision(
            forms["old"][old_form],
            forms["new"][new_form],
            tmp_path / f"{old_form}-{new_form}.json",
        )
        if got != oracle:
            mismatches[f"{old_form}/{new_form}"] = got
    assert not mismatches, {"oracle": oracle, **mismatches}
