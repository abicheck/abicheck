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

"""Unit tests for G34 Phase A toolchain-identity enforcement
(abicheck/buildsource/toolchain_probe.py)."""

from __future__ import annotations

import shutil
from dataclasses import dataclass

import pytest

from abicheck.buildsource import toolchain_probe as tp
from abicheck.buildsource.toolchain_bindings import BINDINGS_SCHEMA, BindingsFile


class TestParseVersionConstraints:
    def test_bare_version_defaults_to_equals(self) -> None:
        assert tp.parse_version_constraints("14.2") == [("==", (14, 2))]

    def test_comma_separated_clauses(self) -> None:
        assert tp.parse_version_constraints(">=14,<15") == [
            (">=", (14,)),
            ("<", (15,)),
        ]

    def test_blank_clauses_are_skipped(self) -> None:
        assert tp.parse_version_constraints(" >=1.0 , , <=2.0 ") == [
            (">=", (1, 0)),
            ("<=", (2, 0)),
        ]

    def test_invalid_clause_raises(self) -> None:
        with pytest.raises(tp.ToolchainProbeError, match="invalid version constraint"):
            tp.parse_version_constraints("~>1.0")


class TestVersionSatisfies:
    @pytest.mark.parametrize(
        ("actual", "constraint", "expected"),
        [
            ("gcc 13.2.0", "==13.2.0", True),
            ("gcc 13.2.0", "==13.2.1", False),
            ("gcc 13.2.0", ">=13,<14", True),
            ("gcc 13.2.0", ">=14", False),
            ("gcc 13.2.0", "!=13.2.0", False),
            ("clang version 18", ">=17", True),
            ("clang version 18", "<=17", False),
            ("gcc 13", ">=13.0.0", True),
        ],
    )
    def test_cases(self, actual: str, constraint: str, expected: bool) -> None:
        assert tp.version_satisfies(actual, constraint) is expected

    def test_no_version_number_raises(self) -> None:
        with pytest.raises(tp.ToolchainProbeError, match="no version number"):
            tp.version_satisfies("unavailable: no such tool", ">=1")

    def test_invalid_constraint_raises(self) -> None:
        with pytest.raises(tp.ToolchainProbeError, match="invalid version constraint"):
            tp.version_satisfies("gcc 13.0.0", "bogus")


@dataclass
class _FakeCompileSpec:
    compiler_family: str = ""
    compiler_version: str = ""
    binding: str = ""


@dataclass
class _FakeProfile:
    id: str
    compile: _FakeCompileSpec | None = None
    consumer_compile: _FakeCompileSpec | None = None


def _stub_metadata(
    monkeypatch: pytest.MonkeyPatch, by_path: dict[str, dict[str, str]]
) -> None:
    def _fake_tool_identity_metadata(path: str) -> dict[str, str]:
        return by_path[path]

    monkeypatch.setattr(tp, "_tool_identity_metadata", _fake_tool_identity_metadata)


class TestCheckProfileToolchainIdentity:
    def test_no_binding_declared_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_metadata(monkeypatch, {})
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={})
        profiles = {
            "p1": _FakeProfile(id="p1", compile=_FakeCompileSpec(compiler_family="gcc"))
        }
        assert tp.check_profile_toolchain_identity(profiles, bf) == []

    def test_no_family_or_version_declared_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_metadata(monkeypatch, {})
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc14": "/opt/gcc"})
        profiles = {
            "p1": _FakeProfile(id="p1", compile=_FakeCompileSpec(binding="gcc14"))
        }
        assert tp.check_profile_toolchain_identity(profiles, bf) == []

    def test_unresolvable_binding_is_skipped_here(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # check_profile_bindings_resolve's job, not this function's — no duplicate error.
        _stub_metadata(monkeypatch, {})
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(compiler_family="gcc", binding="gcc14"),
            )
        }
        assert tp.check_profile_toolchain_identity(profiles, bf) == []

    def test_msvc_family_is_never_probed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(path: str) -> dict[str, str]:
            raise AssertionError("MSVC bindings must not be probed")

        monkeypatch.setattr(tp, "_tool_identity_metadata", _boom)
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"msvc14": "C:/cl.exe"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(
                    compiler_family="msvc", compiler_version="19.30", binding="msvc14"
                ),
            )
        }
        assert tp.check_profile_toolchain_identity(profiles, bf) == []

    def test_matching_family_and_version_yields_no_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_metadata(
            monkeypatch,
            {"/opt/gcc": {"selected": "/opt/gcc", "version": "gcc 13.2.0"}},
        )
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc14": "/opt/gcc"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(
                    compiler_family="gcc", compiler_version=">=13,<14", binding="gcc14"
                ),
            )
        }
        assert tp.check_profile_toolchain_identity(profiles, bf) == []

    def test_mismatched_family_yields_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_metadata(
            monkeypatch,
            {"/opt/gcc": {"selected": "/opt/gcc", "version": "gcc 13.2.0"}},
        )
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc14": "/opt/gcc"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(compiler_family="clang", binding="gcc14"),
            )
        }
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 1
        assert "p1.compile.compiler_family" in errors[0]
        assert "clang" in errors[0]
        assert "gnu" in errors[0]

    def test_mismatched_version_yields_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_metadata(
            monkeypatch,
            {"/opt/gcc": {"selected": "/opt/gcc", "version": "gcc 13.2.0"}},
        )
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc14": "/opt/gcc"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(
                    compiler_family="gcc", compiler_version=">=14", binding="gcc14"
                ),
            )
        }
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 1
        assert "p1.compile.compiler_version" in errors[0]

    def test_probe_error_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_metadata(
            monkeypatch,
            {"/opt/gcc": {"selected": "/opt/gcc", "error": "OSError: boom"}},
        )
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc14": "/opt/gcc"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(compiler_family="gcc", binding="gcc14"),
            )
        }
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 1
        assert "could not be probed" in errors[0]

    def test_consumer_compile_overlay_checked_independently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_metadata(
            monkeypatch,
            {"/opt/gcc": {"selected": "/opt/gcc", "version": "gcc 13.2.0"}},
        )
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc14": "/opt/gcc"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(compiler_family="gcc", binding="gcc14"),
                consumer_compile=_FakeCompileSpec(
                    compiler_family="clang", binding="gcc14"
                ),
            )
        }
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 1
        assert "consumer_compile" in errors[0]

    def test_multiple_profiles_each_checked_independently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_metadata(
            monkeypatch,
            {
                "/opt/gcc": {"selected": "/opt/gcc", "version": "gcc 13.2.0"},
                "/opt/clang": {"selected": "/opt/clang", "version": "clang 18.0.0"},
            },
        )
        bf = BindingsFile(
            schema=BINDINGS_SCHEMA,
            bindings={"gcc14": "/opt/gcc", "clang18": "/opt/clang"},
        )
        profiles = {
            "ok": _FakeProfile(
                id="ok",
                compile=_FakeCompileSpec(compiler_family="gcc", binding="gcc14"),
            ),
            "bad": _FakeProfile(
                id="bad",
                compile=_FakeCompileSpec(compiler_family="gcc", binding="clang18"),
            ),
        }
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 1
        assert "bad" in errors[0]


@pytest.mark.integration
class TestCheckProfileToolchainIdentityRealCompiler:
    def test_real_gcc_binding_matches_its_own_family(self) -> None:
        gcc_path = shutil.which("gcc")
        if gcc_path is None:
            pytest.skip("gcc not available")
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc-real": gcc_path})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(compiler_family="gcc", binding="gcc-real"),
            )
        }
        assert tp.check_profile_toolchain_identity(profiles, bf) == []

    def test_real_gcc_binding_rejects_wrong_family(self) -> None:
        gcc_path = shutil.which("gcc")
        if gcc_path is None:
            pytest.skip("gcc not available")
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc-real": gcc_path})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(compiler_family="clang", binding="gcc-real"),
            )
        }
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 1
