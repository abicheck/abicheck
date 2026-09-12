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

"""`mode: compare`'s compile-context region against a *release-style*
operand (a directory or package), at the region level.

Split out of `test_action_compile_context_parity.py` — that module asks
whether each mode forwards the compile-context inputs at all, and its own
`architecture/debt.yaml` `no_growth` baseline is why these cases live in a
module of their own rather than growing it (AGENTS.md "Files that are
large": move responsibility out to a properly-owned module, never trim the
file to fit). It re-exports the region-extraction harness, which stays
there with the region markers it reads.

The question here is narrower and has one oracle: **packaging an operand
does not change the compile context.** `run.sh` used to reject these
inputs outright for a directory/package operand, citing a per-library
fan-out that "never threads the L2 compile context to each pair's header
dump" — untrue since `cli_resolve.resolve_directory_compile_context` made
the identical `resolve_compile_context` call the single-pair path makes.
So every case below measures the release-style run against the single-pair
run for the same inputs, never against a hand-written expectation.

The end-to-end half — the real `run.sh`, every input family, four release
operand spellings — is
`tests/test_action_run_sh_release_capability_parity.py`.
"""

from __future__ import annotations

from test_action_compile_context_parity import (
    _COMPARE_COMPILE_CONTEXT_START,
    _COMPARE_MODE_MARKER,
    _FULL_ENV,
    RUN_SH,
    _read_compile_config_overlay,
    _run_region,
)


class TestCompareReleaseStyleOperandCompileContext:
    def test_compare_forwards_compile_context_for_release_style_operand(
        self,
    ) -> None:
        """The inverse of the guard this case used to pin. The CLI does
        thread the both-sides compile context through the per-library
        release fan-out (``cli_resolve.resolve_directory_compile_context``
        makes the identical ``resolve_compile_context`` call the single-pair
        path makes), so rejecting these inputs here made a release
        comparison through the Action strictly less capable than the same
        comparison run through the CLI directly. The oracle is the
        *single-pair* overlay for the identical inputs, not a pinned
        constant: whatever the single pair synthesizes, the directory/
        package operand must synthesize too."""
        env = {
            **_FULL_ENV,
            "INPUT_OLD_LIBRARY": str(RUN_SH.parent),  # any real directory
            "INPUT_NEW_LIBRARY": "new.so",
        }
        release_cmd, stderr = _run_region(
            _COMPARE_MODE_MARKER, env, _COMPARE_COMPILE_CONTEXT_START
        )
        assert "not support" not in stderr
        assert "--config" in release_cmd
        single_cmd, _ = _run_region(
            _COMPARE_MODE_MARKER,
            {**_FULL_ENV, "INPUT_OLD_LIBRARY": "old.so", "INPUT_NEW_LIBRARY": "new.so"},
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert _read_compile_config_overlay(
            release_cmd
        ) == _read_compile_config_overlay(single_cmd)

    def test_compare_release_style_succeeds_when_context_unset(self) -> None:
        """Companion: a plain directory/package compare with no compile-
        context inputs configured synthesizes no overlay at all -- the same
        early return the single-pair shape takes, not an empty compile:
        block."""
        cmd, stderr = _run_region(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_OLD_LIBRARY": str(RUN_SH.parent),
                "INPUT_NEW_LIBRARY": "new.so",
            },
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert "not support" not in stderr
        assert "--config" not in cmd

    def test_compare_release_style_ast_frontend_auto_synthesizes_nothing(
        self,
    ) -> None:
        """ "auto" is the documented no-op spelling of ast-frontend -- it
        resolves to the same default castxml selection as leaving the input
        unset entirely (see the input's description in action.yml), so it
        synthesizes no overlay at all, on this operand shape exactly as on
        a single pair.

        This case used to assert only that the release overlay *equalled*
        the single-pair one, which was true while both wrongly produced an
        empty `{"compile": {}}` overlay -- parity held at the wrong value,
        and a `--config` reached the CLI that a run configuring nothing
        should never have put there (Codex review, PR #1233). The
        end-to-end half, including the same equivalence for `lang: c++`,
        is `tests/test_action_run_sh_release_capability_parity.py`'s
        `TestNoOpInputsSynthesizeNoOverlay`.
        """
        release_cmd, stderr = _run_region(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_OLD_LIBRARY": str(RUN_SH.parent),
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_AST_FRONTEND": "auto",
            },
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert "not support" not in stderr
        assert "--config" not in release_cmd
        single_cmd, _ = _run_region(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_OLD_LIBRARY": "old.so",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_AST_FRONTEND": "auto",
            },
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert single_cmd == release_cmd

    def test_compare_release_style_ast_frontend_clang_reaches_the_overlay(
        self,
    ) -> None:
        """Companion: an actual, non-"auto" frontend choice is now
        *forwarded* for a directory/package operand rather than rejected,
        and lands in the synthesized compile: block exactly as it does for
        a single pair."""
        cmd, stderr = _run_region(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_OLD_LIBRARY": str(RUN_SH.parent),
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_AST_FRONTEND": "clang",
            },
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert "not support" not in stderr
        assert _read_compile_config_overlay(cmd).get("frontend") == "clang"

    def test_compare_release_style_synthesizes_nothing_for_default_lang(
        self,
    ) -> None:
        """The default INPUT_LANG=c++ is not a user override, so it
        synthesizes no overlay at all -- for a directory/package operand
        exactly as for a single pair (and exactly as for ``dump`` two cases
        above)."""
        cmd, stderr = _run_region(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_OLD_LIBRARY": str(RUN_SH.parent),
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_LANG": "c++",
            },
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert "not support" not in stderr
        assert "--config" not in cmd

    def test_compare_release_style_forwards_non_default_lang(self) -> None:
        """Companion: an actual, non-default lang choice reaches the
        synthesized compile: block for a directory/package operand instead
        of tripping a guard -- the release fan-out threads it like any
        other both-sides compile-context value."""
        cmd, stderr = _run_region(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_OLD_LIBRARY": str(RUN_SH.parent),
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_LANG": "c",
            },
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert "not support" not in stderr
        assert _read_compile_config_overlay(cmd)["lang"] == "c"
