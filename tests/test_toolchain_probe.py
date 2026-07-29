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

    def test_comma_only_spec_raises_instead_of_matching_vacuously(self) -> None:
        # Regression: "," parsed to zero clauses, and version_satisfies's
        # all-clauses-must-hold loop then vacuously accepted any compiler.
        with pytest.raises(
            tp.ToolchainProbeError, match="no version constraint clauses"
        ):
            tp.parse_version_constraints(",")

    def test_blank_spec_raises_instead_of_matching_vacuously(self) -> None:
        with pytest.raises(
            tp.ToolchainProbeError, match="no version constraint clauses"
        ):
            tp.parse_version_constraints("   ")


class TestVersionSatisfies:
    @pytest.mark.parametrize(
        ("actual", "constraint", "expected"),
        [
            ("gcc 13.2.0", "==13.2.0", True),
            ("gcc 13.2.0", "==13.2.1", False),
            ("gcc 13.2.0", ">=13,<14", True),
            ("gcc 13.2.0", ">=14", False),
            ("gcc 13.2.0", "!=13.2.0", False),
            ("gcc 13.2.0", ">13.1.0", True),
            ("gcc 13.2.0", ">13.2.0", False),
            ("gcc 13.2.0", "<13.3.0", True),
            ("gcc 13.2.0", "<13.2.0", False),
            ("clang version 18", ">=17", True),
            ("clang version 18", "<=17", False),
            ("gcc 13", ">=13.0.0", True),
            # Cross-compiler binding name (Debian/Ubuntu convention) embeds bare
            # target-triple digits ("86", "64") before the real, dotted version.
            (
                "x86_64-linux-gnu-gcc-13 (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0",
                ">=13,<14",
                True,
            ),
        ],
    )
    def test_cases(self, actual: str, constraint: str, expected: bool) -> None:
        assert tp.version_satisfies(actual, constraint) is expected

    def test_cross_compiler_prefix_does_not_shadow_real_version(self) -> None:
        # Regression: a bare first-digit-substring search picked "86" out of
        # the invoked name "x86_64-linux-gnu-gcc-13", rejecting a valid profile.
        banner = "x86_64-linux-gnu-gcc-13 (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0"
        assert tp.version_satisfies(banner, ">=13,<14") is True

    def test_no_version_number_raises(self) -> None:
        with pytest.raises(tp.ToolchainProbeError, match="no version number"):
            tp.version_satisfies("unavailable: no such tool", ">=1")

    def test_comma_only_constraint_does_not_vacuously_match(self) -> None:
        with pytest.raises(
            tp.ToolchainProbeError, match="no version constraint clauses"
        ):
            tp.version_satisfies("gcc 13.2.0", ",")

    def test_invalid_constraint_raises(self) -> None:
        with pytest.raises(tp.ToolchainProbeError, match="invalid version constraint"):
            tp.version_satisfies("gcc 13.0.0", "bogus")


class TestProbeCompilerFamily:
    def test_generic_alias_resolves_via_realpath(self) -> None:
        # Regression: a "cc"/"c++" driver alias (or symlink) that resolves to
        # a real gcc binary was classified by its own generic basename.
        metadata = {
            "selected": "/usr/bin/cc",
            "realpath": "/usr/bin/x86_64-linux-gnu-gcc-13",
            "version": "cc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0",
        }
        assert tp._probe_compiler_family(metadata) == "gnu"

    def test_clang_backed_alias_is_not_misread_as_gnu(self) -> None:
        # Regression: an alias whose OWN name contains "gcc" but whose
        # realpath/version banner is actually Clang must not be accepted as GNU.
        metadata = {
            "selected": "/usr/bin/gcc",
            "realpath": "/usr/bin/clang",
            "version": "Ubuntu clang version 18.1.3 (1ubuntu1)",
        }
        assert tp._probe_compiler_family(metadata) == "clang"

    def test_gnu_signature_phrase_fallback(self) -> None:
        metadata = {
            "selected": "/opt/cc1",
            "version": "cc1 (Free Software Foundation, Inc.) 12.0",
        }
        assert tp._probe_compiler_family(metadata) == "gnu"

    def test_inconclusive_metadata_returns_none(self) -> None:
        metadata = {"selected": "/opt/weird-tool", "version": "weird-tool 1.0"}
        assert tp._probe_compiler_family(metadata) is None

    def test_cl_exe_name_resolves_to_msvc(self) -> None:
        metadata = {"selected": "C:/VC/bin/cl.exe", "version": ""}
        assert tp._probe_compiler_family(metadata) == "msvc"


class TestOsFamily:
    @pytest.mark.parametrize(
        ("triple", "expected"),
        [
            ("x86_64-linux-gnu", "linux"),
            ("aarch64-linux-android", "android"),
            ("x86_64-w64-mingw32", "windows"),
            ("x86_64-pc-windows-msvc", "windows"),
            ("x86_64-apple-darwin23", "darwin"),
            ("x86_64-unknown-freebsd14", "freebsd"),
            ("x86_64-unknown-netbsd", "netbsd"),
            ("x86_64-unknown-openbsd", "openbsd"),
        ],
    )
    def test_recognized_markers(self, triple: str, expected: str) -> None:
        assert tp._os_family(triple) == expected

    def test_unrecognized_triple_returns_none(self) -> None:
        assert tp._os_family("some-made-up-triple") is None


@dataclass
class _FakeCompileSpec:
    compiler_family: str = ""
    compiler_version: str = ""
    target: str = ""
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

    def test_generic_alias_binding_matching_gcc_yields_no_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: profiles.<id>.compile.binding pointing at a "cc" driver
        # alias that resolves to real gcc used to be falsely rejected.
        _stub_metadata(
            monkeypatch,
            {
                "/usr/bin/cc": {
                    "selected": "/usr/bin/cc",
                    "realpath": "/usr/bin/x86_64-linux-gnu-gcc-13",
                    "version": "cc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0",
                }
            },
        )
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"cc1": "/usr/bin/cc"})
        profiles = {
            "p1": _FakeProfile(
                id="p1", compile=_FakeCompileSpec(compiler_family="gcc", binding="cc1")
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

    def test_unavailable_version_capture_is_reported_as_a_probe_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: dumper_toolchain._tool_version_output() swallows a
        # failed --version invocation (wrong format, not executable, timed
        # out) into the "version" string itself, not "error" -- a stale
        # binding merely named like a real compiler (e.g. "gcc") must not
        # pass family matching on basename alone when it can't even run.
        _stub_metadata(
            monkeypatch,
            {
                "/opt/gcc": {
                    "selected": "/opt/gcc",
                    "version": "unavailable:OSError:[Errno 8] Exec format error",
                }
            },
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

    def test_matching_target_architecture_yields_no_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_metadata(
            monkeypatch,
            {
                "/opt/gcc": {
                    "selected": "/opt/gcc",
                    "version": "gcc 13.2.0",
                    "target_triple": "x86_64-linux-gnu",
                }
            },
        )
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc14": "/opt/gcc"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(target="x86_64-linux-gnu", binding="gcc14"),
            )
        }
        assert tp.check_profile_toolchain_identity(profiles, bf) == []

    def test_target_architecture_alias_is_reconciled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_metadata(
            monkeypatch,
            {
                "/opt/gcc": {
                    "selected": "/opt/gcc",
                    "version": "gcc 13.2.0",
                    "target_triple": "aarch64-linux-gnu",
                }
            },
        )
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc14": "/opt/gcc"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(target="arm64-linux-gnu", binding="gcc14"),
            )
        }
        assert tp.check_profile_toolchain_identity(profiles, bf) == []

    def test_mismatched_target_architecture_yields_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: a target: declared with a mismatched architecture
        # (e.g. aarch64 bound to a real x86_64 gcc) previously passed
        # validation silently -- target was never checked at all.
        _stub_metadata(
            monkeypatch,
            {
                "/opt/gcc": {
                    "selected": "/opt/gcc",
                    "version": "gcc 13.2.0",
                    "target_triple": "x86_64-linux-gnu",
                }
            },
        )
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc14": "/opt/gcc"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(
                    compiler_family="gcc", target="aarch64-linux-gnu", binding="gcc14"
                ),
            )
        }
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 1
        assert "p1.compile.target" in errors[0]
        assert "aarch64" in errors[0]
        assert "x86_64" in errors[0]

    def test_mismatched_target_os_yields_error_despite_matching_arch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: x86_64-w64-mingw32 (Windows) bound to a real
        # x86_64-linux-gnu gcc shares the same architecture, so an
        # architecture-only check passed it silently.
        _stub_metadata(
            monkeypatch,
            {
                "/opt/gcc": {
                    "selected": "/opt/gcc",
                    "version": "gcc 13.2.0",
                    "target_triple": "x86_64-linux-gnu",
                }
            },
        )
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc14": "/opt/gcc"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(
                    compiler_family="gcc", target="x86_64-w64-mingw32", binding="gcc14"
                ),
            )
        }
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 1
        assert "p1.compile.target" in errors[0]
        assert "windows" in errors[0]
        assert "linux" in errors[0]

    def test_target_only_declared_with_no_probed_triple_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A tool that doesn't support -dumpmachine has no target_triple key
        # at all (dumper_toolchain._tool_target_triple returns None); no
        # basis to compare against, so this must not error.
        _stub_metadata(
            monkeypatch,
            {"/opt/gcc": {"selected": "/opt/gcc", "version": "gcc 13.2.0"}},
        )
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc14": "/opt/gcc"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(target="x86_64-linux-gnu", binding="gcc14"),
            )
        }
        assert tp.check_profile_toolchain_identity(profiles, bf) == []

    def test_version_only_declared_checks_without_a_family(
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
                compile=_FakeCompileSpec(compiler_version=">=13,<14", binding="gcc14"),
            )
        }
        assert tp.check_profile_toolchain_identity(profiles, bf) == []

    def test_unparseable_version_constraint_is_reported(
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
                    compiler_version="not-a-constraint", binding="gcc14"
                ),
            )
        }
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 1
        assert "compiler_version" in errors[0]
        assert "invalid version constraint" in errors[0]

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
    @staticmethod
    def _resolve_real_family(binding_path: str) -> str | None:
        # Ask the probe itself what family the "gcc" binary on THIS host
        # actually resolves to, rather than assuming -- on macOS, /usr/bin/gcc
        # is Apple's Clang-backed alias, not a real GNU compiler (confirmed
        # via a real CI failure on macos-latest: the probe correctly reported
        # family "clang" for a test that assumed "gcc").
        return tp._probe_compiler_family(tp._tool_identity_metadata(binding_path))

    def test_real_gcc_binding_matches_its_own_family(self) -> None:
        gcc_path = shutil.which("gcc")
        if gcc_path is None:
            pytest.skip("gcc not available")
        real_family = self._resolve_real_family(gcc_path)
        if real_family is None:
            pytest.skip("could not determine the real family of the gcc binary")
        declared = {"gnu": "gcc", "clang": "clang"}.get(real_family)
        if declared is None:
            pytest.skip(f"unhandled real family {real_family!r} for this test")
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc-real": gcc_path})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(compiler_family=declared, binding="gcc-real"),
            )
        }
        assert tp.check_profile_toolchain_identity(profiles, bf) == []

    def test_real_gcc_binding_rejects_wrong_family(self) -> None:
        gcc_path = shutil.which("gcc")
        if gcc_path is None:
            pytest.skip("gcc not available")
        real_family = self._resolve_real_family(gcc_path)
        if real_family is None:
            pytest.skip("could not determine the real family of the gcc binary")
        # Declare the OTHER family from whichever this host's "gcc" really is.
        wrong_declared = "clang" if real_family == "gnu" else "gcc"
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc-real": gcc_path})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(
                    compiler_family=wrong_declared, binding="gcc-real"
                ),
            )
        }
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 1
