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

"""ADR-063 Phase 1: ``legacy_compile_db_matched`` is a signal independent of
``legacy_compile_db_tokens`` (Codex review, fresh evidence on PR #935).

``_seeded_includes_and_compile_context``'s returned ``applied`` boolean is
what ``_resolve_side_snapshot_impl`` gates ``AbiSnapshot.parsed_with_
build_context`` on (mirroring ``perform_elf_dump``'s own ``compile_db_
context_matched``/``l3_context_applied`` OR condition). An earlier version
of the ``legacy_compile_db_tokens`` threading folded the legacy match's
derived flags into the resolved ``CompileContext`` but left ``applied``
unchanged when the P0.3 fold itself did not match -- so a typed dump that
got real compile-database context purely from the legacy-match fallback
would still report ``parsed_with_build_context=False``, wrongly triggering
the ``header_parse_context_drift``/``header_build_context_mismatch``
advisory findings and wrongly failing a ``--depth build`` gate that legacy
CLI dump run would have satisfied. Doubly wrong for a compile unit that
matched the legacy auto-match but genuinely derived zero castxml flags
(possible when a matched TU carries no ABI-relevant ``-D``/``-I``/... at
all): an empty ``legacy_compile_db_tokens`` tuple is indistinguishable from
"never matched" without a separate signal.

These are fast, monkeypatch-based unit tests (no compiler needed) --
the true end-to-end proof against a real compile database lives in
``tests/test_legacy_compile_db_typed_threading.py`` (``integration``).
"""

from __future__ import annotations

from pathlib import Path

from abicheck.api_types import InputSpec
from abicheck.compile_context import CompileContext
from abicheck.service_compare_evidence import SideEvidence
from abicheck.workflows.artifact.resolve import _seeded_includes_and_compile_context


def _side_and_evidence(tmp_path: Path) -> tuple[InputSpec, SideEvidence]:
    header = tmp_path / "h.h"
    header.write_text("void f();\n", encoding="utf-8")
    side = InputSpec(path=tmp_path / "lib.so", sources=tmp_path, headers=(header,))
    evidence = SideEvidence(
        headers=[header], compile=None, collect_mode="off", dump_manifest=None
    )
    return side, evidence


class TestLegacyMatchedIsASeparateSignalFromTokens:
    def test_matched_with_zero_tokens_still_marks_applied(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """A compile unit the legacy match genuinely matched, but which
        derives no castxml flags at all, is still real build-context
        evidence -- `applied` must become True even though the token tuple
        stays empty."""

        def _fake_seed(*, pending_cleanups, **kwargs):
            return [], False, None, ()

        monkeypatch.setattr(
            "abicheck.buildsource.l2_seed.seed_includes_and_fold_compile_context",
            _fake_seed,
        )
        side, evidence = _side_and_evidence(tmp_path)

        includes, ctx, applied, cleanups = _seeded_includes_and_compile_context(
            side,
            evidence,
            legacy_compile_db_tokens=(),
            legacy_compile_db_matched=True,
        )
        assert applied is True

    def test_unmatched_with_zero_tokens_stays_unapplied(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """The default/no-legacy-match case: no tokens, not matched -- must
        stay `applied=False`, unchanged from before this parameter existed."""

        def _fake_seed(*, pending_cleanups, **kwargs):
            return [], False, None, ()

        monkeypatch.setattr(
            "abicheck.buildsource.l2_seed.seed_includes_and_fold_compile_context",
            _fake_seed,
        )
        side, evidence = _side_and_evidence(tmp_path)

        includes, ctx, applied, cleanups = _seeded_includes_and_compile_context(
            side, evidence, legacy_compile_db_tokens=(), legacy_compile_db_matched=False
        )
        assert applied is False
        assert ctx is None

    def test_matched_with_tokens_folds_flags_and_marks_applied(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """The common real case: the legacy match found a real header and
        derived real flags -- both the folded gcc_options and `applied` must
        reflect it."""

        def _fake_seed(*, pending_cleanups, **kwargs):
            return [], False, None, ()

        monkeypatch.setattr(
            "abicheck.buildsource.l2_seed.seed_includes_and_fold_compile_context",
            _fake_seed,
        )
        side, evidence = _side_and_evidence(tmp_path)

        includes, ctx, applied, cleanups = _seeded_includes_and_compile_context(
            side,
            evidence,
            legacy_compile_db_tokens=("-DWIDE=1",),
            legacy_compile_db_matched=True,
        )
        assert applied is True
        assert ctx is not None
        assert ctx.gcc_options == "-DWIDE=1"

    def test_fold_applying_wins_over_legacy_match_regardless_of_matched_flag(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Precedence, unchanged by this fix: when the P0.3 fold itself
        applies, its own result wins outright and the legacy match's tokens
        are discarded -- even if the caller also claims a legacy match
        (a real caller wouldn't pass both, but the precedence must hold
        regardless of what a future caller passes)."""

        def _fake_seed(*, pending_cleanups, **kwargs):
            return [], True, CompileContext(gcc_options="-DFOLD=1"), ()

        monkeypatch.setattr(
            "abicheck.buildsource.l2_seed.seed_includes_and_fold_compile_context",
            _fake_seed,
        )
        side, evidence = _side_and_evidence(tmp_path)

        includes, ctx, applied, cleanups = _seeded_includes_and_compile_context(
            side,
            evidence,
            legacy_compile_db_tokens=("-DLEGACY=1",),
            legacy_compile_db_matched=True,
        )
        assert applied is True
        assert ctx is not None
        assert ctx.gcc_options == "-DFOLD=1"
        assert "-DLEGACY=1" not in (ctx.gcc_options or "")
