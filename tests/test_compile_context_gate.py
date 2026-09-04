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

"""Primitive-level tests for ``workflows.artifact.compile_context_gate``,
split out of ``execute.py`` (ADR-063 Track 3, Codex review, PR #1047).
``test_dump_resolved_execution_context.py`` and
``test_service_compare_pipeline.py`` already cover this gate end-to-end
through the real dump/compare pipelines; these test the primitive directly,
per this repo's own "give a new reusable primitive its own property-style
tests" convention (root ``AGENTS.md``).
"""

from __future__ import annotations

from pathlib import Path

from abicheck.compile_context import CompileContext
from abicheck.model import AbiSnapshot
from abicheck.workflows.artifact.compile_context_gate import (
    SideCompileInput,
    resolved_pair_compile_contexts,
    side_effective_compile_context,
)


def _snapshot(*, from_headers: bool) -> AbiSnapshot:
    return AbiSnapshot(library="libfoo.so.1", version="1.0", from_headers=from_headers)


def _elf(tmp_path: Path, name: str = "lib.so") -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x7fELF" + b"\x00" * 200)
    return p


class TestSideEffectiveCompileContext:
    def test_returns_none_when_no_context_was_resolved(self, tmp_path):
        assert (
            side_effective_compile_context(
                None, _snapshot(from_headers=True), _elf(tmp_path), dump_manifest=None
            )
            is None
        )

    def test_returns_none_when_no_header_ast_parse_ran(self, tmp_path):
        ctx = CompileContext(gcc_option_tokens=("-std=c++20",))
        assert (
            side_effective_compile_context(
                ctx, _snapshot(from_headers=False), _elf(tmp_path), dump_manifest=None
            )
            is None
        )

    def test_returns_none_for_a_manifest_driven_side(self, tmp_path):
        ctx = CompileContext(gcc_option_tokens=("-std=c++20",))
        assert (
            side_effective_compile_context(
                ctx,
                _snapshot(from_headers=True),
                _elf(tmp_path),
                dump_manifest=object(),
            )
            is None
        )

    def test_returns_the_context_for_a_real_elf_path(self, tmp_path):
        ctx = CompileContext(gcc_option_tokens=("-std=c++20",))
        assert (
            side_effective_compile_context(
                ctx, _snapshot(from_headers=True), _elf(tmp_path), dump_manifest=None
            )
            is ctx
        )

    def test_returns_none_when_the_format_cannot_be_detected(self, tmp_path):
        ctx = CompileContext(gcc_option_tokens=("-std=c++20",))
        text_path = tmp_path / "not_a_binary.txt"
        text_path.write_text("hello\n", encoding="utf-8")
        assert (
            side_effective_compile_context(
                ctx, _snapshot(from_headers=True), text_path, dump_manifest=None
            )
            is None
        )

    def test_follows_a_linker_script_to_detect_the_real_target(self, tmp_path):
        """The regression this gate exists to fix (Codex review, PR #1047):
        a linker script's own bytes are text, but the real ELF target it
        resolves to is what actually drove the header-AST parse."""
        target = tmp_path / "libfoo.so.1"
        target.write_bytes(b"\x7fELF" + b"\x00" * 200)
        script = tmp_path / "libfoo.so"
        script.write_text("INPUT(libfoo.so.1)\n", encoding="utf-8")
        ctx = CompileContext(gcc_option_tokens=("-std=c++20",))

        assert (
            side_effective_compile_context(
                ctx, _snapshot(from_headers=True), script, dump_manifest=None
            )
            is ctx
        )


class TestResolvedPairCompileContexts:
    def test_both_sides_present_when_both_qualify(self, tmp_path):
        old_ctx = CompileContext(gcc_option_tokens=("-std=c++20",))
        new_ctx = CompileContext(gcc_option_tokens=("-std=c++17",))

        result = resolved_pair_compile_contexts(
            SideCompileInput(
                old_ctx, _snapshot(from_headers=True), _elf(tmp_path, "old.so")
            ),
            SideCompileInput(
                new_ctx, _snapshot(from_headers=True), _elf(tmp_path, "new.so")
            ),
        )

        assert result == {"old": old_ctx, "new": new_ctx}

    def test_a_side_is_absent_not_placeholder_valued_when_excluded(self, tmp_path):
        old_ctx = CompileContext(gcc_option_tokens=("-std=c++20",))

        result = resolved_pair_compile_contexts(
            SideCompileInput(
                old_ctx, _snapshot(from_headers=True), _elf(tmp_path, "old.so")
            ),
            SideCompileInput(
                None, _snapshot(from_headers=True), _elf(tmp_path, "new.so")
            ),
        )

        assert result == {"old": old_ctx}
        assert "new" not in result

    def test_empty_when_neither_side_qualifies(self, tmp_path):
        result = resolved_pair_compile_contexts(
            SideCompileInput(
                None, _snapshot(from_headers=False), _elf(tmp_path, "old.so")
            ),
            SideCompileInput(
                None, _snapshot(from_headers=False), _elf(tmp_path, "new.so")
            ),
        )

        assert result == {}
