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


def test_source_from_argv_msvc_combined_forced_include_rejected():
    # /FIconfig.hpp is a combined forced-include option, never the TU.
    assert source_from_argv(["clang-cl", "/FIconfig.hpp", "foo.cc"]) == "foo.cc"


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
    assert source_from_argv(["cd", "sub", "&&", "gcc", "-c", "/work/src/x.c"]) == "/work/src/x.c"
