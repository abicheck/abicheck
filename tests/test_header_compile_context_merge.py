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
2000-line hard cap) for section 3 of that file's own docstring:
``buildsource.l2_seed._merge_l3_compile_context``'s "derived leads, explicit
wins" precedence -- the fold that combines a P0.3 L3-derived
``CompileContext`` with a caller's own explicit one. Split out during the
P0.3 follow-up round 2 merge against ``main`` (which independently moved
``_merge_l3_compile_context`` from ``service_input_resolution`` into
``buildsource.l2_seed`` as part of its own PR C dedup, and added the
``forced_includes``/include-operand-dirs coverage below) to keep the parent
file under the cap once both branches' additions landed together, following
this repo's established sibling-split convention (e.g.
``test_call_graph_extra.py``, ``test_header_compile_context_split_flags.py``).

Covers ``_merge_l3_compile_context``'s trailing-token fold for
``sysroot``/``gcc_options``, the include-search first-match-wins reordering
(Codex review, PR #782), the attached-vs-spaced ``-I`` form distinction, and
``_include_operand_dirs``'s directory extraction for the AST cache key's
``extra_hash_dirs`` channel.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.compile_context import CompileContext


def test_merge_l3_compile_context_derived_leads_explicit_wins() -> None:
    from abicheck.buildsource.l2_seed import _merge_l3_compile_context

    derived = CompileContext(gcc_option_tokens=("-DFOO=1", "-fPIC"))
    explicit = CompileContext(gcc_option_tokens=("-DFOO=2",), sysroot=Path("/x"))
    merged = _merge_l3_compile_context(explicit, derived)
    assert merged is not None
    # Finding 2: explicit's structured `sysroot` is folded into a trailing
    # token (and the structured field cleared) so it lands strictly after
    # every derived token in the actually-rendered command, not before it.
    assert merged.gcc_option_tokens == (
        "-DFOO=1",
        "-fPIC",
        "--sysroot=/x",
        "-DFOO=2",
    )
    assert merged.sysroot is None


def test_merge_l3_compile_context_explicit_gcc_options_string_folded_after_derived() -> (
    None
):
    """Finding 2: the free-form ``gcc_options`` string channel, not just
    ``sysroot``, must also land after every derived token."""
    from abicheck.buildsource.l2_seed import _merge_l3_compile_context

    derived = CompileContext(gcc_option_tokens=("-DFOO=1",))
    explicit = CompileContext(gcc_options="-DFOO=2 -DBAR=3")
    merged = _merge_l3_compile_context(explicit, derived)
    assert merged is not None
    assert merged.gcc_option_tokens == ("-DFOO=1", "-DFOO=2", "-DBAR=3")
    assert merged.gcc_options is None


def test_merge_l3_compile_context_conflicting_sysroot_explicit_wins_in_rendered_command(
    tmp_path: Path,
) -> None:
    """End-to-end-shaped: build the actual castxml command from a merged
    context carrying a derived AND an explicit, conflicting sysroot, and
    assert the *last* --sysroot= token (the one that wins under real
    compiler last-flag-wins semantics) is the explicit one."""
    from abicheck.buildsource.l2_seed import _merge_l3_compile_context
    from abicheck.dumper_ast_config import _build_castxml_command

    derived = CompileContext(gcc_option_tokens=("--sysroot=/derived",))
    explicit = CompileContext(sysroot=Path("/explicit"))
    merged = _merge_l3_compile_context(explicit, derived)
    assert merged is not None
    cmd = _build_castxml_command(
        "g++",
        "gnu",
        [],
        tmp_path / "out.xml",
        tmp_path / "agg.h",
        sysroot=merged.sysroot,
        gcc_options=merged.gcc_options,
        gcc_option_tokens=merged.gcc_option_tokens,
        force_cpp=True,
    )
    sysroot_tokens = [tok for tok in cmd if tok.startswith("--sysroot=")]
    assert sysroot_tokens == ["--sysroot=/derived", "--sysroot=/explicit"]
    assert sysroot_tokens[-1] == "--sysroot=/explicit"  # last-flag-wins: explicit


def test_merge_l3_compile_context_explicit_include_search_wins_first_match() -> None:
    """Codex review, PR #782: unlike a macro/std/sysroot switch (last-flag-
    wins), an include search path is first-match-wins -- so an explicit
    -I/-isystem must search *before* a derived one, the opposite order from
    every other token this function merges."""
    from abicheck.buildsource.l2_seed import _merge_l3_compile_context

    derived = CompileContext(
        gcc_option_tokens=("-DFOO=1", "-I", "/build/inc", "-isystem", "/build/sys")
    )
    explicit = CompileContext(gcc_option_tokens=("-I", "/user/inc"))
    merged = _merge_l3_compile_context(explicit, derived)
    assert merged is not None
    assert merged.gcc_option_tokens == (
        "-DFOO=1",  # a non-include derived token: unaffected, still leads
        "-I",
        "/user/inc",  # explicit's own -I: now searches before derived's
        "-I",
        "/build/inc",
        "-isystem",
        "/build/sys",
    )


def test_merge_l3_compile_context_attached_include_form_stays_paired() -> None:
    """The attached spelling (-Idir, no space) is self-contained -- must not
    consume a following, unrelated token as if it were a spaced operand."""
    from abicheck.buildsource.l2_seed import _merge_l3_compile_context

    derived = CompileContext(gcc_option_tokens=("-I/build/inc", "-DFOO=1"))
    explicit = CompileContext()
    merged = _merge_l3_compile_context(explicit, derived)
    assert merged is not None
    # -DFOO=1 is not an include token and must not be swept into the
    # include group merely for following an attached -I entry.
    assert merged.gcc_option_tokens == ("-DFOO=1", "-I/build/inc")


def test_include_operand_dirs_extracts_spaced_and_attached_forms() -> None:
    """Codex review, PR #782: the AST cache key's extra_hash_dirs channel
    needs real directory Paths, not token strings, to stat -- covers both
    the spaced (-I dir) and attached (-Idir) spellings, and a non-include
    token contributes nothing."""
    from abicheck.buildsource.l2_seed import _include_operand_dirs

    dirs = _include_operand_dirs(
        ("-DFOO=1", "-I", "/build/inc", "-isystem/build/sys", "-fPIC")
    )
    assert dirs == (Path("/build/inc"), Path("/build/sys"))


def test_merge_l3_compile_context_none_derived_is_noop() -> None:
    from abicheck.buildsource.l2_seed import _merge_l3_compile_context

    explicit = CompileContext(gcc_options="-DX=1")
    assert _merge_l3_compile_context(explicit, None) is explicit


def test_merge_l3_compile_context_none_explicit_uses_derived() -> None:
    from abicheck.buildsource.l2_seed import _merge_l3_compile_context

    derived = CompileContext(gcc_option_tokens=("-std=c++20",))
    assert _merge_l3_compile_context(None, derived) is derived
