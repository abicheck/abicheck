# Copyright 2026 Nikolay Petrov
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

"""Tests for ``buildsource.inputs_validate`` and the ``abicheck inputs validate``
CLI command (ADR-038 C.8, recommendation #28)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.buildsource import SourceAbiTu, SourceEntity, SourceLocation
from abicheck.buildsource.inputs_pack import ABICHECK_INPUTS_VERSION, INPUTS_KIND
from abicheck.buildsource.inputs_validate import validate_inputs_pack
from abicheck.buildsource.source_abi import default_fact_set
from abicheck.cli import main


def _tu(
    name: str, *, fact_set: dict | None = None, coverage: dict | None = None
) -> SourceAbiTu:
    ent = SourceEntity(
        id=f"decl://{name}",
        kind="function",
        qualified_name=name,
        mangled_name=f"_Z{len(name)}{name}v",
        signature_hash="sig1",
        source_location=SourceLocation(
            path=f"include/{name}.h", line=3, origin="PUBLIC_HEADER"
        ),
        visibility="public_header",
    )
    return SourceAbiTu(
        tu_id=f"cu://src/{name}.cpp#cfg:abc",
        target_id="target://libfoo",
        source=f"src/{name}.cpp",
        public_header_roots=[f"include/{name}.h"],
        functions=[ent],
        fact_set=fact_set or {},
        coverage=coverage or {},
    )


def _write_pack(
    root: Path, tus: list[SourceAbiTu], *, manifest_extra: dict | None = None
) -> Path:
    pack = root / "abicheck_inputs"
    (pack / "source_facts").mkdir(parents=True)
    lines = "\n".join(json.dumps(t.to_dict()) for t in tus)
    (pack / "source_facts" / "libfoo.jsonl").write_text(lines + "\n", encoding="utf-8")
    manifest = {
        "kind": INPUTS_KIND,
        "abicheck_inputs_version": ABICHECK_INPUTS_VERSION,
        "library": "libfoo.so",
        "version": "1.0",
        "created_by": "abicheck-clang-plugin 0.4",
    }
    if manifest_extra:
        manifest.update(manifest_extra)
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return pack


# -- validate_inputs_pack -----------------------------------------------------


def test_clean_pack_reports_no_issues(tmp_path: Path) -> None:
    fs = default_fact_set(producer="abicheck-clang-plugin", producer_version="0.4")
    tu = _tu("a", fact_set=fs, coverage={"functions": "complete"})
    pack = _write_pack(tmp_path, [tu])
    report = validate_inputs_pack(pack)
    assert report.ok
    assert report.errors == []
    assert report.tu_count == 1
    assert report.fact_set == fs


def test_missing_manifest_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        validate_inputs_pack(tmp_path / "nope")


def test_wrong_kind_manifest_raises_value_error(tmp_path: Path) -> None:
    pack = tmp_path / "not_inputs"
    pack.mkdir()
    (pack / "manifest.json").write_text(json.dumps({"build_source_pack_version": 1}))
    with pytest.raises(ValueError):
        validate_inputs_pack(pack)


def test_empty_pack_warns(tmp_path: Path) -> None:
    pack = _write_pack(tmp_path, [])
    report = validate_inputs_pack(pack)
    assert report.ok  # a warning, not an error
    assert report.tu_count == 0
    assert any("zero readable TU" in w for w in report.warnings)


def test_duplicate_tu_id_is_an_error(tmp_path: Path) -> None:
    fs = default_fact_set(producer="p", producer_version="1")
    tu1 = _tu("a", fact_set=fs)
    tu2 = _tu("a", fact_set=fs)
    tu2.tu_id = tu1.tu_id  # force a collision the race-free filename should prevent
    pack = _write_pack(tmp_path, [tu1, tu2])
    report = validate_inputs_pack(pack)
    assert not report.ok
    assert tu1.tu_id in report.duplicate_tu_ids
    assert any("duplicate tu_id" in e for e in report.errors)


def test_no_fact_set_anywhere_warns_not_errors(tmp_path: Path) -> None:
    pack = _write_pack(tmp_path, [_tu("a")])
    report = validate_inputs_pack(pack)
    assert report.ok
    assert any("no fact_set identity" in w for w in report.warnings)


def test_mismatched_fact_set_version_is_an_error(tmp_path: Path) -> None:
    bad_fs = default_fact_set(producer="p", producer_version="1")
    bad_fs["version"] = 999
    tu = _tu("a", fact_set=bad_fs)
    pack = _write_pack(tmp_path, [tu])
    report = validate_inputs_pack(pack)
    assert not report.ok
    assert any("fact_set version is 999" in e for e in report.errors)


def test_manifest_level_fact_set_used_when_present(tmp_path: Path) -> None:
    fs = default_fact_set(producer="abicheck-clang-plugin", producer_version="0.4")
    tu = _tu("a")  # no per-TU fact_set
    pack = _write_pack(tmp_path, [tu], manifest_extra={"fact_set": fs})
    report = validate_inputs_pack(pack)
    assert report.fact_set == fs
    assert report.ok


def test_manifest_level_fact_set_does_not_mask_tu_disagreement(tmp_path: Path) -> None:
    """A manifest-declared fact_set must not paper over TUs that disagree with
    each other (or with it) — the manifest cannot know what every TU later
    reported (Codex review)."""
    fs = default_fact_set(producer="abicheck-clang-plugin", producer_version="0.4")
    other_fs = default_fact_set(
        producer="abicheck-clang-plugin", producer_version="0.5"
    )
    tu1 = _tu("a", fact_set=fs)
    tu2 = _tu("b", fact_set=other_fs)
    pack = _write_pack(tmp_path, [tu1, tu2], manifest_extra={"fact_set": fs})
    report = validate_inputs_pack(pack)
    assert report.fact_set == {}
    assert any(
        "do not agree on a single fact_set identity" in w for w in report.warnings
    )
    # Only the disagreement warning fires, not also the generic "no fact_set
    # identity found" one (they'd be redundant/confusing together).
    assert not any("no fact_set identity found" in w for w in report.warnings)


def test_manifest_level_fact_set_used_when_no_tu_ever_stamped_one(
    tmp_path: Path,
) -> None:
    """When no TU carries a fact_set at all (a plain pre-C.8 producer for the
    per-TU records, but the manifest itself is current), the manifest value is
    still usable — this is not the "TUs disagree" case."""
    fs = default_fact_set(producer="abicheck-clang-plugin", producer_version="0.4")
    tu1 = _tu("a")
    tu2 = _tu("b")
    pack = _write_pack(tmp_path, [tu1, tu2], manifest_extra={"fact_set": fs})
    report = validate_inputs_pack(pack)
    assert report.fact_set == fs
    assert report.ok


def test_incomplete_mandatory_family_warns(tmp_path: Path) -> None:
    fs = default_fact_set(producer="p", producer_version="1")
    tu = _tu("a", fact_set=fs, coverage={"functions": "complete", "macros": "partial"})
    pack = _write_pack(tmp_path, [tu])
    report = validate_inputs_pack(pack)
    assert report.ok
    assert report.incomplete_families == ["macros"]
    assert any("macros" in w for w in report.warnings)


def test_empty_public_surface_warns(tmp_path: Path) -> None:
    # A TU with no public entities at all (bury the function under a private
    # header origin) links to an empty surface.
    ent = SourceEntity(
        id="decl://a",
        kind="function",
        qualified_name="a",
        mangled_name="_Z1av",
        visibility="private_header",
        api_relevant=False,
        source_location=SourceLocation(path="src/a.h", line=1, origin="PRIVATE_HEADER"),
    )
    tu = SourceAbiTu(
        tu_id="cu://src/a.cpp#cfg:abc", target_id="target://libfoo", functions=[ent]
    )
    pack = _write_pack(tmp_path, [tu])
    report = validate_inputs_pack(pack)
    assert any("empty public surface" in w for w in report.warnings)


def test_macro_only_pack_is_not_reported_as_empty_surface(tmp_path: Path) -> None:
    """A macro-only (or header-only) pack's public evidence lands in
    reachable_macros, not reachable_declarations/reachable_types — checking
    only the latter two would false-warn on a genuinely non-empty pack
    (Codex review)."""
    macro = SourceEntity(
        id="macro://FOO",
        kind="macro",
        qualified_name="FOO",
        value="1",
        visibility="public_header",
        source_location=SourceLocation(
            path="include/a.h", line=1, origin="PUBLIC_HEADER"
        ),
    )
    tu = SourceAbiTu(
        tu_id="cu://src/a.cpp#cfg:abc",
        target_id="target://libfoo",
        public_header_roots=["include/a.h"],
        macros=[macro],
    )
    pack = _write_pack(tmp_path, [tu])
    report = validate_inputs_pack(pack)
    assert not any("empty public surface" in w for w in report.warnings)


def test_report_to_dict_round_trips_shape(tmp_path: Path) -> None:
    pack = _write_pack(
        tmp_path,
        [_tu("a", fact_set=default_fact_set(producer="p", producer_version="1"))],
    )
    report = validate_inputs_pack(pack)
    d = report.to_dict()
    assert set(d) == {
        "root",
        "ok",
        "errors",
        "warnings",
        "tu_count",
        "duplicate_tu_ids",
        "incomplete_families",
        "fact_set",
    }


# -- CLI: `abicheck inputs validate` ------------------------------------------


def test_cli_validate_clean_pack_exits_zero(tmp_path: Path) -> None:
    fs = default_fact_set(producer="p", producer_version="1")
    pack = _write_pack(
        tmp_path, [_tu("a", fact_set=fs, coverage={"functions": "complete"})]
    )
    result = CliRunner().invoke(main, ["inputs", "validate", str(pack)])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output


def test_cli_validate_warnings_exit_one(tmp_path: Path) -> None:
    pack = _write_pack(tmp_path, [_tu("a")])  # no fact_set -> warning
    result = CliRunner().invoke(main, ["inputs", "validate", str(pack)])
    assert result.exit_code == 1, result.output
    assert "WARNING" in result.output


def test_cli_validate_errors_exit_two(tmp_path: Path) -> None:
    bad_fs = default_fact_set(producer="p", producer_version="1")
    bad_fs["version"] = 999
    pack = _write_pack(tmp_path, [_tu("a", fact_set=bad_fs)])
    result = CliRunner().invoke(main, ["inputs", "validate", str(pack)])
    assert result.exit_code == 2, result.output
    assert "ERROR" in result.output


def test_cli_validate_bad_path_exits_usage_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["inputs", "validate", str(tmp_path / "nope")])
    assert result.exit_code == 64, result.output


def test_cli_validate_json_format(tmp_path: Path) -> None:
    fs = default_fact_set(producer="p", producer_version="1")
    pack = _write_pack(
        tmp_path, [_tu("a", fact_set=fs, coverage={"functions": "complete"})]
    )
    result = CliRunner().invoke(
        main, ["inputs", "validate", str(pack), "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["tu_count"] == 1


def test_cli_validate_writes_to_output_file(tmp_path: Path) -> None:
    fs = default_fact_set(producer="p", producer_version="1")
    pack = _write_pack(
        tmp_path, [_tu("a", fact_set=fs, coverage={"functions": "complete"})]
    )
    out = tmp_path / "report.txt"
    result = CliRunner().invoke(main, ["inputs", "validate", str(pack), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "OK" in out.read_text(encoding="utf-8")
