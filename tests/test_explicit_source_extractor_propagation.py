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

"""Contract tests for ``explicit_source_extractor`` and the one
call site that consumes it (``scan_engine._build_new_snapshot``).

**Bug class**: ``config.propagation_completeness`` in
``tests/regressions/manifest.py`` — "an accepted configuration value either
reaches every relevant consumer with identical semantics, or is rejected at
the public boundary — no third state." That class's own recorded known gap
names "frontend/compiler as a general per-entry-point concern" as one of the
configurable concerns not yet given this treatment; this module is that
treatment for the ``--ast-frontend`` → L4-source-ABI-replay chain.

**The instance**: ``scan`` accepted ``--ast-frontend castxml``, folded it into
``CompileContext.frontend`` exactly as ``dump``/``compare`` do, and then
ignored it for L4 — ``scan_engine`` passed a hardcoded
``source_extractor="auto"``, which ``buildsource.inline._make_source_extractor``
reads as clang unconditionally. So a ``scan --against`` candidate replayed L4
through clang while a ``dump`` baseline taken with the *identical* flag
replayed through castxml, and the two tools' differing accounts of the same
translation unit surfaced as a spurious ``COMPATIBLE_WITH_RISK`` on unchanged
source (the third state the invariant forbids: accepted, but not reaching this
consumer).

**Why these tests are not shaped as one repro.** The reported symptom was a
single frontend spelling on a single command. The invariant underneath is
about the whole small, closed domain of frontend requests, so the request
half is enumerated exhaustively rather than sampled: every accepted
``compile.frontend`` spelling (including case variants and the ``hybrid``
value that has no L4 backend at all) crossed with every
``ABICHECK_AST_FRONTEND`` value that can reach the resolution (11 x 6 = 66
combinations), checked against a hand-written expected table rather than
against a formula. The table is the independent oracle the repository's
bug-class contract requires: recomputing the expectation with
``effective_frontend`` would only assert that the implementation calls the
helper it visibly calls, which is exactly the self-consistent, tautological
check ADR-059 §12's storage incident is the standing example of.

The two end-to-end halves (a real castxml ``dump`` baseline vs. a real
``scan --against`` candidate) live in
``tests/test_dump_scan_l3_comparability.py``, where they used to be two
pinned xfails.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from abicheck.buildsource.inline import _make_source_extractor
from abicheck.compile_context import CompileContext
from abicheck.service_compare_evidence import (
    L4_SOURCE_EXTRACTORS,
    explicit_source_extractor,
)

#: Every ``compile.frontend`` spelling that can reach the primitive, in the
#: case variants ``frontend_value_errors`` validates case-insensitively and
#: therefore lets through. Deliberately wider than the CLI's own
#: ``cli_options.AST_FRONTENDS`` choice:
#:
#: * ``android`` is in ``api_types.SUPPORTED_FRONTENDS`` but *not* in the
#:   header-AST backends ``dumper._resolve_header_backend`` accepts, so it is
#:   the one value where a naive delegation to ``effective_frontend`` raises
#:   instead of answering (CodeRabbit review, PR #990). Three upstream
#:   validators reject it before a real ``scan`` could carry it, so this is a
#:   latent sharp edge rather than a live bug -- which is exactly why it
#:   belongs in a contract test rather than only in a reachable-input test.
#: * ``nonsense``/``""`` stand for "not a frontend at all". The primitive is
#:   not a validator and must answer ``None`` for these rather than raise;
#:   rejecting a bad frontend is the three upstream validators' job.
_FRONTEND_SPELLINGS: tuple[str, ...] = (
    "auto",
    "AUTO",
    "Auto",
    "castxml",
    "CASTXML",
    "CastXML",
    "clang",
    "CLANG",
    "Clang",
    "hybrid",
    "HYBRID",
    "android",
    "ANDROID",
    "nonsense",
    "",
)

#: Every ``ABICHECK_AST_FRONTEND`` value that can reach the resolution, plus
#: the unset case (``None``) and a junk value (``_resolve_header_backend``
#: ignores anything it does not recognise).
_ENV_VALUES: tuple[str | None, ...] = (
    None,
    "",
    "castxml",
    "clang",
    "hybrid",
    "nonsense",
)

#: The hand-written oracle: what an explicit request *means* for L4, stated
#: directly rather than recomputed. ``auto`` and ``hybrid`` both answer None,
#: for different reasons that matter and are asserted separately below —
#: ``auto`` is "nothing was stated, the caller's own default stands", while
#: ``hybrid`` is "stated, but names an L2-only dual-backend mode that has no
#: L4 extractor to select at all".
_EXPECTED_BY_FRONTEND: dict[str, str | None] = {
    "auto": None,
    "castxml": "castxml",
    "clang": "clang",
    "hybrid": None,
    "android": None,
    "nonsense": None,
    "": None,
}


class _StopResolution(Exception):
    """Abort the real resolution once the spy has captured what it needs."""


def _ctx(frontend: str) -> CompileContext:
    """A context carrying only *frontend*, the one field under test."""
    return CompileContext(frontend=frontend)


def _set_env(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    """Set ``ABICHECK_AST_FRONTEND`` to *value*, or unset it for ``None``."""
    if value is None:
        monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    else:
        monkeypatch.setenv("ABICHECK_AST_FRONTEND", value)


class TestExplicitlyRequestedSourceExtractorContract:
    """The primitive's contract, stated as invariants over its whole domain."""

    @pytest.mark.parametrize(
        ("frontend", "env"), list(itertools.product(_FRONTEND_SPELLINGS, _ENV_VALUES))
    )
    def test_matches_the_hand_written_oracle(
        self, frontend: str, env: str | None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exhaustive: every accepted spelling × every reachable env value."""
        _set_env(monkeypatch, env)
        expected = _EXPECTED_BY_FRONTEND[frontend.lower()]
        assert explicit_source_extractor(_ctx(frontend)) == expected

    @pytest.mark.parametrize(
        ("frontend", "env"), list(itertools.product(_FRONTEND_SPELLINGS, _ENV_VALUES))
    )
    def test_answer_is_always_none_or_a_real_l4_backend(
        self, frontend: str, env: str | None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No answer may escape the set of backends L4 actually implements.

        The failure this forecloses is a value like ``"hybrid"`` (or a future
        third L2 mode) reaching ``_make_source_extractor``, which has no
        ``hybrid`` branch and would silently run *clang* while the extractor
        record claimed otherwise.
        """
        _set_env(monkeypatch, env)
        answer = explicit_source_extractor(_ctx(frontend))
        assert answer is None or answer in L4_SOURCE_EXTRACTORS

    @pytest.mark.parametrize("env", _ENV_VALUES)
    def test_an_unstated_frontend_is_env_blind(
        self, env: str | None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``auto`` answers None under *every* env value, including
        ``ABICHECK_AST_FRONTEND=castxml``.

        This is the invariant that keeps ``scan``'s own unflagged default
        untouched. ``effective_frontend`` deliberately *does* consult the env
        var for an unstated request (that is how ``dump``/``compare`` resolve
        "auto"), so an implementation that forwarded to it unconditionally
        would flip a plain ``scan --depth source`` to castxml for anyone with
        that variable exported — the exact behaviour change this primitive
        exists to avoid making silently.
        """
        _set_env(monkeypatch, env)
        assert explicit_source_extractor(_ctx("auto")) is None
        assert explicit_source_extractor(None) is None

    @pytest.mark.parametrize(
        ("frontend", "env"),
        list(itertools.product(("castxml", "CASTXML", "clang", "Clang"), _ENV_VALUES)),
    )
    def test_an_explicit_request_is_env_blind_too(
        self, frontend: str, env: str | None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicitly named backend is never overridden by the environment.

        Stated separately from the oracle table because it is the property a
        user relies on: ``--ast-frontend castxml`` means castxml on a machine
        that happens to export ``ABICHECK_AST_FRONTEND=clang``.
        """
        _set_env(monkeypatch, env)
        assert explicit_source_extractor(_ctx(frontend)) == frontend.lower()

    @pytest.mark.parametrize("frontend", _FRONTEND_SPELLINGS)
    def test_case_spelling_never_changes_the_answer(self, frontend: str) -> None:
        """``CastXML`` and ``castxml`` are one request, not two."""
        assert explicit_source_extractor(_ctx(frontend)) == explicit_source_extractor(
            _ctx(frontend.lower())
        )

    @pytest.mark.parametrize("frontend", ("castxml", "clang"))
    def test_the_answer_actually_selects_that_backend_downstream(
        self, frontend: str
    ) -> None:
        """Ground the name in its real consumer, not in a sibling resolver.

        ``_make_source_extractor`` is what the returned string is ultimately
        handed to, and it special-cases exactly one literal. Asserting the
        round trip here is what makes the primitive's answer *mean* something
        rather than merely be a string that matches another string.
        """
        answer = explicit_source_extractor(_ctx(frontend))
        assert answer is not None
        _impl, tool_name = _make_source_extractor(answer, "clang")
        assert tool_name == frontend

    @pytest.mark.parametrize("frontend", ("auto", "hybrid", "android", "nonsense"))
    def test_none_leaves_the_caller_default_reaching_clang(self, frontend: str) -> None:
        """The None branch preserves today's behaviour end to end.

        ``scan``'s call site substitutes its own ``"auto"`` when this
        primitive answers None, and ``_make_source_extractor`` reads that as
        clang — so "unstated" and "hybrid" both keep the exact extractor the
        candidate resolution used before this primitive existed.
        """
        assert explicit_source_extractor(_ctx(frontend)) is None
        _impl, tool_name = _make_source_extractor("auto", "clang")
        assert tool_name == "clang"


class TestScanEngineCallSitePropagation:
    """The call site actually consumes the primitive.

    ``tests/test_dump_scan_l3_comparability.py`` proves this end to end with a
    real compiler, but that module is ``integration``-marked and so excluded
    from the default fast lane. A hardcoded ``source_extractor="auto"``
    reappearing in ``scan_engine`` is exactly the regression that started
    here, and it must not be able to reach a PR whose author ran only the
    fast command.
    """

    @staticmethod
    def _captured_source_extractor(
        monkeypatch: pytest.MonkeyPatch, frontend: str
    ) -> object:
        """The ``source_extractor`` a real ``_build_new_snapshot`` passes on.

        Spies on the shared resolver and aborts as soon as the argument is
        captured, so this stays a fast-lane test with no compiler, no binary
        and no snapshot work behind it.
        """
        import abicheck.scan_engine as scan_engine
        import abicheck.workflows.artifact.execute as execute

        captured: dict[str, object] = {}

        def _spy(*_args: object, **kwargs: object) -> object:
            """Capture the kwarg under test, then abort the resolution."""
            captured["source_extractor"] = kwargs.get("source_extractor")
            raise _StopResolution

        monkeypatch.setattr(execute, "_resolve_side_snapshot_impl", _spy)
        with pytest.raises(_StopResolution):
            scan_engine._build_new_snapshot(
                binary=Path("libfoo.so"),
                headers=[],
                includes=[],
                sources=None,
                collect_mode="source-target",
                lang="c++",
                allow_build_query=False,
                compile_context=CompileContext(frontend=frontend),
            )
        return captured["source_extractor"]

    def test_an_explicit_frontend_reaches_the_l4_extractor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both concrete backends reach L4 replay when explicitly requested."""
        assert self._captured_source_extractor(monkeypatch, "castxml") == "castxml"
        assert self._captured_source_extractor(monkeypatch, "clang") == "clang"

    @pytest.mark.parametrize("frontend", ("auto", "hybrid", "android", "nonsense"))
    def test_an_unstated_frontend_keeps_scans_own_default(
        self, frontend: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unchanged behaviour, pinned so a future "just use effective_frontend"
        simplification cannot silently flip ``scan``'s default to castxml."""
        assert self._captured_source_extractor(monkeypatch, frontend) == "auto"


class TestNeverRaises:
    """The primitive is total: it answers, it does not validate.

    Split from the contract class above because it is a different kind of
    claim — not "what does it answer for X" but "there is no X it refuses to
    answer for". Rejecting a bad frontend belongs to the three upstream
    validators (``cli_options.AST_FRONTENDS``' Click choice, the
    ``.abicheck.yml`` ``compile.frontend`` check, and
    ``api_types.frontend_value_errors``); a resolver that also raises here
    turns a value one vocabulary calls valid into a crash the moment the two
    vocabularies drift, which is the failure mode this whole change set
    exists to remove.
    """

    @pytest.mark.parametrize(
        "frontend",
        (
            *_FRONTEND_SPELLINGS,
            "android-x86",
            "Clang++",
            "  clang  ",
            "castxml\n",
            "0",
            "auto ",
        ),
    )
    def test_answers_rather_than_raises(self, frontend: str) -> None:
        """Every spelling gets an answer -- ``None`` or a real L4 backend."""
        answer = explicit_source_extractor(_ctx(frontend))
        assert answer is None or answer in L4_SOURCE_EXTRACTORS
