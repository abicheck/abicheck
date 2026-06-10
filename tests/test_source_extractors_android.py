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

"""Tests for the ADR-030 phase-6 Android header-abi adapter.

The normalizer is pure and tested in the fast lane; loading a pre-captured dump
needs only a JSON file on disk (no Android tools).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.evidence.source_abi import SourceAbiTu
from abicheck.evidence.source_diff import diff_source_abi
from abicheck.evidence.source_extractors import (
    AndroidHeaderAbiAdapter,
    SourceExtractionError,
    parse_android_dump,
)
from abicheck.evidence.source_link import link_source_abi


def _dump(record_size: int = 8) -> dict:
    return {
        "source_file": "include/foo.h",
        "record_types": [
            {
                "name": "Foo", "size": record_size, "linker_set_key": "_ZTI3Foo",
                "source_file": "include/foo.h",
                "fields": [{"field_name": "a", "referenced_type": "int", "field_offset": 0}],
            }
        ],
        "enum_types": [
            {"name": "E", "enum_fields": [{"name": "A", "enum_field_value": 0}]}
        ],
        "functions": [
            {
                "function_name": "foo", "linker_set_key": "_Z3foov",
                "return_type": "void", "parameters": [{"referenced_type": "int"}],
            }
        ],
        "global_vars": [{"name": "g", "linker_set_key": "g", "referenced_type": "int"}],
    }


def test_parse_routes_entities_to_buckets() -> None:
    tu = parse_android_dump(_dump(), target_id="target://libfoo")
    assert tu.extractor["name"] == "android-header-abi"
    assert {(e.qualified_name, e.kind) for e in tu.types} == {("Foo", "record"), ("E", "enum")}
    assert [(e.qualified_name, e.mangled_name) for e in tu.functions] == [("foo", "_Z3foov")]
    assert [e.qualified_name for e in tu.variables] == ["g"]
    # Android emits no inline/template bodies or macros (clang's job, phase 5).
    assert tu.inline_bodies == [] and tu.templates == [] and tu.macros == []
    # Round-trips through the normalized schema.
    assert SourceAbiTu.from_dict(tu.to_dict()).tu_id == tu.tu_id


def test_parse_is_defensive_against_missing_keys() -> None:
    # A sparse/hand-edited dump must never abort the load (forward-compat).
    tu = parse_android_dump({"record_types": [{}], "functions": [{}]})
    assert len(tu.types) == 1 and len(tu.functions) == 1


def test_record_size_change_detected_end_to_end() -> None:
    old = link_source_abi([parse_android_dump(_dump(8), target_id="t")])
    new = link_source_abi([parse_android_dump(_dump(16), target_id="t")])
    # A record layout change shows up as an odr_source_conflict only across TUs;
    # here it is the type_hash that differs — assert the surfaces actually differ.
    old_hash = {e.qualified_name: e.type_hash for e in old.reachable_types}
    new_hash = {e.qualified_name: e.type_hash for e in new.reachable_types}
    assert old_hash["Foo"] != new_hash["Foo"]
    # No spurious findings for an unchanged enum.
    assert diff_source_abi(old, new) == [] or all(
        c.symbol != "E" for c in diff_source_abi(old, new)
    )


def test_adapter_load_reads_json_dump(tmp_path: Path) -> None:
    path = tmp_path / "libfoo.lsdump"
    path.write_text(json.dumps(_dump()))
    tu = AndroidHeaderAbiAdapter().load(path, target_id="target://libfoo")
    assert any(e.qualified_name == "Foo" for e in tu.types)
    assert tu.source == "include/foo.h"


def test_adapter_load_rejects_non_json(tmp_path: Path) -> None:
    # A raw protobuf .sdump (not produced with -output-format Json) is rejected
    # with an actionable message rather than a confusing parse error.
    path = tmp_path / "raw.sdump"
    path.write_text("\x08\x01 not json")
    with pytest.raises(SourceExtractionError, match="output-format Json"):
        AndroidHeaderAbiAdapter().load(path)


def test_adapter_load_rejects_non_object(tmp_path: Path) -> None:
    path = tmp_path / "arr.lsdump"
    path.write_text("[1, 2, 3]")
    with pytest.raises(SourceExtractionError, match="JSON object"):
        AndroidHeaderAbiAdapter().load(path)


def test_run_dumper_requires_tool() -> None:
    adapter = AndroidHeaderAbiAdapter(dumper_bin="header-abi-dumper-nope")
    assert adapter.available() is False
    with pytest.raises(SourceExtractionError, match="not found in PATH"):
        adapter.run_dumper("foo.h", output="out.sdump")


def test_run_dumper_success(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from abicheck.evidence.source_extractors import android as android_mod

    adapter = AndroidHeaderAbiAdapter()
    monkeypatch.setattr(adapter, "available", lambda: True)
    out = tmp_path / "o.sdump"

    class _Result:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(cmd, **kw):  # type: ignore[no-untyped-def]
        # The dumper writes its JSON output to the -o path.
        assert "-output-format" in cmd and "Json" in cmd
        out.write_text(json.dumps(_dump()))
        return _Result()

    monkeypatch.setattr(android_mod.subprocess, "run", fake_run)
    tu = adapter.run_dumper(
        tmp_path / "foo.h", output=out, clang_argv=["-std=c++17"], target_id="t"
    )
    assert any(e.qualified_name == "Foo" for e in tu.types)


def test_run_dumper_failure(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from abicheck.evidence.source_extractors import android as android_mod

    adapter = AndroidHeaderAbiAdapter()
    monkeypatch.setattr(adapter, "available", lambda: True)

    class _Result:
        returncode = 2
        stderr = "dump error"
        stdout = ""

    monkeypatch.setattr(android_mod.subprocess, "run", lambda cmd, **kw: _Result())
    with pytest.raises(SourceExtractionError, match="failed"):
        adapter.run_dumper("foo.h", output=tmp_path / "o.sdump")


def test_run_dumper_timeout(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import subprocess as sp

    from abicheck.evidence.source_extractors import android as android_mod

    adapter = AndroidHeaderAbiAdapter(timeout=1)
    monkeypatch.setattr(adapter, "available", lambda: True)

    def fake_run(cmd, **kw):  # type: ignore[no-untyped-def]
        raise sp.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(android_mod.subprocess, "run", fake_run)
    with pytest.raises(SourceExtractionError, match="timed out"):
        adapter.run_dumper("foo.h", output=tmp_path / "o.sdump")


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(SourceExtractionError, match="cannot read"):
        AndroidHeaderAbiAdapter().load(tmp_path / "nope.lsdump")
