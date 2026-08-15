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
