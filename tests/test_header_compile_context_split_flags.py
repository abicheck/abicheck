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
    one token later.

    The encoded survivor carries no ``-Xclang`` marker at all (P2 review,
    "Canonicalize equivalent cc1 survivor spellings", fresh evidence) --
    identical to the bare-captured encoding for the same value, see
    :func:`test_extract_abi_relevant_flags_bare_and_xclang_wrapped_forms_encode_identically`
    below."""
    out = extract_abi_relevant_flags(
        ["clang", "-Xclang", "-target-abi", "-Xclang", "aapcs", "-c", "t.c"]
    )
    assert out == ["-target-abi=aapcs"]
    assert "-target-abi=-Xclang" not in out


def test_extract_abi_relevant_flags_xclang_wrapping_covers_sibling_flags() -> None:
    """The same ``-Xclang``-wrapped shape for the sibling split-operand cc1
    flags named in the finding's own follow-up question -- encoded without
    the ``-Xclang`` marker, same as the bare-captured form."""
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
        "-target-cpu=cortex-a72",
        "-target-feature=+neon",
    ]


def test_extract_abi_relevant_flags_bare_and_xclang_wrapped_forms_encode_identically() -> (
    None
):
    """Regression test for the P2 review finding "Canonicalize equivalent
    cc1 survivor spellings": a ``-target-abi aapcs`` captured bare (a direct
    ``-cc1`` invocation) and the identical value captured via an ordinary
    driver's ``-Xclang -target-abi -Xclang aapcs`` wrapping must produce the
    SAME internal encoding -- not two visually different strings for the
    same semantic value, which used to make
    ``header_compile_context._EffectiveContextSignature`` and
    ``adapters.base.derive_build_options`` (both of which compare/key on
    this raw string directly, never through ``split_operand_survivor``)
    treat two equivalent compile units as different."""
    bare = extract_abi_relevant_flags(
        ["clang", "-cc1", "-target-abi", "aapcs", "-c", "t.c"]
    )
    wrapped = extract_abi_relevant_flags(
        ["clang", "-Xclang", "-target-abi", "-Xclang", "aapcs", "-c", "t.c"]
    )
    assert bare == wrapped == ["-target-abi=aapcs"]


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


def test_resolve_renders_legacy_xclang_wrapped_survivor_as_four_complete_argv_tokens(
    tmp_path: Path,
) -> None:
    """End-to-end through ``resolve_header_compile_context``: the LEGACY
    ``-Xclang -target-abi=aapcs`` marker encoding -- what a pre-canonicalization
    revision of ``extract_abi_relevant_flags`` used to persist for a real
    ``-Xclang``-wrapped compile unit argv, and what could still appear in an
    evidence pack persisted by that earlier revision -- is still decoded and
    reconstructed into the correct, replayable four-token form. The current
    ``extract_abi_relevant_flags`` never produces this marker itself any
    more (see the canonicalization tests above); this test only pins
    backward-compatible decoding of it."""
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


def test_split_operand_survivor_reconstructs_bare_capture_as_xclang_wrapped() -> None:
    """P2 review finding, "Forward bare cc1 flags through the replay driver"
    (fresh evidence): the BARE internal encoding (``<flag>=<value>``, what
    ``extract_abi_relevant_flags`` records for a direct ``clang -cc1
    -target-abi aapcs`` capture) must ALSO reconstruct into the
    ``-Xclang``-wrapped four-token form, not the bare two-token form --
    every replay path invokes an ordinary Clang driver, never ``-cc1``
    directly, and an ordinary driver rejects bare ``-target-abi aapcs``
    outright ("unknown argument '-target-abi'; did you mean '-Xclang
    -target-abi'"). Both the bare and ``-Xclang``-wrapped internal
    encodings must converge on the identical replayable output."""
    assert _split_operand_survivor("-target-abi=aapcs") == [
        "-Xclang",
        "-target-abi",
        "-Xclang",
        "aapcs",
    ]


def test_resolve_renders_split_operand_survivor_as_xclang_wrapped_argv_tokens(
    tmp_path: Path,
) -> None:
    """The internal ``-target-abi=aapcs`` encoding (adapters.base.
    extract_abi_relevant_flags's normalized output for a bare ``-cc1``-style
    capture, see above) must be reconstructed into the ``-Xclang``-wrapped,
    driver-forwarded form by the time it reaches the rendered
    ``CompileContext`` -- a single ``-target-abi=aapcs`` token is not real
    clang syntax, and the bare two-token ``-target-abi aapcs`` form is
    rejected by the ordinary driver every replay path actually invokes."""
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
    tokens = list(result.context.gcc_option_tokens)
    # -Xclang-wrapped, driver-forwarded tokens -- never the combined,
    # internal-only "-target-abi=aapcs" spelling, and never the bare
    # two-token form an ordinary driver invocation rejects.
    assert "-target-abi=aapcs" not in tokens
    assert tokens.count("-Xclang") == 2
    idx = tokens.index("-target-abi")
    assert tokens[idx - 1] == "-Xclang"
    assert tokens[idx + 1] == "-Xclang"
    assert tokens[idx + 2] == "aapcs"


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


def test_resolve_bare_and_xclang_wrapped_captures_of_same_value_stay_unambiguous(
    tmp_path: Path,
) -> None:
    """Regression test for the P2 review finding "Canonicalize equivalent
    cc1 survivor spellings" (``abicheck/buildsource/adapters/base.py:772``):
    two compile units capturing the IDENTICAL ``-target-abi aapcs`` value
    through two different real argv shapes -- one a direct ``-cc1``
    invocation (bare two-token capture), the other an ordinary driver
    invocation forwarding the flag via ``-Xclang`` on both sides -- must
    resolve to the SAME ``_EffectiveContextSignature`` and therefore stay
    unambiguous. Before the fix, the two capture forms encoded to visibly
    different internal strings (``-target-abi=aapcs`` vs. ``-Xclang
    -target-abi=aapcs``), which ``_EffectiveContextSignature`` compares
    verbatim -- spuriously raising ``HeaderCompileContextAmbiguousError``
    for two units that mean exactly the same thing."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    unit_a = _cu(
        source=str(src_a),
        directory=str(tmp_path),
        abi_relevant_flags=extract_abi_relevant_flags(
            ["clang", "-cc1", "-target-abi", "aapcs", "-c", "a.c"]
        ),
    )
    unit_b = _cu(
        source=str(src_b),
        directory=str(tmp_path),
        abi_relevant_flags=extract_abi_relevant_flags(
            ["clang", "-Xclang", "-target-abi", "-Xclang", "aapcs", "-c", "b.c"]
        ),
    )
    ev = BuildEvidence(compile_units=[unit_a, unit_b])
    result = resolve_header_compile_context(ev, [header])
    assert result.matched is True
    assert result.matched_unit_count == 2


def test_resolve_bare_and_xclang_wrapped_captures_of_different_values_still_ambiguous(
    tmp_path: Path,
) -> None:
    """Companion negative case: a genuine disagreement in *value* must still
    be detected even when the two units captured it through different argv
    shapes -- canonicalizing the capture-form encoding must not accidentally
    also canonicalize away a real value difference."""
    header = tmp_path / "widget.h"
    header.write_text("struct Widget { int x; };\n", encoding="utf-8")
    src_a = tmp_path / "a.cpp"
    src_a.write_text('#include "widget.h"\n', encoding="utf-8")
    src_b = tmp_path / "b.cpp"
    src_b.write_text('#include "widget.h"\n', encoding="utf-8")
    unit_a = _cu(
        source=str(src_a),
        directory=str(tmp_path),
        abi_relevant_flags=extract_abi_relevant_flags(
            ["clang", "-cc1", "-target-abi", "aapcs", "-c", "a.c"]
        ),
    )
    unit_b = _cu(
        source=str(src_b),
        directory=str(tmp_path),
        abi_relevant_flags=extract_abi_relevant_flags(
            ["clang", "-Xclang", "-target-abi", "-Xclang", "aapcs-vfp", "-c", "b.c"]
        ),
    )
    ev = BuildEvidence(compile_units=[unit_a, unit_b])
    with pytest.raises(HeaderCompileContextAmbiguousError):
        resolve_header_compile_context(ev, [header])
