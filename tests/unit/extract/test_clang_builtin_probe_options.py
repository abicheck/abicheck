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

"""The builtin-ABI probe measures the options the header parse ran with.

A clang header parse under ``-m32`` must probe the i386 builtins. The probe
used to receive only ``gcc_option_tokens``, which drops the ``gcc_options``
string, so it measured the host (LP64) and gave ``time_t`` an 8-byte fact on
a 32-bit build. The oracle here is the command line the parse emits, spelled
out by hand, not the helper's own implementation.
"""

from __future__ import annotations

import pytest

from abicheck.extract.headers.clang.backend import (
    parse_option_tokens,
    probe_option_tokens,
)


@pytest.mark.parametrize(
    ("gcc_options", "gcc_option_tokens", "expected"),
    [
        # The bug: a -m32 carried only by the string must reach the probe.
        ("-m32", (), ("-m32",)),
        (
            "-m32 -D_TIME_BITS=64 -D_FILE_OFFSET_BITS=64",
            (),
            (
                "-m32",
                "-D_TIME_BITS=64",
                "-D_FILE_OFFSET_BITS=64",
            ),
        ),
        # Tokens follow the string, as on the parse's own command line.
        (
            "-std=c99",
            ("--target=i386-linux-gnu",),
            (
                "-std=c99",
                "--target=i386-linux-gnu",
            ),
        ),
        # A tokens-only request is unchanged (the pre-existing path).
        (None, ("-I/opt/inc",), ("-I/opt/inc",)),
        # Nothing given: nothing forwarded.
        (None, (), ()),
        ("", (), ()),
    ],
)
def test_probe_receives_the_parse_options_in_command_line_order(
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    assert parse_option_tokens(gcc_options, gcc_option_tokens) == expected


@pytest.mark.parametrize("gcc_options", ["-m32", "-m32 -mfpmath=387"])
def test_an_ilp32_request_keeps_its_m32_for_the_probe(gcc_options: str) -> None:
    # Sibling of the reported case: whichever 32-bit flag set the parse was
    # given, the probe sees the -m32 that selects the target's builtins.
    assert "-m32" in parse_option_tokens(gcc_options, ())


@pytest.mark.parametrize(
    ("gcc_options", "gcc_option_tokens", "expected"),
    [
        # A C++ standard in the parse's options must not reach the C probe.
        ("-m32 -std=c++17", (), ("-m32",)),
        ("-std=c++20", ("-m32",), ("-m32",)),
        ("--std=c++17 -m32", (), ("-m32",)),
        # A C standard is also a language selector; the probe needs none of it.
        ("-std=c99 -m32", (), ("-m32",)),
        # Target and ABI flags survive, in their order.
        (
            "-m32 -D_TIME_BITS=64 -std=gnu11",
            ("--target=i386-linux-gnu",),
            ("-m32", "-D_TIME_BITS=64", "--target=i386-linux-gnu"),
        ),
        # Nothing to drop: unchanged.
        ("-m32", (), ("-m32",)),
        (None, (), ()),
    ],
)
def test_the_c_probe_drops_language_standards_and_keeps_target_options(
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    assert probe_option_tokens(gcc_options, gcc_option_tokens) == expected


@pytest.mark.parametrize("standard", ["-std=c++17", "--std=c++20", "-std=c99"])
def test_no_language_standard_reaches_the_probe_for_any_standard(standard: str) -> None:
    # Sibling of the reported C++ case: whichever standard the parse names,
    # the probe's options never carry it, and the -m32 it needs stays.
    options = probe_option_tokens(f"-m32 {standard}", ())
    assert standard not in options
    assert "-m32" in options


@pytest.mark.parametrize(
    ("gcc_options", "expected"),
    [
        # Separate-argument forced include: both the flag and its file are dropped.
        ("-include /tmp/prelude.hpp -m32", ("-m32",)),
        ("-imacros /tmp/macros.h -m32", ("-m32",)),
        # A precompiled-header include takes a separate operand: the path goes too.
        ("-include-pch /tmp/prelude.hpp.pch -m32", ("-m32",)),
        # Joined forms.
        ("-include/tmp/prelude.hpp -m32", ("-m32",)),
        ("--include=/tmp/prelude.hpp -m32", ("-m32",)),
        ("--imacros=/tmp/macros.h -m32", ("-m32",)),
        # Target and ABI flags beside a forced include survive, in order.
        (
            "-include /tmp/p.hpp -m32 -D_TIME_BITS=64 -std=c++17",
            ("-m32", "-D_TIME_BITS=64"),
        ),
    ],
)
def test_forced_includes_never_reach_the_c_probe(
    gcc_options: str, expected: tuple[str, ...]
) -> None:
    # A C++-only prelude makes the C probe fail to compile, discarding every
    # builtin fact; the probe needs the target and ABI flags, never the header.
    assert probe_option_tokens(gcc_options, ()) == expected
