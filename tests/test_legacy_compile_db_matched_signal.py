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

A second review round on the fix above (still fresh evidence on PR #935,
same commit range) found the complementary gap: a caller that passes
non-empty ``legacy_compile_db_tokens`` while leaving ``legacy_compile_db_
matched`` at its default ``False`` -- exactly the shape
``tests/test_legacy_compile_db_typed_threading.py``'s own end-to-end caller
uses -- still got ``applied=False`` even though the non-empty tokens are
themselves proof a match occurred. ``_legacy_compile_db_achieved`` closes
this by treating non-empty tokens as sufficient evidence on their own,
independent of whether ``matched`` was also passed; see
``test_tokens_alone_without_explicit_matched_flag_still_marks_applied``.

A third review round (still PR #935, commit ``8f2c22d``) found a distinct
bug in the same neighborhood: ``_fold_legacy_compile_db_tokens`` used to
``" ".join()`` the already-split argv tokens into the free-form
``gcc_options`` string, which every consumer later re-splits via
``split_gcc_options``. A token containing embedded whitespace (a Windows
SDK include path with a space, or a compile-db ``-DNAME=a b`` define)
silently split back into the wrong number of tokens, corrupting the
derived flag. Fixed by routing the tokens through ``gcc_option_tokens``
(verbatim argv entries, never re-parsed) instead -- see
``TestWhitespaceBearingTokensSurviveTheFold`` below.
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
        derived real flags -- both the folded tokens and `applied` must
        reflect it. The tokens ride in `gcc_option_tokens` (verbatim argv
        entries), not joined into the `gcc_options` string -- see
        `_fold_legacy_compile_db_tokens`'s own docstring for why."""

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
        assert ctx.gcc_option_tokens == ("-DWIDE=1",)
        assert ctx.gcc_options is None

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

    def test_tokens_alone_without_explicit_matched_flag_still_marks_applied(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Codex review, second round: a caller may pass non-empty
        `legacy_compile_db_tokens` while leaving `legacy_compile_db_matched`
        at its default `False` -- the shape
        `test_legacy_compile_db_typed_threading.py`'s own end-to-end caller
        uses. Non-empty tokens are themselves proof a match occurred, so
        `applied` must still become True even though `matched` was never
        explicitly passed."""

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
            # legacy_compile_db_matched deliberately omitted (defaults False)
        )
        assert applied is True
        assert ctx is not None
        assert ctx.gcc_option_tokens == ("-DWIDE=1",)

    def test_early_return_path_also_honors_tokens_alone(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Same gap, but in the early-return branch (no sources/build_info,
        or no evidence.headers) -- it has its own independent `applied`
        computation and needed the identical fix."""
        header = tmp_path / "h.h"
        header.write_text("void f();\n", encoding="utf-8")
        side = InputSpec(path=tmp_path / "lib.so", sources=None, headers=(header,))
        evidence = SideEvidence(
            headers=[], compile=None, collect_mode="off", dump_manifest=None
        )

        includes, ctx, applied, cleanups = _seeded_includes_and_compile_context(
            side,
            evidence,
            legacy_compile_db_tokens=("-DWIDE=1",),
        )
        assert applied is True
        assert ctx is not None
        assert ctx.gcc_option_tokens == ("-DWIDE=1",)


class TestWhitespaceBearingTokensSurviveTheFold:
    """Codex review, third round on PR #935: a legacy-derived token carrying
    embedded whitespace (a real, common shape -- ``to_castxml_flags()``
    emits ``-I``/``<path>`` as two separate argv entries, and a path can
    legitimately contain a space) must not be corrupted by a join-then-
    re-split round trip."""

    def test_whitespace_bearing_include_path_is_not_split_apart(self) -> None:
        from abicheck.workflows.artifact.resolve import (
            _fold_legacy_compile_db_tokens,
        )

        result = _fold_legacy_compile_db_tokens(None, ("-I", "/opt/SDK Files/include"))
        assert result is not None
        assert result.gcc_option_tokens == ("-I", "/opt/SDK Files/include")
        assert result.gcc_options is None

    def test_whitespace_bearing_define_value_is_not_split_apart(self) -> None:
        from abicheck.workflows.artifact.resolve import (
            _fold_legacy_compile_db_tokens,
        )

        result = _fold_legacy_compile_db_tokens(None, ("-DNAME=a b",))
        assert result is not None
        assert result.gcc_option_tokens == ("-DNAME=a b",)

    def test_existing_gcc_options_string_is_preserved_and_wins_precedence(
        self,
    ) -> None:
        """An explicit, caller-supplied `ctx.gcc_options` must still win
        over the legacy match for a conflicting flag -- the exact
        precedence `_merge_gcc_options`'s string-join ordering already
        established. Verified via the *combined* effective token sequence
        (mirroring how `test_legacy_compile_db_typed_threading.py`'s own
        precedence test reads it), since the encoding moved from a single
        joined string to two fields."""
        from abicheck._compiler_options import split_gcc_options
        from abicheck.workflows.artifact.resolve import (
            _fold_legacy_compile_db_tokens,
        )

        ctx = CompileContext(gcc_options="-DFOO=explicit")
        result = _fold_legacy_compile_db_tokens(ctx, ("-DFOO=legacy",))
        assert result is not None
        combined = list(result.gcc_option_tokens)
        if result.gcc_options:
            combined = split_gcc_options(result.gcc_options) + combined
        # Legacy comes first (lowest precedence); the explicit value comes
        # after it, so a "last -D wins" consumer picks the explicit one --
        # unchanged from the pre-fix string-join ordering.
        assert combined.index("-DFOO=legacy") < combined.index("-DFOO=explicit")

    def test_existing_gcc_option_tokens_still_win_over_legacy(self) -> None:
        from abicheck.workflows.artifact.resolve import (
            _fold_legacy_compile_db_tokens,
        )

        ctx = CompileContext(gcc_option_tokens=("-DFOO=explicit",))
        result = _fold_legacy_compile_db_tokens(ctx, ("-DFOO=legacy",))
        assert result is not None
        combined = list(result.gcc_option_tokens)
        assert combined.index("-DFOO=legacy") < combined.index("-DFOO=explicit")

    def test_no_tokens_is_a_true_no_op(self) -> None:
        """Empty tokens must return *ctx* completely unchanged -- not even
        re-encoded -- so every pre-existing caller is unaffected."""
        from abicheck.workflows.artifact.resolve import (
            _fold_legacy_compile_db_tokens,
        )

        ctx = CompileContext(gcc_options="-DFOO=1", gcc_option_tokens=("-DBAR=1",))
        assert _fold_legacy_compile_db_tokens(ctx, ()) is ctx
        assert _fold_legacy_compile_db_tokens(None, ()) is None
