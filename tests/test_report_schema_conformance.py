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

"""Every report a compare mode emits validates against its published schema.

Found while testing the evidence-entity-model plan (gap C1): a no-baseline
compare emitted ``analysis_assurance.schema_staleness_status: "not_evaluated"``
-- the value the producer has always used when there is no OLD snapshot --
while the schema's enum listed only ``clean``/``degraded``. No test validated
emitted reports against the schema, so the two drifted silently.

Two independent guards:

* **Real reports**: each mode is run through the CLI and its JSON validated
  with ``jsonschema`` against the shipped schema (compare report, or the audit
  report for ``--no-baseline``). Modes needing a toolchain (live binaries, a
  release directory) are ``integration``.
* **Vocabulary**: for every ``analysis_assurance`` field whose schema declares
  an enum, the producer's own value vocabulary
  (``policy.analysis_assurance_merge._AXIS_WORST_LAST``, the ordered scale the
  merge ranks every emitted value on) must equal the enum -- so a value added
  to either side without the other fails here, whether or not any fixture
  happens to emit it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Param, RecordType, TypeField
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol
from abicheck.serialization import save_snapshot

_SCHEMAS = Path(__file__).resolve().parents[1] / "abicheck" / "schemas"


def _schema(name: str) -> dict[str, Any]:
    return json.loads((_SCHEMAS / f"{name}.schema.json").read_text())


def _validate(doc: dict[str, Any], schema: str) -> None:
    validator = jsonschema.Draft202012Validator(_schema(schema))
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    assert not errors, "\n".join(
        f"{'/'.join(map(str, e.path))}: {e.message}" for e in errors[:10]
    )


def _snap(version: str, *, field_type: str, extra_fn: bool) -> AbiSnapshot:
    fns = [
        Function(
            name="f",
            mangled="f",
            return_type="int",
            params=[Param(name="a", type="int")],
        )
    ]
    if extra_fn:
        fns.append(Function(name="g", mangled="g", return_type="void"))
    return AbiSnapshot(
        library="libx.so",
        version=version,
        functions=fns,
        types=[
            RecordType(
                name="S",
                kind="struct",
                size_bits=32,
                fields=[TypeField(name="x", type=field_type, offset_bits=0)],
            )
        ],  # fmt: skip
        elf=ElfMetadata(
            symbols=[ElfSymbol(name=f.mangled) for f in fns], machine="EM_X86_64"
        ),
        platform="elf",
    )


def _run(tmp_path: Path, args: list[str]) -> dict[str, Any]:
    out = tmp_path / "report.json"
    res = CliRunner().invoke(main, [*args, "-o", f"json={out}"])
    assert res.exit_code in (0, 1, 2, 4), res.output
    return json.loads(out.read_text())


def _stored_pair(tmp_path: Path) -> tuple[Path, Path]:
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    save_snapshot(_snap("1", field_type="int", extra_fn=False), old)
    save_snapshot(_snap("2", field_type="long", extra_fn=True), new)
    return old, new


# mode -> (argv builder, schema name). Every entry is a documented compare mode.
MODES: dict[str, tuple[Callable[[Path], list[str]], str]] = {
    "stored_pair": (
        lambda t: ["compare", *map(str, _stored_pair(t))],
        "compare_report",
    ),
    "stored_pair_contract_public": (
        lambda t: ["compare", *map(str, _stored_pair(t)), "--contract", "public"],
        "compare_report",
    ),
    "stored_pair_contract_exports": (
        lambda t: ["compare", *map(str, _stored_pair(t)), "--contract", "exports"],
        "compare_report",
    ),
    "stored_identical": (
        lambda t: ["compare", str(_stored_pair(t)[0]), str(_stored_pair(t)[0])],
        "compare_report",
    ),
    "no_baseline_stored": (
        lambda t: ["compare", "--no-baseline", str(_stored_pair(t)[1])],
        "audit_report",
    ),
}


@pytest.mark.parametrize("mode", sorted(MODES))
def test_every_compare_mode_emits_a_schema_valid_report(
    tmp_path: Path, mode: str
) -> None:
    build, schema = MODES[mode]
    _validate(_run(tmp_path, build(tmp_path)), schema)


def _enum_fields(obj: dict[str, Any]) -> dict[str, set[str]]:
    return {
        name: set(spec["enum"])
        for name, spec in obj.get("properties", {}).items()
        if isinstance(spec, dict) and "enum" in spec
    }


def test_assurance_vocabulary_matches_the_schema_enums() -> None:
    from abicheck.policy.analysis_assurance_merge import _AXIS_WORST_LAST

    enums = _enum_fields(_schema("compare_report")["properties"]["analysis_assurance"])
    checked = 0
    for axis, scale in _AXIS_WORST_LAST.items():
        if axis not in enums:
            continue
        checked += 1
        assert set(scale) == enums[axis], (axis, sorted(set(scale) ^ enums[axis]))
    # Vacuity guard: the comparison really ran over the enumerated fields.
    assert checked >= 1
    assert "schema_staleness_status" in enums


@pytest.mark.xfail(strict=True, reason="gap C1")
def test_audit_and_compare_share_one_assurance_shape() -> None:
    compare = _schema("compare_report")["properties"]["analysis_assurance"]
    audit = _schema("audit_report")["$defs"]["analysis_assurance"]
    assert audit == compare


def test_no_baseline_assurance_really_is_not_evaluated(tmp_path: Path) -> None:
    """The fixture exercises the value that was rejected, not a lucky one."""
    doc = _run(tmp_path, MODES["no_baseline_stored"][0](tmp_path))
    assert doc["run_outcome"]["assurance"]["schema_staleness_status"] == "not_evaluated"


# --- live modes (real toolchain) -------------------------------------------


def _build(tmp_path: Path, name: str, body: str) -> tuple[Path, Path]:
    inc = tmp_path / name / "include"
    inc.mkdir(parents=True)
    (inc / "x.h").write_text(
        "int f(int a);\n" + ("void g(void);\n" if "g" in body else "")
    )
    src = tmp_path / name / "x.c"
    src.write_text(body)
    lib = tmp_path / name / "libx.so"
    subprocess.run(
        ["gcc", "-g", "-shared", "-fPIC", str(src), "-o", str(lib)], check=True
    )
    return lib, inc / "x.h"


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["live_pair", "live_no_baseline", "release_dir"])
def test_live_modes_emit_schema_valid_reports(tmp_path: Path, mode: str) -> None:
    if shutil.which("gcc") is None or shutil.which("castxml") is None:
        pytest.skip("needs gcc and castxml")
    old_lib, old_h = _build(tmp_path, "old", "int f(int a){return a;}\n")
    new_lib, new_h = _build(
        tmp_path, "new", "int f(int a){return a+1;}\nvoid g(void){}\n"
    )
    if mode == "live_pair":
        doc = _run(
            tmp_path,
            [
                "compare",
                str(old_lib),
                str(new_lib),
                "--header",
                f"old={old_h}",
                "--header",
                f"new={new_h}",
            ],
        )
        _validate(doc, "compare_report")
    elif mode == "live_no_baseline":
        doc = _run(
            tmp_path, ["compare", "--no-baseline", str(new_lib), "-H", str(new_h)]
        )
        _validate(doc, "audit_report")
    else:
        doc = _run(tmp_path, ["compare", str(old_lib.parent), str(new_lib.parent)])
        # A release envelope nests one compare report per matched member.
        members = [
            v
            for v in doc.get("libraries", doc.get("members", []))
            if isinstance(v, dict)
        ]
        for member in members:
            report = member.get("report", member)
            if "report_schema_version" in report:
                _validate(report, "compare_report")


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param(
            "no_baseline_stored", marks=pytest.mark.xfail(strict=True, reason="gap C1")
        ),
        "stored_pair",
    ],
)
def test_run_outcome_assurance_validates_against_the_assurance_shape(
    tmp_path: Path, mode: str
) -> None:
    """The reported defect itself: a no-baseline report's assurance block,
    checked against the published ``analysis_assurance`` shape, was rejected
    for ``schema_staleness_status: not_evaluated``."""
    compare = _schema("compare_report")
    sub = dict(compare["properties"]["analysis_assurance"])
    sub["$schema"] = compare["$schema"]
    doc = _run(tmp_path, MODES[mode][0](tmp_path))
    assurance = doc["run_outcome"]["assurance"]
    errors = list(jsonschema.Draft202012Validator(sub).iter_errors(assurance))
    assert not errors, [e.message for e in errors]
