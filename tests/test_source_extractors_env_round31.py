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

"""Sibling split for round 31's Codex review Finding 2 on
:mod:`abicheck.buildsource.source_extractors._argv` (this test-tree has no
``__init__.py``, so sibling test modules do not import from one another --
every split follows the established self-contained convention).

Finding 2: ``_fold_chdir_into_operands``'s round-30-Finding-4 suffix-only
source-file-operand detector (``_looks_like_source_file_operand``) never
recognized an EXTENSIONLESS source file paired with an explicit
``-x <language>`` flag (e.g. ``clang -x c++ generated -Iinclude`` --
``generated`` is a genuine source-file operand, its language stated
explicitly rather than inferred from a suffix it does not have). Left
un-rebased under ``env -C``, such an operand resolves to the wrong
(raw-directory) path downstream in ``include_graph.py``/
``preprocessor_scan.py``, losing the TU's include graph or macro evidence.
"""

from __future__ import annotations

import os

from abicheck.buildsource.source_extractors._argv import strip_launchers
from abicheck.buildsource.source_extractors._argv_shortopts import join_path_token


def test_strip_launchers_chdir_rebases_extensionless_source_after_dash_x() -> None:
    """``env -C build clang -x c++ generated -Iinclude`` -- ``generated``
    has no recognizable source-file suffix, but the preceding
    ``-x c++`` already states unambiguously that it IS the source file.
    Both the extensionless source operand and the trailing ``-Iinclude``
    must rebase onto the effective ``env -C`` directory."""
    result = strip_launchers(
        ["env", "-C", "build", "clang", "-x", "c++", "generated", "-Iinclude"]
    )
    assert result == [
        "clang",
        "-x",
        "c++",
        os.path.normpath(os.path.join("build", "generated")),
        "-I" + join_path_token("build", "include"),
    ]


def test_strip_launchers_chdir_rebases_extensionless_source_after_dash_x_c() -> None:
    """The same fix applies for a plain ``-x c`` (not just ``-x c++``) --
    the CONTEXT (any explicit ``-x <language>``), not the specific
    language spelling, is what licenses treating the trailing bare token
    as a source operand."""
    result = strip_launchers(["env", "-C", "build", "clang", "-x", "c", "generated"])
    assert result == [
        "clang",
        "-x",
        "c",
        os.path.normpath(os.path.join("build", "generated")),
    ]


def test_strip_launchers_chdir_negative_control_no_dash_x_extensionless_token_untouched() -> (
    None
):
    """Negative control (preserving round 30 Finding 4's safe-by-default
    behavior): an ordinary unrecognized, extensionless bare token with NO
    preceding ``-x`` is still left completely unchanged -- e.g. the
    ``-meabi <value>`` repro from round 30 Finding 4 itself, an
    unrecognized flag's own non-path value, must not be folded just
    because it happens to be a bare token."""
    result = strip_launchers(
        [
            "env",
            "-C",
            "build",
            "clang",
            "-target",
            "armv7-none-eabi",
            "-meabi",
            "gnu",
            "-c",
            "x.c",
        ]
    )
    assert result == [
        "clang",
        "-target",
        "armv7-none-eabi",
        "-meabi",
        "gnu",
        "-c",
        os.path.normpath(os.path.join("build", "x.c")),
    ]


def test_strip_launchers_chdir_dash_x_does_not_license_flag_shaped_tokens() -> None:
    """Negative control: seeing ``-x <language>`` earlier does not turn a
    later FLAG-shaped token (starting with ``-``/``/``) into a source
    operand -- only a bare, non-flag-shaped token is eligible at all, the
    same gate every other branch of this function already applies."""
    result = strip_launchers(
        ["env", "-C", "build", "clang", "-x", "c++", "-Wall", "generated"]
    )
    assert result == [
        "clang",
        "-x",
        "c++",
        "-Wall",
        os.path.normpath(os.path.join("build", "generated")),
    ]


def test_strip_launchers_chdir_no_env_prefix_dash_x_extensionless_negative_control() -> (
    None
):
    """Negative control: with no ``env -C`` prefix at all, an extensionless
    source operand after ``-x c++`` is passed through completely
    unchanged -- the pre-existing behavior for the common (no ``env``)
    case is unaffected by this fix."""
    argv = ["clang", "-x", "c++", "generated", "-Iinclude"]
    assert strip_launchers(argv) == argv
