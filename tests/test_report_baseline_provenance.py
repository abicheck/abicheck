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

"""``report.baseline_provenance``: the caller-asserted baseline record in the
compare (schema 6.8) and release (schema 2.8) JSON reports.

The invariant: whatever route a two-operand ``compare`` takes, a run given a
provenance file states exactly that file's values in its JSON report, marked
``asserted_by: action``; a run given none carries no block; and a file the
block could not honestly carry is refused before analysis (exit 64), never
dropped. The oracle is the input document itself and the published JSON
Schema, not the reader under test.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from schema_validation import validate_instance

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.baseline_provenance import (
    BASELINE_PROVENANCE_SOURCES,
    BaselineProvenance,
    BaselineProvenanceError,
)
from abicheck.schemas import (
    RELEASE_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    load_compare_report_schema,
)
from abicheck.serialization import snapshot_to_json

_FIELDS = ("source", "asset_name", "project_ref", "generation", "profile")


def _snapshot(version: str, *names: str) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so",
        version=version,
        functions=[
            Function(name=n, mangled=n, return_type="int", visibility=Visibility.PUBLIC)
            for n in names
        ],
    )


def _pair(root: Path) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    old, new = root / "old.json", root / "new.json"
    old.write_text(snapshot_to_json(_snapshot("1", "f", "g")), encoding="utf-8")
    new.write_text(snapshot_to_json(_snapshot("2", "f")), encoding="utf-8")
    return old, new


def _release_dirs(root: Path) -> tuple[Path, Path]:
    old_dir, new_dir = root / "old", root / "new"
    for side, snap in (
        (old_dir, _snapshot("1", "f", "g")),
        (new_dir, _snapshot("2", "f")),
    ):
        side.mkdir(parents=True)
        (side / "libtest.abicheck.json").write_text(
            snapshot_to_json(snap), encoding="utf-8"
        )
    return old_dir, new_dir


def _documents() -> list[dict[str, object]]:
    """Every source with every optional field absent or present."""
    values: dict[str, object] = {
        "asset_name": "abicheck-baseline-release.tar.zst",
        "project_ref": "v1.2.0",
        "generation": 3,
        "profile": "release",
    }
    docs = []
    for source in BASELINE_PROVENANCE_SOURCES:
        for mask in itertools.product((False, True), repeat=len(values)):
            doc: dict[str, object] = {"source": source}
            for present, (key, value) in zip(mask, values.items(), strict=True):
                if present:
                    doc[key] = value
            docs.append(doc)
    return docs


def _expected_block(doc: dict[str, object]) -> dict[str, object]:
    return {
        "asserted_by": "action",
        "applies_to": "baseline_operand",
        **{key: doc.get(key) for key in _FIELDS},
    }


# ── the record ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("doc", _documents())
def test_every_valid_document_round_trips_into_the_block(
    doc: dict[str, object],
) -> None:
    block = BaselineProvenance.from_mapping(doc).to_report_dict()
    assert block == _expected_block(doc)
    # The block alone validates against the published schema's definition.
    schema = load_compare_report_schema()
    validate_instance(
        block, schema["properties"]["baseline_provenance"] | {"$defs": schema["$defs"]}
    )


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        ([], "JSON object"),
        ({}, "source must be"),
        ({"source": "release"}, "source must be"),
        ({"source": "baseline-set", "projectref": "v1"}, "unknown key"),
        ({"source": "baseline-set", "asserted_by": "me"}, "unknown key"),
        ({"source": "baseline-set", "generation": -1}, "generation"),
        ({"source": "baseline-set", "generation": True}, "generation"),
        ({"source": "baseline-set", "generation": "3"}, "generation"),
        ({"source": "baseline-set", "generation": 1.5}, "generation"),
        ({"source": "baseline-set", "profile": 7}, "profile"),
        ({"source": "baseline-set", "asset_name": ["a"]}, "asset_name"),
        ({"source": "baseline-set", "project_ref": {"a": 1}}, "project_ref"),
    ],
)
def test_a_malformed_document_is_refused(doc: object, message: str) -> None:
    with pytest.raises(BaselineProvenanceError, match=message):
        BaselineProvenance.from_mapping(doc)


def test_empty_strings_read_as_absent() -> None:
    # The Action spells an unset output as "".
    doc = {
        "source": "legacy-asset",
        "asset_name": "a.json",
        "project_ref": "",
        "generation": "",
        "profile": "",
    }
    block = BaselineProvenance.from_mapping(doc).to_report_dict()
    assert (block["project_ref"], block["generation"], block["profile"]) == (
        None,
        None,
        None,
    )


# ── the CLI, every route ─────────────────────────────────────────────────


_DOC = {
    "source": "baseline-set",
    "asset_name": "abicheck-baseline-release.tar.zst",
    "project_ref": "v1.2.0",
    "generation": 0,
    "profile": "release",
}


@pytest.fixture
def _compare(monkeypatch: pytest.MonkeyPatch):
    """Run ``compare`` with the current directory set to *cwd* (``--set`` paths
    resolve against it)."""

    def run(args: list[str], cwd: Path) -> tuple[int, str]:
        monkeypatch.chdir(cwd)
        result = CliRunner().invoke(main, ["compare", *args])
        return result.exit_code, result.output

    return run


@pytest.mark.parametrize("spelling", ["@prov.json", "prov.json"])
@pytest.mark.parametrize("route", ["pair", "release"])
def test_set_states_the_file_in_the_json_report(
    tmp_path: Path, _compare, route: str, spelling: str
) -> None:
    (tmp_path / "prov.json").write_text(json.dumps(_DOC), encoding="utf-8")
    old, new = (
        _pair(tmp_path / "in") if route == "pair" else _release_dirs(tmp_path / "in")
    )
    out = tmp_path / "report.json"
    code, output = _compare(
        [
            str(old),
            str(new),
            "--set",
            f"report.baseline_provenance={spelling}",
            "-o",
            f"json={out}",
        ],
        tmp_path,
    )
    assert code in (0, 4), output
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["baseline_provenance"] == _expected_block(_DOC)
    if route == "pair":
        assert report["report_schema_version"] == REPORT_SCHEMA_VERSION
        validate_instance(report, load_compare_report_schema())
    else:
        assert report["release_schema_version"] == RELEASE_SCHEMA_VERSION


@pytest.mark.parametrize("route", ["pair", "release"])
def test_no_key_means_no_block(tmp_path: Path, _compare, route: str) -> None:
    old, new = (
        _pair(tmp_path / "in") if route == "pair" else _release_dirs(tmp_path / "in")
    )
    out = tmp_path / "report.json"
    code, output = _compare([str(old), str(new), "-o", f"json={out}"], tmp_path)
    assert code in (0, 4), output
    assert "baseline_provenance" not in json.loads(out.read_text(encoding="utf-8"))


def test_config_file_path_resolves_against_the_config_directory(
    tmp_path: Path, _compare
) -> None:
    cfg_dir = tmp_path / "proj"
    cfg_dir.mkdir()
    (cfg_dir / "prov.json").write_text(json.dumps(_DOC), encoding="utf-8")
    cfg = cfg_dir / ".abicheck.yml"
    cfg.write_text("report:\n  baseline_provenance: prov.json\n", encoding="utf-8")
    old, new = _pair(tmp_path / "in")
    out = tmp_path / "report.json"
    code, output = _compare(
        [str(old), str(new), "--config", str(cfg), "-o", f"json={out}"], tmp_path
    )
    assert code in (0, 4), output
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["baseline_provenance"]["project_ref"] == "v1.2.0"


@pytest.mark.parametrize("route", ["pair", "release"])
@pytest.mark.parametrize(
    "content",
    ["not json", json.dumps({"source": "nope"}), json.dumps(["baseline-set"]), None],
)
def test_an_unusable_file_is_a_usage_error_before_analysis(
    tmp_path: Path, _compare, route: str, content: str | None
) -> None:
    if content is not None:
        (tmp_path / "prov.json").write_text(content, encoding="utf-8")
    old, new = (
        _pair(tmp_path / "in") if route == "pair" else _release_dirs(tmp_path / "in")
    )
    out = tmp_path / "report.json"
    code, output = _compare(
        [
            str(old),
            str(new),
            "--set",
            "report.baseline_provenance=@prov.json",
            "-o",
            f"json={out}",
        ],
        tmp_path,
    )
    assert code == 64, output
    assert "report.baseline_provenance" in output
    assert not out.exists()


def test_no_baseline_refuses_the_key(tmp_path: Path, _compare) -> None:
    (tmp_path / "prov.json").write_text(json.dumps(_DOC), encoding="utf-8")
    _, new = _pair(tmp_path / "in")
    code, output = _compare(
        [str(new), "--no-baseline", "--set", "report.baseline_provenance=@prov.json"],
        tmp_path,
    )
    assert code == 64, output
    assert "report.baseline_provenance is not available with --no-baseline" in output
