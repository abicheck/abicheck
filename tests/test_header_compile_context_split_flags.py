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

"""Sibling split of ``test_header_compile_context.py`` (which sits at its
2000-line hard cap) for Finding 2 (P2 review, ``discussion_r3787772666``):
split two-token ABI flags (``-target-abi <value>`` and siblings) losing
their operand when captured into ``CompileUnit.abi_relevant_flags``, and
the internal ``<flag>=<value>`` encoding being reconstructed back into two
literal argv tokens by the time it reaches a rendered ``CompileContext``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.buildsource.adapters.base import extract_abi_relevant_flags
from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.header_compile_context import (
    _split_operand_survivor,
    resolve_header_compile_context,
)
from abicheck.errors import HeaderCompileContextAmbiguousError


def _cu(**kwargs: object) -> CompileUnit:
    defaults: dict[str, object] = dict(
        id=f"cu://{kwargs.get('source', 'x')}",
        source="",
        directory="",
        target_id="",
        compiler="",
        language="CXX",
        standard="",
        defines={},
        undefines=[],
        include_paths=[],
        system_include_paths=[],
        sysroot=None,
        target_triple="",
        abi_relevant_flags=[],
    )
    defaults.update(kwargs)
    return CompileUnit(**defaults)  # type: ignore[arg-type]


def test_extract_abi_relevant_flags_preserves_target_abi_operand() -> None:
    """P2 review finding (``discussion_r3787772666``): a split ``-target-abi
    aapcs`` (two argv tokens, confirmed via a real ``clang -cc1 --help``)
    used to have only the bare ``-target-abi`` switch captured (it shares
    the ``-target`` prefix already matched by ``ABI_RELEVANT_FLAG_PREFIXES``),
    silently dropping the operand. Normalized into one internal
    ``-target-abi=aapcs`` token instead."""
    out = extract_abi_relevant_flags(["clang", "-target-abi", "aapcs", "-c", "t.c"])
    assert out == ["-target-abi=aapcs"]


def test_extract_abi_relevant_flags_preserves_sibling_split_operand_flags() -> None:
    """Sibling split two-token flags named in the finding's own follow-up
    ("check whether OTHER real cc1/clang flags follow this same shape") --
    ``-target-cpu``, ``-target-feature``, ``-target-linker-version``, all
    confirmed via the same real ``clang -cc1 --help``."""
    out = extract_abi_relevant_flags(
        [
            "clang",
            "-target-cpu",
            "cortex-a72",
            "-target-feature",
            "+neon",
            "-target-linker-version",
            "609",
            "-c",
            "t.c",
        ]
    )
    assert out == [
        "-target-cpu=cortex-a72",
        "-target-feature=+neon",
        "-target-linker-version=609",
    ]


def test_extract_abi_relevant_flags_target_abi_without_operand_falls_back_to_bare() -> (
    None
):
    """A dangling ``-target-abi`` with nothing following it (end of argv)
    degrades to the pre-fix bare-token capture, same as any other
    ABI_RELEVANT_FLAG_PREFIXES match with no operand available."""
    out = extract_abi_relevant_flags(["clang", "-c", "t.c", "-target-abi"])
    assert out == ["-target-abi"]


def test_extract_abi_relevant_flags_does_not_combine_bare_target_operand() -> None:
    """Deliberately NOT extended to bare ``-target``/``--sysroot``/
    ``-isysroot`` split forms (already covered by their own dedicated
    structured ``CompileUnit`` fields) -- only the bare switch is captured,
    exactly the pre-existing (still correct) behavior."""
    out = extract_abi_relevant_flags(["clang", "-target", "aarch64-linux-gnu", "-c"])
    assert out == ["-target"]


def test_extract_abi_relevant_flags_preserves_xclang_wrapped_target_abi() -> None:
    """P2 review finding (``discussion_r3788073752``): a real Clang driver
    invocation never passes a cc1-only split-operand flag bare -- each token
    is individually wrapped, ``-Xclang -target-abi -Xclang aapcs``. Before
    the fix, the bare two-token branch fired on ``-target-abi`` and consumed
    the *second* ``-Xclang`` as if it were the value, producing the corrupted
    ``-target-abi=-Xclang`` and silently dropping the real value (``aapcs``)
    one token later."""
    out = extract_abi_relevant_flags(
        ["clang", "-Xclang", "-target-abi", "-Xclang", "aapcs", "-c", "t.c"]
    )
    assert out == ["-Xclang -target-abi=aapcs"]
    assert "-target-abi=-Xclang" not in out


def test_extract_abi_relevant_flags_xclang_wrapping_covers_sibling_flags() -> None:
    """The same ``-Xclang``-wrapped shape for the sibling split-operand cc1
    flags named in the finding's own follow-up question."""
    out = extract_abi_relevant_flags(
        [
            "clang",
            "-Xclang",
            "-target-cpu",
            "-Xclang",
            "cortex-a72",
            "-Xclang",
            "-target-feature",
            "-Xclang",
            "+neon",
            "-c",
            "t.c",
        ]
    )
    assert out == [
        "-Xclang -target-cpu=cortex-a72",
        "-Xclang -target-feature=+neon",
    ]


def test_extract_abi_relevant_flags_xclang_wrapped_operand_missing_falls_back() -> None:
    """A dangling ``-Xclang -target-abi`` with no second ``-Xclang``/value
    wrapping following it (end of argv) must not consume tokens
    speculatively -- degrades to the bare-token capture on the next
    iteration instead, the same fallback the pre-existing bare two-token
    form already uses when its own operand is missing."""
    out = extract_abi_relevant_flags(["clang", "-c", "t.c", "-Xclang", "-target-abi"])
    assert out == ["-target-abi"]


def test_split_operand_survivor_reconstructs_xclang_wrapped_pair() -> None:
    """``_split_operand_survivor`` reconstructs the internal ``-Xclang
    -target-abi=aapcs`` encoding back into the full, real four-token argv
    form a compiler invocation actually needs -- never the bare unwrapped
    form, which a normal ``clang`` driver invocation rejects outright."""
    assert _split_operand_survivor("-Xclang -target-abi=aapcs") == [
        "-Xclang",
        "-target-abi",
        "-Xclang",
        "aapcs",
    ]


def test_split_operand_survivor_returns_unmodified_for_unrecognized_xclang_marker() -> (
    None
):
    """A flag carrying the internal ``-Xclang `` marker but not naming a
    recognized :data:`_SPLIT_OPERAND_ABI_FLAGS` member (which
    ``extract_abi_relevant_flags`` never actually produces, since it always
    checks membership before emitting the marker) is returned unmodified
    rather than mis-expanded -- a defensive fallback, not a reachable
    real-world input."""
    assert _split_operand_survivor("-Xclang -not-a-real-flag=x") == [
        "-Xclang -not-a-real-flag=x"
    ]


def test_resolve_renders_xclang_wrapped_survivor_as_four_complete_argv_tokens(
    tmp_path: Path,
) -> None:
    """End-to-end through ``resolve_header_compile_context``: the internal
    ``-Xclang -target-abi=aapcs`` encoding produced by
    ``extract_abi_relevant_flags`` on a real ``-Xclang``-wrapped compile unit
    argv is reconstructed into the correct, replayable four-token form."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    cu = _cu(
        source=str(src),
        directory=str(tmp_path),
        abi_relevant_flags=["-Xclang -target-abi=aapcs"],
    )
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    tokens = list(result.context.gcc_option_tokens)
    assert "-Xclang -target-abi=aapcs" not in tokens
    assert tokens.count("-Xclang") == 2
    idx = tokens.index("-target-abi")
    assert tokens[idx - 1] == "-Xclang"
    assert tokens[idx + 1] == "-Xclang"
    assert tokens[idx + 2] == "aapcs"


def test_resolve_renders_split_operand_survivor_as_two_complete_argv_tokens(
    tmp_path: Path,
) -> None:
    """The internal ``-target-abi=aapcs`` encoding (adapters.base.
    extract_abi_relevant_flags's normalized output, see above) must be
    reconstructed into two separate, literal argv tokens by the time it
    reaches the rendered ``CompileContext`` -- a single ``-target-abi=aapcs``
    token is not real clang syntax and would abort a replay."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text('#include "widget.h"\n', encoding="utf-8")
    cu = _cu(
        source=str(src),
        directory=str(tmp_path),
        abi_relevant_flags=["-target-abi=aapcs"],
    )
    ev = BuildEvidence(compile_units=[cu])
    result = resolve_header_compile_context(ev, [header])
    assert result.context is not None
    tokens = result.context.gcc_option_tokens
    # Two separate, syntactically complete tokens -- never the combined,
    # internal-only "-target-abi=aapcs" spelling.
    assert "-target-abi=aapcs" not in tokens
    idx = tokens.index("-target-abi")
    assert tokens[idx + 1] == "aapcs"


def test_resolve_disagreeing_target_abi_values_now_correctly_ambiguous(
    tmp_path: Path,
) -> None:
    """Before the fix, only the bare ``-target-abi`` switch (no value) was
    captured, so two units disagreeing purely on the ABI value read as
    identical and no ambiguity was ever raised. With the value preserved,
    a genuine disagreement is now detected."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    unit_a = _cu(
        source=str(src_a),
        directory=str(tmp_path),
        abi_relevant_flags=["-target-abi=aapcs"],
    )
    unit_b = _cu(
        source=str(src_b),
        directory=str(tmp_path),
        abi_relevant_flags=["-target-abi=aapcs-vfp"],
    )
    ev = BuildEvidence(compile_units=[unit_a, unit_b])
    with pytest.raises(HeaderCompileContextAmbiguousError):
        resolve_header_compile_context(ev, [header])


def test_resolve_agreeing_target_abi_values_stay_unambiguous(tmp_path: Path) -> None:
    """Companion positive case: two units agreeing on the SAME ``-target-abi``
    value must not be flagged ambiguous."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    unit_a = _cu(
        source=str(src_a),
        directory=str(tmp_path),
        abi_relevant_flags=["-target-abi=aapcs"],
    )
    unit_b = _cu(
        source=str(src_b),
        directory=str(tmp_path),
        abi_relevant_flags=["-target-abi=aapcs"],
    )
    ev = BuildEvidence(compile_units=[unit_a, unit_b])
    result = resolve_header_compile_context(ev, [header])
    assert result.matched is True
    assert result.matched_unit_count == 2
