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
import subprocess
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

    def test_apple_clang_build_identifier_does_not_shadow_real_version(self) -> None:
        # Regression: Apple/Xcode clang's banner puts its real version right
        # after the literal word "version", followed by an unrelated
        # parenthetical build identifier with MORE dot components than the
        # real version -- a bare "last dotted token" search previously
        # picked "1600.0.26.4" (the build ID) instead of "16.0.0" (the real
        # version), rejecting a valid >=16,<17 constraint (Codex review,
        # fresh evidence).
        banner = "Apple clang version 16.0.0 (clang-1600.0.26.4)"
        assert tp._extract_version_token(banner) == "16.0.0"
        assert tp.version_satisfies(banner, ">=16,<17") is True

    def test_dotted_target_os_version_in_prefix_does_not_shadow_real_version(
        self,
    ) -> None:
        # Regression: a cross-compiler binding name can itself embed a
        # dotted number ahead of the real version -- not just bare
        # target-triple digits (the case above), but a genuinely dotted
        # target-triple OS version. "x86_64-pc-solaris2.11-gcc" previously
        # extracted "2.11" instead of the real "13.2.0" (Codex review,
        # fresh evidence).
        banner = "x86_64-pc-solaris2.11-gcc (GCC) 13.2.0"
        assert tp.version_satisfies(banner, ">=13,<14") is True

    def test_intel_oneapi_build_identifier_does_not_shadow_real_version(self) -> None:
        # Regression: Intel's oneAPI DPC++/C++ compiler banner has no
        # "version" keyword and puts its real version BEFORE a parenthesized
        # build identifier (the opposite arrangement from GCC's package
        # descriptor, which comes before the real version) -- an unfiltered
        # last-dotted-match search picked the build identifier
        # "2026.1.0.20260617" instead of the real "2026.1.0" (Codex review,
        # fresh evidence, using the real banner from
        # tests/fixtures/g32/dpcpp/compiler_invocation.log).
        banner = "Intel(R) oneAPI DPC++/C++ Compiler 2026.1.0 (2026.1.0.20260617)"
        assert tp._extract_version_token(banner) == "2026.1.0"
        assert tp.version_satisfies(banner, "==2026.1.0") is True
        assert tp.version_satisfies(banner, "<=2026.1.0") is True

    def test_dotted_match_only_inside_parentheses_still_extracts_something(
        self,
    ) -> None:
        # No banner shape this module has actually seen has its ONLY dotted
        # number fully parenthesized, but the extractor must still return
        # something rather than nothing in that hypothetical case.
        assert tp._extract_version_token("gcc (13.2.0)") == "13.2.0"

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

    def test_icpx_binary_name_resolves_to_icx(self) -> None:
        # Regression: Intel's oneAPI DPC++/C++ compiler is a clang-based
        # driver under a non-"clang"-spelled name; without recognizing it
        # explicitly it was indeterminate (Codex review, fresh evidence).
        metadata = {
            "selected": "/opt/intel/oneapi/compiler/2026.1/bin/compiler/icpx",
            "version": "Intel(R) oneAPI DPC++/C++ Compiler 2026.1.0 (2026.1.0.20260617)",
        }
        assert tp._probe_compiler_family(metadata) == "icx"

    def test_oneapi_banner_alone_resolves_to_icx(self) -> None:
        # A resolved path that doesn't itself carry an icx/icpx/dpcpp name
        # (e.g. an unusual symlink) still resolves via the banner text.
        metadata = {
            "selected": "/usr/local/bin/mycompiler",
            "version": "Intel(R) oneAPI DPC++/C++ Compiler 2026.1.0 (2026.1.0.20260617)",
        }
        assert tp._probe_compiler_family(metadata) == "icx"

    def test_classic_icc_binary_name_resolves_to_icc(self) -> None:
        # Regression: classic, pre-oneAPI icc/icpc was previously
        # unrecognized entirely -- a declared compiler_family: icc profile
        # was rejected unconditionally, even though CompilerFamily.ICC is
        # already a recognized, distinct family elsewhere in the codebase
        # (Codex review, fresh evidence).
        metadata = {
            "selected": "/opt/intel/bin/icc",
            "version": "icc (ICC) 2021.7.1 20221019",
        }
        assert tp._probe_compiler_family(metadata) == "icc"

    def test_classic_icpc_banner_alone_resolves_to_icc(self) -> None:
        metadata = {
            "selected": "/usr/local/bin/mycompiler",
            "version": (
                "Intel(R) C++ Compiler for applications running on Intel(R) "
                "64, Version 19.1.3.304"
            ),
        }
        assert tp._probe_compiler_family(metadata) == "icc"

    def test_classic_icc_is_not_confused_with_oneapi_icx(self) -> None:
        # The oneAPI check runs first and must not misfire on a classic
        # ICC banner (which never mentions "oneAPI"/"DPC++").
        metadata = {
            "selected": "/opt/intel/bin/icc",
            "version": "icc (ICC) 2021.7.1 20221019",
        }
        assert tp._probe_compiler_family(metadata) != "icx"


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


class TestEnvFamily:
    @pytest.mark.parametrize(
        ("triple", "expected"),
        [
            ("x86_64-linux-gnu", "gnu"),
            ("x86_64-linux-musl", "musl"),
            ("arm-linux-gnueabihf", "gnueabihf"),
            ("arm-linux-gnueabi", "gnueabi"),
            ("x86_64-linux-gnux32", "gnux32"),
            ("x86_64-pc-windows-msvc", "msvc"),
            ("mips64el-linux-gnuabi64", "gnuabi64"),
            ("mips64el-linux-gnuabin32", "gnuabin32"),
            ("aarch64-linux-gnu_ilp32", "gnu_ilp32"),
            ("powerpc-linux-gnuspe", "gnuspe"),
            ("x86_64-w64-mingw32", "gnu"),
            ("x86_64-pc-windows-gnu", "gnu"),
            ("x86_64-w64-windows-gnu", "gnu"),
        ],
    )
    def test_recognized_markers(self, triple: str, expected: str) -> None:
        assert tp._env_family(triple) == expected

    def test_empty_triple_returns_none(self) -> None:
        assert tp._env_family("") is None

    def test_gcc_and_clang_mingw_spellings_are_equivalent(self) -> None:
        # Regression: GCC's own x86_64-w64-mingw32 triple has no separate
        # env component at all (folded into the OS component), while Clang
        # spells the identical real environment as an explicit 4th "gnu"
        # component -- both describe the same MinGW-w64 runtime, but
        # previously only the Clang spelling normalized to "gnu" (GCC's
        # normalized to None), rejecting an otherwise-valid MinGW
        # cross-compiler profile (Codex review, fresh evidence).
        assert tp._env_family("x86_64-w64-mingw32") == tp._env_family(
            "x86_64-pc-windows-gnu"
        )
        assert tp._env_family("x86_64-w64-mingw32") == tp._env_family(
            "x86_64-w64-windows-gnu"
        )

    def test_gnueabihf_and_gnueabi_are_distinct(self) -> None:
        # Regression: both contain "gnu" as a substring and previously
        # collapsed to the same family, hiding an incompatible calling
        # convention (soft-float vs. hard-float EABI).
        assert tp._env_family("arm-linux-gnueabi") != tp._env_family(
            "arm-linux-gnueabihf"
        )

    def test_gnu_and_gnux32_are_distinct(self) -> None:
        # Regression: x86_64-linux-gnu vs x86_64-linux-gnux32 (the x32
        # ILP32-on-x86_64 ABI) previously both reduced to "gnu".
        assert tp._env_family("x86_64-linux-gnu") != tp._env_family(
            "x86_64-linux-gnux32"
        )

    def test_unrecognized_triple_returns_none(self) -> None:
        assert tp._env_family("arm-none-eabi") is None

    def test_mips_n64_and_n32_abis_are_distinct(self) -> None:
        # Regression: mips64el-linux-gnuabi64 (N64) vs
        # mips64el-linux-gnuabin32 (N32) both contain "gnu" as a substring
        # and previously collapsed to the same generic "gnu" family, hiding
        # an incompatible MIPS data model (Codex review, fresh evidence,
        # a third round beyond the gnueabi*/gnux32 fix).
        assert tp._env_family("mips64el-linux-gnuabi64") != tp._env_family(
            "mips64el-linux-gnuabin32"
        )

    def test_gnu_and_gnu_ilp32_are_distinct(self) -> None:
        # Regression: aarch64-linux-gnu vs aarch64-linux-gnu_ilp32 (the
        # AArch64 ILP32 data model) previously both reduced to "gnu".
        assert tp._env_family("aarch64-linux-gnu") != tp._env_family(
            "aarch64-linux-gnu_ilp32"
        )

    def test_gnu_and_gnuspe_are_distinct(self) -> None:
        # Regression: powerpc-linux-gnuspe (PowerPC SPE) vs
        # powerpc-linux-gnu previously both reduced to "gnu" too --
        # closed by generalizing to the whole trailing triple component
        # instead of enumerating yet another individual ABI-suffix marker
        # (Codex review, fresh evidence: enumeration is inherently
        # incomplete, confirmed by this being the fourth distinct GNU ABI
        # suffix pair found across four separate review rounds).
        assert tp._env_family("powerpc-linux-gnu") != tp._env_family(
            "powerpc-linux-gnuspe"
        )

    def test_any_gnu_suffix_is_preserved_without_enumeration(self) -> None:
        # The general mechanism itself: an entirely made-up ABI suffix
        # nobody has enumerated is still preserved verbatim, rather than
        # collapsing to the generic "gnu" bucket.
        assert tp._env_family("arm-linux-gnu_made_up_suffix") == "gnu_made_up_suffix"
        assert tp._env_family("arm-linux-gnu") != tp._env_family(
            "arm-linux-gnu_made_up_suffix"
        )


class TestClangAcceptsTarget:
    def _run(self, monkeypatch: pytest.MonkeyPatch, returncode: int, stderr: str):
        tp._clang_accepts_target.cache_clear()

        def _fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(
                argv, returncode, stdout="", stderr=stderr
            )

        monkeypatch.setattr(tp.subprocess, "run", _fake_run)

    def test_accepted_target_returns_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._run(monkeypatch, 0, "")
        assert (
            tp._clang_accepts_target("/opt/clang", "digest1", "aarch64-linux-gnu")
            is True
        )

    def test_unknown_target_triple_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._run(monkeypatch, 1, "error: unknown target triple 'bogus'")
        assert tp._clang_accepts_target("/opt/clang", "digest2", "bogus") is False

    def test_a_differently_worded_invalid_target_diagnostic_still_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: real Clang 17 and Apple/macOS clang both reject a bogus
        # target with wording that doesn't contain "unknown target triple"
        # (e.g. "version 'target' in target triple '...' is invalid") --
        # classification must not depend on a specific diagnostic phrase.
        self._run(
            monkeypatch,
            1,
            "error: version 'target' in target triple 'bogus' is invalid",
        )
        assert tp._clang_accepts_target("/opt/clang", "digest3", "bogus") is False

    def test_any_nonzero_exit_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._run(monkeypatch, 1, "error: unsupported option '-fsyntax-only'")
        assert (
            tp._clang_accepts_target("/opt/clang", "digest3b", "aarch64-linux-gnu")
            is False
        )

    def test_probe_failure_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tp._clang_accepts_target.cache_clear()

        def _boom(argv, **kwargs):
            raise OSError("exec failed")

        monkeypatch.setattr(tp.subprocess, "run", _boom)
        assert (
            tp._clang_accepts_target("/opt/clang", "digest4", "aarch64-linux-gnu")
            is None
        )


@pytest.mark.integration
class TestClangAcceptsTargetRealCompiler:
    def test_real_clang_accepts_a_real_cross_target(self) -> None:
        clang_path = shutil.which("clang")
        if clang_path is None:
            pytest.skip("clang not available")
        tp._clang_accepts_target.cache_clear()
        assert tp._clang_accepts_target(clang_path, "real", "aarch64-linux-gnu") is True

    def test_real_clang_rejects_a_bogus_target(self) -> None:
        clang_path = shutil.which("clang")
        if clang_path is None:
            pytest.skip("clang not available")
        tp._clang_accepts_target.cache_clear()
        assert (
            tp._clang_accepts_target(clang_path, "real", "not-a-real-target") is False
        )


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

    def test_msvc_binding_is_never_probed_even_without_a_declared_family(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: the MSVC skip only checked the *declared*
        # compiler_family. A profile declaring just target:/compiler_version:
        # (no compiler_family:) whose binding resolves to a real cl.exe was
        # still probed with --version, which cl.exe doesn't support --
        # reported as a probe error, contradicting this module's own
        # documented "a declared MSVC family/binding is silently skipped"
        # (CodeRabbit review, fresh evidence).
        def _boom(path: str) -> dict[str, str]:
            raise AssertionError("cl.exe must not be probed")

        monkeypatch.setattr(tp, "_tool_identity_metadata", _boom)
        bf = BindingsFile(
            schema=BINDINGS_SCHEMA, bindings={"msvc14": "C:/VC/bin/cl.exe"}
        )
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(
                    target="x86_64-pc-windows-msvc", binding="msvc14"
                ),
            )
        }
        assert tp.check_profile_toolchain_identity(profiles, bf) == []

    def test_declared_gcc_family_against_a_resolved_cl_exe_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: the resolved-path MSVC skip above applied
        # unconditionally, so a profile explicitly declaring
        # compiler_family: gcc whose binding resolves to a real cl.exe was
        # also silently exempted -- a genuinely conflicting declared family
        # must still be probed and reported, not skipped just because the
        # resolved binary happens to be MSVC (Codex review, fresh evidence).
        _stub_metadata(
            monkeypatch,
            {
                "C:/VC/bin/cl.exe": {
                    "selected": "C:/VC/bin/cl.exe",
                    "version": "unavailable: cl.exe does not support --version",
                }
            },
        )
        bf = BindingsFile(
            schema=BINDINGS_SCHEMA, bindings={"msvc14": "C:/VC/bin/cl.exe"}
        )
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(compiler_family="gcc", binding="msvc14"),
            )
        }
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 1
        assert "could not be probed" in errors[0]

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

    def test_declared_icx_family_and_version_against_a_real_intel_binding_yields_no_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: a declared compiler_family: icx profile was
        # unconditionally rejected -- Intel's oneAPI DPC++/C++ compiler
        # (icx/icpx) was previously either indeterminate or misclassified,
        # even though CompilerFamily.ICX is already a recognized, distinct
        # family elsewhere in the codebase (Codex review, fresh evidence).
        _stub_metadata(
            monkeypatch,
            {
                "/opt/intel/icpx": {
                    "selected": "/opt/intel/icpx",
                    "version": (
                        "Intel(R) oneAPI DPC++/C++ Compiler 2026.1.0 "
                        "(2026.1.0.20260617)"
                    ),
                    "target_triple": "x86_64-unknown-linux-gnu",
                }
            },
        )
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"icx26": "/opt/intel/icpx"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(
                    compiler_family="icx",
                    compiler_version="==2026.1.0",
                    binding="icx26",
                ),
            )
        }
        assert tp.check_profile_toolchain_identity(profiles, bf) == []

    def test_declared_icc_family_against_a_real_classic_intel_binding_yields_no_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: a declared compiler_family: icc profile was
        # unconditionally rejected -- classic, pre-oneAPI icc/icpc was
        # previously unrecognized entirely (Codex review, fresh evidence).
        _stub_metadata(
            monkeypatch,
            {
                "/opt/intel/bin/icc": {
                    "selected": "/opt/intel/bin/icc",
                    "version": "icc (ICC) 2021.7.1 20221019",
                }
            },
        )
        bf = BindingsFile(
            schema=BINDINGS_SCHEMA, bindings={"icc21": "/opt/intel/bin/icc"}
        )
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(
                    compiler_family="icc",
                    compiler_version="==2021.7.1",
                    binding="icc21",
                ),
            )
        }
        assert tp.check_profile_toolchain_identity(profiles, bf) == []

    def test_gcc_mingw_binding_against_a_clang_style_declared_target_yields_no_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: a GCC-family MinGW binding's probed target_triple
        # (x86_64-w64-mingw32, GCC's own 3-component spelling with no
        # separate env component) previously failed to match a declared
        # target using Clang's equivalent 4-component spelling
        # (x86_64-pc-windows-gnu), rejecting an otherwise-valid MinGW
        # cross-compiler profile (Codex review, fresh evidence).
        _stub_metadata(
            monkeypatch,
            {
                "/opt/mingw-gcc": {
                    "selected": "/opt/mingw-gcc",
                    "version": "gcc 13.2.0",
                    "target_triple": "x86_64-w64-mingw32",
                }
            },
        )
        bf = BindingsFile(
            schema=BINDINGS_SCHEMA, bindings={"mingw14": "/opt/mingw-gcc"}
        )
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(
                    compiler_family="gcc",
                    target="x86_64-pc-windows-gnu",
                    binding="mingw14",
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

    def test_target_declared_with_no_probed_triple_on_gnu_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: a GCC-family tool that doesn't support -dumpmachine
        # (no target_triple key) previously skipped the target check
        # entirely, silently accepting an unverifiable claim.
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
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 1
        assert "cannot be verified" in errors[0]

    def test_target_declared_with_unidentifiable_family_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: an unidentifiable executable (no recognized name or
        # version signature) previously passed any declared compiler_family
        # AND any declared target, since both checks silently no-op'd.
        _stub_metadata(
            monkeypatch,
            {"/opt/weird": {"selected": "/opt/weird", "version": "weird-tool 1.0"}},
        )
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"weird14": "/opt/weird"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(
                    compiler_family="gcc", target="x86_64-linux-gnu", binding="weird14"
                ),
            )
        }
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 2
        assert any(
            "compiler_family" in e and "could not be determined" in e for e in errors
        )
        assert any("target" in e and "could not be determined" in e for e in errors)

    def test_target_only_declared_with_unidentifiable_family_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_metadata(
            monkeypatch,
            {"/opt/weird": {"selected": "/opt/weird", "version": "weird-tool 1.0"}},
        )
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"weird14": "/opt/weird"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(target="x86_64-linux-gnu", binding="weird14"),
            )
        }
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 1
        assert "could not be determined" in errors[0]

    def test_clang_cross_compile_target_is_exempt_from_triple_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: a single Clang binary is inherently multi-target via
        # --target= (the profile's own compose logic passes it explicitly);
        # its bare -dumpmachine probe only reports its host default, so
        # comparing it against a declared cross-compilation target
        # previously rejected an entirely valid profile.
        _stub_metadata(
            monkeypatch,
            {
                "/opt/clang": {
                    "selected": "/opt/clang",
                    "version": "clang version 18.1.3",
                    "target_triple": "x86_64-pc-linux-gnu",
                }
            },
        )
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"clang18": "/opt/clang"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(target="aarch64-linux-gnu", binding="clang18"),
            )
        }
        assert tp.check_profile_toolchain_identity(profiles, bf) == []

    def test_musl_vs_gnu_environment_mismatch_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: x86_64-linux-musl vs x86_64-linux-gnu share
        # architecture and OS family (both "linux"), so the earlier
        # architecture+OS-only check passed a genuinely incompatible libc.
        _stub_metadata(
            monkeypatch,
            {
                "/opt/musl-gcc": {
                    "selected": "/opt/musl-gcc",
                    "version": "gcc (Alpine 13.2.0) 13.2.0",
                    "target_triple": "x86_64-linux-musl",
                }
            },
        )
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"musl14": "/opt/musl-gcc"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(target="x86_64-linux-gnu", binding="musl14"),
            )
        }
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 1
        assert "environment" in errors[0]
        assert "musl" in errors[0]

    def test_mips_n64_vs_n32_environment_mismatch_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: mips64el-linux-gnuabi64 (N64) vs
        # mips64el-linux-gnuabin32 (N32) both contain "gnu" and previously
        # collapsed to the same generic environment, silently passing an
        # incompatible MIPS data model (Codex review, fresh evidence).
        _stub_metadata(
            monkeypatch,
            {
                "/opt/mips-gcc": {
                    "selected": "/opt/mips-gcc",
                    "version": "gcc 13.2.0",
                    "target_triple": "mips64el-linux-gnuabi64",
                }
            },
        )
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"mips14": "/opt/mips-gcc"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(
                    compiler_family="gcc",
                    target="mips64el-linux-gnuabin32",
                    binding="mips14",
                ),
            )
        }
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 1
        assert "environment" in errors[0]

    def test_unrecognized_declared_os_against_a_recognized_probed_os_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: a declared OS this module's marker table doesn't
        # recognize (e.g. Solaris) previously normalized to None, and the
        # comparison only fired when BOTH sides were non-None -- so an
        # unverifiable declared OS silently passed against ANY probed OS,
        # including a genuinely different real one (Codex review, fresh
        # evidence).
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
                    compiler_family="gcc",
                    target="x86_64-pc-solaris2.11",
                    binding="gcc14",
                ),
            )
        }
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 1
        assert "p1.compile.target" in errors[0]
        assert "OS" in errors[0]

    def test_unrecognized_declared_env_against_a_recognized_probed_env_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: a declared bare-metal environment (e.g. arm-none-eabi)
        # normalized to None, so it silently passed against any probed
        # environment, including an incompatible real glibc variant (Codex
        # review, fresh evidence).
        _stub_metadata(
            monkeypatch,
            {
                "/opt/gcc": {
                    "selected": "/opt/gcc",
                    "version": "gcc 13.2.0",
                    "target_triple": "arm-linux-gnueabi",
                }
            },
        )
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc14": "/opt/gcc"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(
                    compiler_family="gcc", target="arm-none-eabi", binding="gcc14"
                ),
            )
        }
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 1
        assert "p1.compile.target" in errors[0]
        assert "environment" in errors[0]

    def test_both_sides_unrecognized_but_identical_suffix_is_not_flagged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # When neither side's OS/environment marker is recognized but the
        # raw, unrecognized suffix is identical on both sides, there is
        # nothing to flag -- not a manufactured mismatch.
        _stub_metadata(
            monkeypatch,
            {
                "/opt/gcc": {
                    "selected": "/opt/gcc",
                    "version": "gcc 13.2.0",
                    "target_triple": "arm-none-eabi",
                }
            },
        )
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc14": "/opt/gcc"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(
                    compiler_family="gcc", target="arm-none-eabi", binding="gcc14"
                ),
            )
        }
        assert tp.check_profile_toolchain_identity(profiles, bf) == []

    def test_both_sides_unrecognized_and_differing_suffix_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: arm-none-eabi (bare-metal EABI) vs arm-none-elf
        # (bare-metal ELF) share the same architecture and neither
        # normalizes to a recognized OS/environment marker, so the
        # os_mismatch/env_mismatch checks alone stayed silent -- a genuine
        # ABI/object-format difference passed unconditionally as long as
        # architecture agreed (Codex review, fresh evidence).
        _stub_metadata(
            monkeypatch,
            {
                "/opt/gcc": {
                    "selected": "/opt/gcc",
                    "version": "gcc 13.2.0",
                    "target_triple": "arm-none-elf",
                }
            },
        )
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"gcc14": "/opt/gcc"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(
                    compiler_family="gcc", target="arm-none-eabi", binding="gcc14"
                ),
            )
        }
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 1
        assert "unrecognized target suffix" in errors[0]

    def test_clang_bogus_target_is_rejected_via_real_probe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: a Clang binding's target was exempt from ALL
        # verification, so a misspelled target: not-a-real-target passed
        # unconditionally.
        _stub_metadata(
            monkeypatch,
            {
                "/opt/clang": {
                    "selected": "/opt/clang",
                    "version": "clang version 18.1.3",
                    "sha256": "deadbeef",
                }
            },
        )
        monkeypatch.setattr(tp, "_clang_accepts_target", lambda path, digest, t: False)
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"clang18": "/opt/clang"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(target="not-a-real-target", binding="clang18"),
            )
        }
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 1
        assert "p1.compile.target" in errors[0]

    def test_clang_valid_target_via_real_probe_yields_no_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_metadata(
            monkeypatch,
            {
                "/opt/clang": {
                    "selected": "/opt/clang",
                    "version": "clang version 18.1.3",
                    "sha256": "deadbeef",
                }
            },
        )
        monkeypatch.setattr(tp, "_clang_accepts_target", lambda path, digest, t: True)
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"clang18": "/opt/clang"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(target="aarch64-linux-gnu", binding="clang18"),
            )
        }
        assert tp.check_profile_toolchain_identity(profiles, bf) == []

    def test_clang_target_probe_that_cannot_run_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regression: _clang_accepts_target returns None when the
        # controlled empty-translation-unit compile itself couldn't run
        # (timeout, OSError) -- e.g. a wrapper that answers --version fine
        # but hangs or fails on a real invocation. That was previously
        # treated as a silent pass, approving a completely unverified
        # target binding (Codex review, fresh evidence).
        _stub_metadata(
            monkeypatch,
            {
                "/opt/clang": {
                    "selected": "/opt/clang",
                    "version": "clang version 18.1.3",
                    "sha256": "deadbeef",
                }
            },
        )
        monkeypatch.setattr(tp, "_clang_accepts_target", lambda path, digest, t: None)
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"clang18": "/opt/clang"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(target="aarch64-linux-gnu", binding="clang18"),
            )
        }
        errors = tp.check_profile_toolchain_identity(profiles, bf)
        assert len(errors) == 1
        assert "p1.compile.target" in errors[0]
        assert "could not be probed" in errors[0]

    def test_clang_target_without_sha256_skips_the_probe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No sha256 in metadata (e.g. an unusual probe result) -- can't key
        # the probe cache, so skip rather than probe unconditionally.
        def _boom(*a, **k):
            raise AssertionError("must not probe without a digest")

        _stub_metadata(
            monkeypatch,
            {
                "/opt/clang": {
                    "selected": "/opt/clang",
                    "version": "clang version 18.1.3",
                }
            },
        )
        monkeypatch.setattr(tp, "_clang_accepts_target", _boom)
        bf = BindingsFile(schema=BINDINGS_SCHEMA, bindings={"clang18": "/opt/clang"})
        profiles = {
            "p1": _FakeProfile(
                id="p1",
                compile=_FakeCompileSpec(target="aarch64-linux-gnu", binding="clang18"),
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
