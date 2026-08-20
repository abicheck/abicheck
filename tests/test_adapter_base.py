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

"""Shared adapter helpers — dialect-aware source detection (ADR-029)."""

from __future__ import annotations

import os

from abicheck.buildsource.adapters.base import (
    _is_msvc_command,
    msvc_driver_token,
    source_from_argv,
)


def test_msvc_command_detected_by_slash_c():
    assert _is_msvc_command(["cl.exe", "/c", "foo.cc"]) is True


def test_msvc_command_detected_by_driver_basename_without_slash_c():
    # No `/c` marker — a clang-cl/cl driver basename alone marks MSVC dialect,
    # even when the driver is a full path.
    assert _is_msvc_command(["clang-cl", "/FIconfig.hpp", "foo.cc"]) is True
    assert _is_msvc_command([r"C:\VS\bin\cl.exe", "foo.cc"]) is True


def test_msvc_command_scan_stops_at_shell_separator():
    # The driver scan must not cross a `&&`/`;` into a following command, so a
    # GNU compile chained after `cd` stays GNU dialect.
    assert _is_msvc_command(["cd", "sub", "&&", "gcc", "-c", "x.c"]) is False


def test_msvc_driver_token_finds_driver_behind_launcher():
    # P2 review finding (discussion_r3788073756): a compiler-cache/launcher
    # wrapper (sccache/ccache/distcc/...) commonly precedes the real driver,
    # so the matched MSVC driver token must not be assumed to be argv[0].
    argv = ["sccache", "clang-cl", "/std:c++20", "-c", "foo.cc"]
    assert _is_msvc_command(argv) is True
    assert msvc_driver_token(argv) == "clang-cl"


def test_msvc_driver_token_is_argv0_when_no_launcher():
    assert msvc_driver_token(["clang-cl", "/std:c++20", "foo.cc"]) == "clang-cl"


def test_msvc_driver_token_full_path_returned_verbatim():
    assert msvc_driver_token([r"C:\VS\bin\cl.exe", "foo.cc"]) == r"C:\VS\bin\cl.exe"


def test_msvc_driver_token_none_when_only_slash_c_marker():
    # A bare `/c` marks MSVC dialect but names no cl/clang-cl-basename token
    # anywhere in argv, so there is nothing more specific than argv[0] to
    # point to -- the caller falls back to argv[0] itself in that case.
    assert msvc_driver_token(["/c", "foo.cc"]) is None


def test_msvc_driver_token_none_for_non_msvc_command():
    assert msvc_driver_token(["gcc", "-c", "foo.c"]) is None


def test_msvc_driver_token_keeps_first_match_when_argv_names_two():
    # An unusual argv naming a cl/clang-cl-basename token twice (e.g. a
    # wrapper re-invoking itself) must keep the first match, not the last.
    argv = ["clang-cl", "cl.exe", "/std:c++20", "foo.cc"]
    assert msvc_driver_token(argv) == "clang-cl"


def test_msvc_driver_token_recognizes_versioned_clang_cl_behind_launcher():
    # P2 review finding (launcher-wrapped supported CL-style alias
    # follow-up): a versioned `clang-cl-20` behind a launcher (sccache) must
    # be recognized as the MSVC driver token -- the same broader
    # recognition `dumper_clang._is_cl_style_driver_name` already applies
    # elsewhere -- not just the four exact `_MSVC_DRIVERS` spellings.
    argv = ["sccache", "clang-cl-20", "/c", "foo.cc"]
    assert _is_msvc_command(argv) is True
    assert msvc_driver_token(argv) == "clang-cl-20"


def test_msvc_driver_token_recognizes_dpcpp_cl_behind_launcher():
    # Same finding, Intel's dpcpp-cl alias (also `-cl`-suffixed, recognized
    # by `_is_clang_family_binary`/`_is_cl_style_driver_name` elsewhere).
    argv = ["sccache", "dpcpp-cl", "/c", "foo.cc"]
    assert _is_msvc_command(argv) is True
    assert msvc_driver_token(argv) == "dpcpp-cl"


def test_msvc_driver_token_versioned_clang_cl_full_path():
    # A versioned clang-cl behind a launcher, given as a full (Windows-style)
    # path -- the version-suffix stripping happens on the basename, not the
    # whole path.
    argv = ["sccache", r"C:\LLVM\bin\clang-cl-18.exe", "/c", "foo.cc"]
    assert _is_msvc_command(argv) is True
    assert msvc_driver_token(argv) == r"C:\LLVM\bin\clang-cl-18.exe"


def test_source_from_argv_msvc_combined_forced_include_rejected():
    # /FIconfig.hpp is a combined forced-include option, never the TU.
    assert source_from_argv(["clang-cl", "/FIconfig.hpp", "foo.cc"]) == "foo.cc"


def test_msvc_driver_scan_does_not_mistake_cl_suffixed_source_for_driver():
    # P2 review finding ("Limit CL-driver matching to executable tokens"):
    # a GNU translation unit whose source basename ends in "-cl" (e.g.
    # foo-cl.cpp) must not be mistaken for a CL-style driver just because
    # `_is_cl_style_driver_name` is a bare name-suffix check applied to
    # every argv token -- the match must be restricted to the command's own
    # leading executable position(s).
    argv = ["gcc", "-c", "foo-cl.cpp"]
    assert _is_msvc_command(argv) is False
    assert msvc_driver_token(argv) is None


def test_msvc_driver_scan_does_not_mistake_cl_suffixed_source_behind_launcher():
    # Same finding, with a recognized launcher wrapper at argv[0] -- the
    # scan must still only consider argv[0]/argv[1] (the launcher and the
    # real compiler), never the source operand that follows.
    argv = ["sccache", "gcc", "-c", "foo-cl.cpp"]
    assert _is_msvc_command(argv) is False
    assert msvc_driver_token(argv) is None


def test_msvc_driver_scan_still_finds_real_driver_when_source_is_cl_suffixed():
    # Companion positive case: a genuine clang-cl driver at argv[0] must
    # still be recognized even when the TU it compiles happens to have a
    # "-cl"-suffixed basename.
    argv = ["clang-cl", "/std:c++20", "/c", "foo-cl.cpp"]
    assert _is_msvc_command(argv) is True
    assert msvc_driver_token(argv) == "clang-cl"


def test_source_from_argv_tp_space_separated_form():
    # `/Tp <file>` (space-separated) names the C++ TU explicitly.
    assert source_from_argv(["cl.exe", "/c", "/Tp", "foo.cc", "/Fofoo.obj"]) == "foo.cc"


def test_msvc_driver_token_recognizes_buildcache_launcher():
    # Codex review, "Reuse the complete launcher parser when locating
    # clang-cl": the hard-coded 3-name launcher list (sccache/ccache/distcc)
    # this scan previously used immediately drifted from
    # source_extractors._argv.COMPILER_LAUNCHERS's six recognized launchers
    # (which also include icecc/icerun/buildcache), so a real
    # `buildcache clang-cl /std:c++20 /c x.cc` command was not recognized as
    # MSVC-dialect at all -- the driver scan never looked past position 1's
    # fixed launcher set, and buildcache wasn't in it.
    argv = ["buildcache", "clang-cl", "/std:c++20", "/c", "x.cc"]
    assert _is_msvc_command(argv) is True
    assert msvc_driver_token(argv) == "clang-cl"


def test_msvc_driver_token_recognizes_icecc_and_icerun_launchers():
    # Same finding: the other two launchers COMPILER_LAUNCHERS names beyond
    # the old hard-coded set.
    assert msvc_driver_token(["icecc", "clang-cl", "/c", "foo.cc"]) == "clang-cl"
    assert msvc_driver_token(["icerun", "clang-cl", "/c", "foo.cc"]) == "clang-cl"


def test_msvc_driver_token_resolves_relative_driver_via_env_chdir():
    # P2 review round 19 Finding 1: `msvc_driver_token` (through
    # `_msvc_driver_scan`) must return the *stripped* driver token
    # `strip_launchers` computed -- which now folds an `env -C DIR` prefix
    # into a relative driver path -- not the raw, unfolded `argv[i]`.
    #
    # Normalized with the token's own (POSIX, forward-slash) grammar,
    # not the host's (Codex review, "Recognize foreign absolute driver
    # paths", fresh evidence) -- a literal forward-slash string, not
    # `os.path.normpath(...)`, which would self-referentially flip to
    # backslashes on a Windows host instead of pinning the actually
    # host-independent result.
    argv = ["env", "-C", "build", "../llvm/bin/clang-cl", "/std:c++20", "/c", "foo.cc"]
    assert msvc_driver_token(argv) == "llvm/bin/clang-cl"


def test_msvc_driver_token_resolves_bare_driver_via_env_path(tmp_path):
    # P2 review round 19 Finding 2: same, for a bare driver name only
    # resolvable through an `env PATH=...` override.
    import stat
    import sys

    driver = tmp_path / "clang-cl"
    # `shutil.which` consults PATHEXT on Windows rather than an exact
    # bare-name match, so an extensionless fixture is never found there
    # (CodeRabbit review, fresh evidence) -- create `clang-cl.exe` on
    # Windows and assert against *that* path; the compiler token in argv
    # stays the bare "clang-cl" (PATHEXT resolution finds the .exe file).
    if sys.platform == "win32":
        driver = driver.with_suffix(".exe")
    driver.write_text("", encoding="utf-8")
    driver.chmod(driver.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    argv = ["env", f"PATH={tmp_path}", "clang-cl", "/c", "foo.cc"]
    # `shutil.which` on Windows returns the resolved path with PATHEXT's own
    # extension casing (commonly uppercase `.EXE`), not necessarily matching
    # the fixture file's own on-disk casing -- both name the same file on a
    # case-insensitive filesystem (live Windows CI signal, fresh evidence).
    # `os.path.normcase` is a no-op on POSIX, so this stays exact there.
    assert os.path.normcase(msvc_driver_token(argv)) == os.path.normcase(str(driver))


# -- Round 26, Finding 2 (Codex): `directory` must reach `_msvc_driver_scan` --


def test_msvc_driver_token_resolves_relative_env_path_when_directory_given(tmp_path):
    # `env -C build PATH=../tool clang-cl /c x.cc` recorded for a compile
    # unit whose own directory is `tmp_path`: GNU env chdirs into
    # `tmp_path/build` before searching the RELATIVE `../tool` PATH entry,
    # landing on `tmp_path/tool/clang-cl` -- not resolvable from abicheck's
    # own process CWD. Before this fix, `msvc_driver_token` had no
    # `directory` parameter at all, so this relative PATH entry could never
    # be composed correctly regardless of what the caller knew.
    import stat
    import sys

    (tmp_path / "build").mkdir()
    tool_dir = tmp_path / "tool"
    tool_dir.mkdir()
    driver = tool_dir / "clang-cl"
    if sys.platform == "win32":
        driver = driver.with_suffix(".exe")
    driver.write_text("", encoding="utf-8")
    driver.chmod(driver.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    argv = ["env", "-C", "build", "PATH=../tool", "clang-cl", "/c", "x.cc"]
    resolved = msvc_driver_token(argv, directory=str(tmp_path))
    assert os.path.normcase(resolved) == os.path.normcase(str(driver))


def test_msvc_driver_token_relative_env_path_left_bare_without_directory(tmp_path):
    # Negative control: the identical argv as above, but with no `directory`
    # supplied (the pre-fix call shape, and every caller with no compile-unit
    # context) -- a relative PATH entry cannot be safely resolved without a
    # base directory, so the bare driver name is left unchanged, exactly the
    # existing `strip_launchers`-level behavior this function delegates to.
    import stat
    import sys

    (tmp_path / "build").mkdir()
    tool_dir = tmp_path / "tool"
    tool_dir.mkdir()
    driver = tool_dir / "clang-cl"
    if sys.platform == "win32":
        driver = driver.with_suffix(".exe")
    driver.write_text("", encoding="utf-8")
    driver.chmod(driver.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    argv = ["env", "-C", "build", "PATH=../tool", "clang-cl", "/c", "x.cc"]
    assert msvc_driver_token(argv) == "clang-cl"


def test_msvc_driver_token_behind_chained_launchers():
    # source_extractors._argv.strip_launchers unwraps a chain of launcher
    # tokens (and any per-launcher `KEY=VALUE` config override) in one pass;
    # reusing it (rather than a fixed {0, 1} position pair) means a chained
    # launcher invocation is handled too, not just a single wrapper.
    argv = ["sccache", "buildcache", "clang-cl", "/std:c++20", "/c", "foo.cc"]
    assert _is_msvc_command(argv) is True
    assert msvc_driver_token(argv) == "clang-cl"


def test_msvc_driver_token_behind_launcher_config_override():
    # ccache's own `KEY=VALUE` per-invocation config-override tokens (ccache
    # manual, "Configuration" section) must be skipped too when locating the
    # real driver behind a launcher.
    argv = ["ccache", 'compiler_check="%compiler% --version"', "clang-cl", "/c", "x.cc"]
    assert _is_msvc_command(argv) is True
    assert msvc_driver_token(argv) == "clang-cl"


def test_source_from_argv_tp_without_valid_operand_is_skipped():
    # A trailing `/Tp` with no source-like operand consumes its slot and yields
    # no source (rather than misreading the next option).
    assert source_from_argv(["cl.exe", "/c", "/Tp"]) == ""


def test_source_from_argv_gnu_absolute_path_kept_behind_cd_prefix():
    # source_from_argv tolerates a `cd dir && cc …` argv: the GNU absolute path
    # is still recovered and the `&&` does not flip the command to MSVC dialect.
    assert (
        source_from_argv(["cd", "sub", "&&", "gcc", "-c", "/work/src/x.c"])
        == "/work/src/x.c"
    )


def test_msvc_driver_token_finds_driver_behind_env_prefix():
    # Codex review finding (round 18, "Unwrap environment prefixes before
    # locating clang-cl"): a compile unit recorded as
    # `env SDKROOT=... /opt/llvm/bin/clang-cl /c ...` must resolve
    # `clang-cl` as the driver, not `env` -- source_extractors._argv.
    # strip_launchers (which _executable_token_positions/_msvc_driver_scan
    # both go through) now unwraps a leading `env` invocation and its
    # `NAME=VALUE` assignments ahead of locating the real driver token.
    argv = ["env", "SDKROOT=/opt/sdk", "/opt/llvm/bin/clang-cl", "/c", "x.cc"]
    assert _is_msvc_command(argv) is True
    assert msvc_driver_token(argv) == "/opt/llvm/bin/clang-cl"


def test_msvc_driver_token_finds_driver_behind_env_with_multiple_assignments():
    argv = ["env", "FOO=1", "BAR=2", "clang-cl", "/c", "x.cc"]
    assert _is_msvc_command(argv) is True
    assert msvc_driver_token(argv) == "clang-cl"


def test_msvc_driver_token_finds_driver_behind_env_chained_with_launcher():
    # env's own prefix precedes a compiler-cache launcher, which in turn
    # precedes the real driver -- both prefix kinds must unwrap together.
    argv = ["env", "FOO=1", "sccache", "clang-cl", "/c", "x.cc"]
    assert _is_msvc_command(argv) is True
    assert msvc_driver_token(argv) == "clang-cl"


def test_msvc_driver_token_none_for_env_wrapped_gnu_command():
    # Companion negative case: env-wrapping an ordinary GNU command must not
    # spuriously flip it to MSVC dialect.
    argv = ["env", "FOO=1", "gcc", "-c", "foo.c"]
    assert _is_msvc_command(argv) is False
    assert msvc_driver_token(argv) is None


def test_derived_gcc_path_resolves_driver_behind_env_prefix():
    # End-to-end: header_compile_context._derived_gcc_path (the consumer
    # msvc_driver_token exists for) must resolve the real clang-cl driver,
    # not "env", for an env-wrapped MSVC-dialect compile unit -- otherwise
    # dumper_clang._resolve_clang_bin rejects "env" as not clang-family and
    # silently falls back to plain clang++, losing the recorded toolchain's
    # built-ins, default headers, and target defaults.
    from abicheck.buildsource.build_evidence import CompileUnit
    from abicheck.buildsource.header_compile_context import _derived_gcc_path

    cu = CompileUnit(
        id="cu://x.cc#cfg:test",
        source="x.cc",
        language="CXX",
        directory="/work",
        output="x.obj",
        argv=[
            "env",
            "SDKROOT=/opt/sdk",
            "/opt/llvm/bin/clang-cl",
            "/std:c++20",
            "/c",
            "x.cc",
        ],
    )
    assert _derived_gcc_path(cu) == "/opt/llvm/bin/clang-cl"


def test_sources_from_argv_returns_every_tu():
    from abicheck.buildsource.adapters.base import sources_from_argv

    # A multi-source compile yields all TU operands, in order; forced-include
    # operands and the output are not mistaken for sources.
    assert sources_from_argv(["g++", "-std=c++17", "-c", "a.cpp", "b.cpp"]) == [
        "a.cpp",
        "b.cpp",
    ]
    assert sources_from_argv(["gcc", "-include", "cfg.h", "-c", "a.c", "b.c"]) == [
        "a.c",
        "b.c",
    ]
    # source_from_argv stays the first element.
    assert source_from_argv(["g++", "-c", "a.cpp", "b.cpp"]) == "a.cpp"


# -- Round 29 (Codex): normalize legacy target features before mode --------
# -- classification ----------------------------------------------------------


def test_legacy_xclang_target_feature_survivor_matches_canonical_shape():
    """A legacy persisted-pack survivor ``-Xclang -target-feature=+sse2``
    (the pre-canonicalization ``-Xclang``-marked spelling) and a freshly
    produced ``-target-feature=+sse2`` survivor must classify into the
    IDENTICAL ``(key, value)`` ``BuildOption`` shape -- otherwise
    ``build_diff`` reads a legacy-collected pack and a freshly-collected
    one as a spurious remove+add for the same effective compiler state."""
    from abicheck.buildsource.adapters.base import derive_build_options
    from abicheck.buildsource.build_evidence import CompileUnit

    cu_legacy = CompileUnit(
        id="cu://legacy.cc",
        source="legacy.cc",
        language="CXX",
        directory="/work",
        output="legacy.o",
        abi_relevant_flags=["-Xclang -target-feature=+sse2"],
    )
    cu_fresh = CompileUnit(
        id="cu://fresh.cc",
        source="fresh.cc",
        language="CXX",
        directory="/work",
        output="fresh.o",
        abi_relevant_flags=["-target-feature=+sse2"],
    )
    legacy_opts = {(o.key, o.value) for o in derive_build_options([cu_legacy])}
    fresh_opts = {(o.key, o.value) for o in derive_build_options([cu_fresh])}
    assert legacy_opts == fresh_opts == {("target_feature:sse2", "+sse2")}


def test_legacy_xclang_target_feature_last_wins_still_applies():
    """The last-wins per-feature-name semantics (round 27 Finding 3) still
    apply correctly when the sequence mixes legacy-marked and canonical
    survivor spellings for the SAME feature name."""
    from abicheck.buildsource.adapters.base import derive_build_options
    from abicheck.buildsource.build_evidence import CompileUnit

    cu = CompileUnit(
        id="cu://mixed.cc",
        source="mixed.cc",
        language="CXX",
        directory="/work",
        output="mixed.o",
        abi_relevant_flags=[
            "-Xclang -target-feature=+sse2",
            "-target-feature=-sse2",
        ],
    )
    opts = {(o.key, o.value) for o in derive_build_options([cu])}
    assert opts == {("target_feature:sse2", "-sse2")}


def test_legacy_xclang_target_feature_negative_control_unrelated_flag_unaffected():
    """Negative control: an ordinary, unrelated ``-Xclang``-wrapped flag
    that is NOT a split-operand-ABI-flag survivor at all is completely
    unaffected by the new strip -- it still falls through to the generic
    option path exactly as before."""
    from abicheck.buildsource.adapters.base import derive_build_options
    from abicheck.buildsource.build_evidence import CompileUnit

    cu = CompileUnit(
        id="cu://unrelated.cc",
        source="unrelated.cc",
        language="CXX",
        directory="/work",
        output="unrelated.o",
        abi_relevant_flags=["-Xclang -some-other-cc1-flag"],
    )
    opts = [(o.key, o.value) for o in derive_build_options([cu])]
    assert opts == [("-Xclang -some-other-cc1-flag", "-Xclang -some-other-cc1-flag")]
