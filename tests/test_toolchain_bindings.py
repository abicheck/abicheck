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

"""Unit tests for the trusted toolchain-bindings file loader
(abicheck/buildsource/toolchain_bindings.py, P1 toolchain-profile audit)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from abicheck.buildsource.toolchain_bindings import (
    BINDINGS_SCHEMA,
    BindingsFile,
    BindingsFileError,
    check_profile_bindings_resolve,
    load_bindings_file,
    resolve_binding,
)


class TestBindingsFileFromDict:
    def test_valid_document_round_trips(self) -> None:
        bf = BindingsFile.from_dict(
            {
                "schema": BINDINGS_SCHEMA,
                "bindings": {"gcc14": "/opt/gcc-14/bin/g++"},
            }
        )
        assert bf.schema == BINDINGS_SCHEMA
        assert bf.bindings == {"gcc14": "/opt/gcc-14/bin/g++"}
        assert bf.to_dict() == {
            "schema": BINDINGS_SCHEMA,
            "bindings": {"gcc14": "/opt/gcc-14/bin/g++"},
        }

    def test_empty_bindings_is_valid(self) -> None:
        bf = BindingsFile.from_dict({"schema": BINDINGS_SCHEMA})
        assert bf.bindings == {}

    def test_non_mapping_document_rejected(self) -> None:
        with pytest.raises(BindingsFileError, match="must be a mapping"):
            BindingsFile.from_dict([])  # type: ignore[arg-type]

    def test_wrong_schema_rejected(self) -> None:
        with pytest.raises(BindingsFileError, match="schema"):
            BindingsFile.from_dict({"schema": "something-else/v1"})

    def test_missing_schema_rejected(self) -> None:
        with pytest.raises(BindingsFileError, match="schema"):
            BindingsFile.from_dict({"bindings": {}})

    def test_unknown_top_level_key_rejected(self) -> None:
        with pytest.raises(BindingsFileError, match="unknown key"):
            BindingsFile.from_dict({"schema": BINDINGS_SCHEMA, "extra": 1})

    def test_bindings_not_a_mapping_rejected(self) -> None:
        with pytest.raises(BindingsFileError, match="must be a mapping"):
            BindingsFile.from_dict({"schema": BINDINGS_SCHEMA, "bindings": []})

    def test_non_string_binding_id_rejected(self) -> None:
        with pytest.raises(BindingsFileError, match="binding id"):
            BindingsFile.from_dict(
                {"schema": BINDINGS_SCHEMA, "bindings": {1: "/usr/bin/g++"}}
            )

    def test_empty_binding_id_rejected(self) -> None:
        with pytest.raises(BindingsFileError, match="binding id"):
            BindingsFile.from_dict(
                {"schema": BINDINGS_SCHEMA, "bindings": {"": "/usr/bin/g++"}}
            )

    def test_non_string_path_rejected(self) -> None:
        with pytest.raises(BindingsFileError, match="gcc14"):
            BindingsFile.from_dict(
                {"schema": BINDINGS_SCHEMA, "bindings": {"gcc14": 123}}
            )

    def test_empty_path_rejected(self) -> None:
        with pytest.raises(BindingsFileError, match="gcc14"):
            BindingsFile.from_dict(
                {"schema": BINDINGS_SCHEMA, "bindings": {"gcc14": ""}}
            )

    def test_whitespace_only_path_rejected(self) -> None:
        with pytest.raises(BindingsFileError, match="gcc14"):
            BindingsFile.from_dict(
                {"schema": BINDINGS_SCHEMA, "bindings": {"gcc14": "   "}}
            )


class TestLoadBindingsFile:
    def test_loads_valid_file(self, tmp_path: Path) -> None:
        path = tmp_path / "bindings.yml"
        path.write_text(
            f"schema: {BINDINGS_SCHEMA}\n"
            "bindings:\n"
            "  gcc14: /opt/gcc-14/bin/g++\n"
            "  castxml07: /opt/conda/bin/castxml\n"
        )
        bf = load_bindings_file(path)
        assert bf.bindings["gcc14"] == "/opt/gcc-14/bin/g++"
        assert bf.bindings["castxml07"] == "/opt/conda/bin/castxml"

    def test_accepts_str_path_too(self, tmp_path: Path) -> None:
        path = tmp_path / "bindings.yml"
        path.write_text(f"schema: {BINDINGS_SCHEMA}\nbindings: {{}}\n")
        bf = load_bindings_file(str(path))
        assert bf.schema == BINDINGS_SCHEMA

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(BindingsFileError, match="cannot read"):
            load_bindings_file(tmp_path / "does-not-exist.yml")

    def test_malformed_yaml_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bindings.yml"
        path.write_text("schema: [unterminated\n")
        with pytest.raises(BindingsFileError, match="cannot parse"):
            load_bindings_file(path)

    def test_empty_file_raises_missing_schema(self, tmp_path: Path) -> None:
        path = tmp_path / "bindings.yml"
        path.write_text("")
        with pytest.raises(BindingsFileError, match="schema"):
            load_bindings_file(path)

    def test_wrong_schema_error_names_the_file(self, tmp_path: Path) -> None:
        path = tmp_path / "bindings.yml"
        path.write_text("schema: nope/v1\n")
        with pytest.raises(BindingsFileError, match=r"bindings\.yml"):
            load_bindings_file(path)

    def test_does_not_auto_discover(self, tmp_path: Path, monkeypatch) -> None:
        """The trust boundary: only the exact given path is ever read. A
        differently-named bindings-looking file sitting next to it (e.g. a
        default '.abicheck-toolchain-bindings.yml') must never be picked up
        implicitly."""
        monkeypatch.chdir(tmp_path)
        decoy = tmp_path / ".abicheck-toolchain-bindings.yml"
        decoy.write_text(f"schema: {BINDINGS_SCHEMA}\nbindings:\n  gcc14: /decoy\n")
        with pytest.raises(BindingsFileError, match="cannot read"):
            load_bindings_file(tmp_path / "real-bindings.yml")


class TestResolveBinding:
    def test_resolves_known_id(self) -> None:
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc14": "/usr/bin/g++-14"})
        assert resolve_binding(bf, "gcc14") == "/usr/bin/g++-14"

    def test_unknown_id_raises_with_available_list(self) -> None:
        bf = BindingsFile(
            schema=BINDINGS_SCHEMA,
            bindings={"gcc14": "/usr/bin/g++-14", "clang20": "/usr/bin/clang++-20"},
        )
        with pytest.raises(BindingsFileError) as exc_info:
            resolve_binding(bf, "msvc194")
        message = str(exc_info.value)
        assert "msvc194" in message
        assert "gcc14" in message
        assert "clang20" in message

    def test_unknown_id_on_empty_bindings_says_none_declared(self) -> None:
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={})
        with pytest.raises(BindingsFileError, match=r"\(none declared\)"):
            resolve_binding(bf, "gcc14")


@dataclass
class _FakeCompileSpec:
    binding: str = ""


@dataclass
class _FakeProfile:
    id: str
    compile: _FakeCompileSpec | None = None


class TestCheckProfileBindingsResolve:
    def test_no_profiles_is_no_errors(self) -> None:
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={})
        assert check_profile_bindings_resolve({}, bf) == []

    def test_profile_with_no_compile_overlay_is_skipped(self) -> None:
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={})
        profiles = {"p1": _FakeProfile(id="p1", compile=None)}
        assert check_profile_bindings_resolve(profiles, bf) == []

    def test_profile_with_no_binding_field_is_skipped(self) -> None:
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={})
        profiles = {"p1": _FakeProfile(id="p1", compile=_FakeCompileSpec(binding=""))}
        assert check_profile_bindings_resolve(profiles, bf) == []

    def test_resolved_binding_yields_no_error(self) -> None:
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc14": "/usr/bin/g++-14"})
        profiles = {
            "p1": _FakeProfile(id="p1", compile=_FakeCompileSpec(binding="gcc14"))
        }
        assert check_profile_bindings_resolve(profiles, bf) == []

    def test_unresolved_binding_yields_one_error_naming_the_profile(self) -> None:
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={})
        profiles = {
            "p1": _FakeProfile(id="p1", compile=_FakeCompileSpec(binding="gcc14"))
        }
        errors = check_profile_bindings_resolve(profiles, bf)
        assert len(errors) == 1
        assert "p1" in errors[0]
        assert "gcc14" in errors[0]

    def test_multiple_profiles_each_checked_independently(self) -> None:
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc14": "/usr/bin/g++-14"})
        profiles = {
            "ok": _FakeProfile(id="ok", compile=_FakeCompileSpec(binding="gcc14")),
            "bad": _FakeProfile(id="bad", compile=_FakeCompileSpec(binding="clang20")),
        }
        errors = check_profile_bindings_resolve(profiles, bf)
        assert len(errors) == 1
        assert "bad" in errors[0]
