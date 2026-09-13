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

"""Descriptor values that contain a space survive parsing and re-splitting.

The parser whitespace-split every element's text, on a docstring claim that
"no supported element's values may contain a space". A Windows SDK include
path under `C:\\Program Files` falsifies that outright: one real path became
two nonexistent ones, and a `<defines>` value with whitespace became two
corrupted macros (Codex review).

Two halves that only work together, so both are tested here: the parser now
splits on *lines* for value elements (whitespace tokens only for
`<gcc_options>`, where that is genuinely the grammar), and
`_descriptor_compile_options` *quotes* what it emits -- every consumer of a
`gcc_options` string splits it again, so an unquoted `-I/some path` would be
re-broken one layer further down.

Bug class: a stated invariant about input shape that the input does not obey.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _descriptor_paths import descriptor_absolute, descriptor_include_flag

from abicheck._compiler_options import join_gcc_options, split_gcc_options
from abicheck.compat.descriptor import parse_descriptor
from abicheck.compat.multi_library_run import _descriptor_compile_options


def _descriptor(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "d.xml"
    lib = tmp_path / "libfoo.so"
    lib.write_bytes(b"\x7fELF" + b"\x00" * 60)
    header = tmp_path / "a.h"
    header.write_text("int f(void);\n")
    path.write_text(
        f"<version>1.0</version>\n<headers>\n  {header}\n</headers>\n"
        f"<libs>\n  {lib}\n</libs>\n{body}"
    )
    return path


class TestAValueMayContainASpace:
    def test_an_include_path_with_a_space_stays_one_path(self, tmp_path):
        desc = parse_descriptor(
            _descriptor(
                tmp_path,
                "<include_paths>\n  /opt/Program Files/inc\n</include_paths>\n",
            )
        )
        assert [str(p) for p in desc.include_paths] == [
            str(descriptor_absolute("/opt/Program Files/inc"))
        ]

    def test_a_define_with_a_space_stays_one_define(self, tmp_path):
        desc = parse_descriptor(
            _descriptor(tmp_path, "<defines>\n  NAME=a b\n</defines>\n")
        )
        assert desc.defines == ["NAME=a b"]

    def test_a_newline_separated_block_is_still_several_values(self, tmp_path):
        """The behaviour the whitespace split existed to provide, which the
        line split must not lose -- a real descriptor spells a list this way."""
        desc = parse_descriptor(
            _descriptor(
                tmp_path,
                "<skip_headers>\n  a.h\n  b.h\n  c.h\n</skip_headers>\n",
            )
        )
        assert desc.skip_headers == ["a.h", "b.h", "c.h"]

    def test_one_element_per_value_still_works(self, tmp_path):
        desc = parse_descriptor(
            _descriptor(
                tmp_path,
                "<skip_headers>a.h</skip_headers>\n<skip_headers>b.h</skip_headers>\n",
            )
        )
        assert desc.skip_headers == ["a.h", "b.h"]

    def test_gcc_options_keeps_the_shell_like_grammar(self, tmp_path):
        """The one element where whitespace tokenization *is* the grammar:
        `-I /path` is two tokens because that is how they reach the compiler."""
        desc = parse_descriptor(
            _descriptor(tmp_path, "<gcc_options>\n  -I /opt/inc -O2\n</gcc_options>\n")
        )
        assert desc.gcc_options == ["-I", "/opt/inc", "-O2"]


class TestEmittedFlagsSurviveReSplitting:
    """The second half: parsing a spaced path correctly buys nothing if the
    flag string built from it is re-broken by its own consumer."""

    def test_a_spaced_include_path_round_trips(self, tmp_path):
        desc = parse_descriptor(
            _descriptor(
                tmp_path,
                "<include_paths>\n  /opt/Program Files/inc\n</include_paths>\n",
            )
        )
        tokens = split_gcc_options(_descriptor_compile_options(desc))
        assert tokens == [descriptor_include_flag("/opt/Program Files/inc")]

    @pytest.mark.parametrize(
        "tokens",
        [
            ["-I/plain/inc"],
            ["-I/some path/inc"],
            ["-DA=1", "-DB=x y", "-std=c++17"],
            ["-I/a b", "-I/c d", "-DE=f g"],
            [],
        ],
    )
    def test_join_and_split_are_inverses(self, tokens):
        """The contract stated as a property over several shapes rather than
        pinned to the one reported path."""
        assert split_gcc_options(join_gcc_options(tokens)) == tokens

    def test_a_plain_token_is_not_gratuitously_quoted(self, tmp_path):
        """Quoting everything would round-trip fine and still be wrong: the
        emitted string is user-visible in diagnostics."""
        assert join_gcc_options(["-O2", "-DFOO=1"]) == "-O2 -DFOO=1"


class TestGccOptionsUsesTheSharedSplitter:
    """`<gcc_options>` is parsed with `split_gcc_options`, not `str.split`.

    A plain split broke a shell-valid quoted argument into fragments with
    quote characters still attached, and `_descriptor_compile_options` then
    quoted those already-corrupted fragments -- so the compiler received
    nonexistent include paths and malformed definitions (Codex review).

    It was also the one place that did not agree with `join_gcc_options`,
    which the same values are re-emitted through: parser and emitter had two
    different grammars for one string.

    Bug class: two sides of a round trip implemented independently.
    """

    def _tokens(self, tmp_path, text):
        desc = parse_descriptor(
            _descriptor(tmp_path, f"<gcc_options>\n  {text}\n</gcc_options>\n")
        )
        return desc.gcc_options

    def test_a_quoted_path_survives(self, tmp_path):
        assert self._tokens(tmp_path, '-I"/opt/Program Files/inc"') == [
            "-I/opt/Program Files/inc"
        ]

    def test_a_quoted_define_survives(self, tmp_path):
        assert self._tokens(tmp_path, '-DNAME="a b"') == ["-DNAME=a b"]

    def test_unquoted_whitespace_still_tokenises(self, tmp_path):
        """The behaviour the shell grammar is *for*, which must not be lost:
        `-I /path` is two tokens because that is how they reach a compiler."""
        assert self._tokens(tmp_path, "-I /opt/inc -O2") == ["-I", "/opt/inc", "-O2"]

    def test_parser_and_emitter_agree(self, tmp_path):
        """The round trip stated end to end, which is what was broken: parse a
        descriptor, emit its flags, re-split them, get the same tokens."""
        desc = parse_descriptor(
            _descriptor(
                tmp_path,
                '<gcc_options>\n  -I"/opt/Program Files/inc" -DNAME="a b" -O2\n'
                "</gcc_options>\n",
            )
        )
        emitted = _descriptor_compile_options(desc)
        assert split_gcc_options(emitted) == [
            "-I/opt/Program Files/inc",
            "-DNAME=a b",
            "-O2",
        ]
