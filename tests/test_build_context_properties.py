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

"""Property-based round-trip tests for build_context.py's argv tokenizers.

The example-based tests in test_build_context.py caught real bugs in
_split_windows_command_line's backslash-escaping rules -- but they were also
the source of hand-verification arithmetic mistakes during development (see
AGENTS.md's session retrospective on this PR): computing the expected output
of N-backslashes-before-a-quote by hand is exactly the kind of thing a human
gets subtly wrong. These tests instead check a *property* automatically,
against an independent oracle, rather than a hand-computed expected value.

subprocess.list2cmdline() is Python's own stdlib implementation of the same
Microsoft C runtime argv-quoting convention _split_windows_command_line
implements the inverse of -- so round-tripping a Hypothesis-generated token
list through list2cmdline() (quote) then _split_windows_command_line() (split)
cross-checks our implementation against an independent, well-tested reference
instead of a hand-derived expected value.
"""

from __future__ import annotations

import shlex
import subprocess

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.build_context import _split_command_line, _split_windows_command_line

pytestmark = pytest.mark.slow

# Printable-ish tokens covering the characters _split_windows_command_line's
# rules actually branch on (space, tab, quote, backslash) plus ordinary text,
# so Hypothesis explores the escaping edge cases instead of only plain words.
_TOKEN_CHARS = st.characters(
    whitelist_categories=("Lu", "Ll", "Nd"),
    whitelist_characters=' \t"\\/:.-_',
)
_TOKENS = st.lists(st.text(alphabet=_TOKEN_CHARS, max_size=12), max_size=8)


@given(tokens=_TOKENS)
@settings(max_examples=300)
def test_windows_tokenizer_round_trips_through_list2cmdline(tokens: list[str]) -> None:
    """_split_windows_command_line(list2cmdline(tokens)) == tokens for any
    token list -- list2cmdline is the stdlib's own MS-quoting-convention
    encoder, independent of this module's implementation."""
    text = subprocess.list2cmdline(tokens)
    assert _split_windows_command_line(text) == tokens


@given(text=st.text(max_size=64))
@settings(max_examples=300)
def test_windows_tokenizer_never_raises(text: str) -> None:
    """Arbitrary (not necessarily list2cmdline-produced) text must never raise
    -- malformed quoting has no invalid state under the documented rules
    (existing docstring claim), unlike shlex.split which can raise ValueError
    on an unbalanced quote."""
    _split_windows_command_line(text)


@given(tokens=st.lists(st.text(alphabet=_TOKEN_CHARS, max_size=12), max_size=8))
@settings(max_examples=300)
def test_gnu_tokenizer_round_trips_through_shlex_join(tokens: list[str]) -> None:
    """The GNU/POSIX-shell branch is a thin delegation to shlex.split(), so
    this mostly cross-checks shlex.join()/shlex.split() agree with each other
    -- still a useful regression guard against this module's own dispatch
    logic (cl_style=False) silently routing to the wrong tokenizer."""
    text = shlex.join(tokens)
    assert _split_command_line(text, cl_style=False) == tokens
