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

"""``--ast-frontend`` reaches L4 source-ABI replay on ``compare``/``dump``.

**Bug class**: ``config.propagation_completeness`` in
``tests/regressions/manifest.py`` -- "an accepted configuration value either
reaches every relevant consumer with identical semantics, or is rejected at
the public boundary -- no third state."

**The consumer**: ``workflows/artifact/embed_side.embed_side_build_source``
picks the L4 replay backend with
``service_compare_evidence.effective_frontend(compile, header_backend)`` and
hands it to ``buildsource.inline._make_source_extractor``. That is the one
path ``compare`` and ``dump`` take. (``scan`` once took a second one, through
``explicit_source_extractor``, which kept its own "auto means clang" default;
both went with ``scan``, ADR-068 Phase 6, and the dead-code plan's Stage D.)

**Why the request half is enumerated, not sampled.** The domain is small and
closed: every ``compile.frontend`` spelling the validators let through (case
variants included) crossed with every ``ABICHECK_AST_FRONTEND`` value that
can reach the resolution. Each combination is checked against a hand-written
table, not against a recomputation through ``_resolve_header_backend``,
which would only show the implementation calls the helper it visibly calls.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.buildsource.inline import _make_source_extractor
from abicheck.compile_context import CompileContext
from abicheck.service_compare_evidence import effective_frontend

#: ``--ast-frontend`` values that name an L4 backend ``_make_source_extractor``
#: implements. ``hybrid`` is an L2-only mode with no L4 extractor; a
#: ``--depth source`` run under it is rejected at the boundary
#: (``workflows.artifact.resolve.reject_hybrid_source_frontend``).
_L4_BACKENDS = ("castxml", "clang")

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
)

_ENV_VALUES: tuple[str | None, ...] = (
    None,
    "",
    "castxml",
    "clang",
    "hybrid",
    "nonsense",
)

#: The hand-written oracle for an unstated request: what ``auto`` resolves
#: to under each environment value. An unrecognised or empty value is
#: ignored and castxml, the schema reference, stands.
_AUTO_BY_ENV: dict[str | None, str] = {
    None: "castxml",
    "": "castxml",
    "castxml": "castxml",
    "clang": "clang",
    "hybrid": "hybrid",
    "nonsense": "castxml",
}


def _ctx(frontend: str) -> CompileContext:
    return CompileContext(frontend=frontend)


def _set_env(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    else:
        monkeypatch.setenv("ABICHECK_AST_FRONTEND", value)


def _expected(frontend: str, env: str | None) -> str:
    stated = frontend.lower()
    return _AUTO_BY_ENV[env] if stated == "auto" else stated


class TestL4FrontendResolution:
    @pytest.mark.parametrize(
        ("frontend", "env"), list(itertools.product(_FRONTEND_SPELLINGS, _ENV_VALUES))
    )
    def test_matches_the_hand_written_oracle(
        self, frontend: str, env: str | None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_env(monkeypatch, env)
        assert effective_frontend(_ctx(frontend), "auto") == _expected(frontend, env)

    @pytest.mark.parametrize(
        ("frontend", "env"),
        list(itertools.product(("castxml", "CASTXML", "clang", "Clang"), _ENV_VALUES)),
    )
    def test_an_explicit_request_is_env_blind(
        self, frontend: str, env: str | None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``--ast-frontend castxml`` means castxml on a machine that exports
        ``ABICHECK_AST_FRONTEND=clang``."""
        _set_env(monkeypatch, env)
        assert effective_frontend(_ctx(frontend), "auto") == frontend.lower()

    @pytest.mark.parametrize("frontend", _FRONTEND_SPELLINGS)
    def test_case_spelling_never_changes_the_answer(
        self, frontend: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_env(monkeypatch, None)
        assert effective_frontend(_ctx(frontend), "auto") == effective_frontend(
            _ctx(frontend.lower()), "auto"
        )

    @pytest.mark.parametrize("backend", _L4_BACKENDS)
    def test_an_absent_context_takes_the_header_backend(
        self, backend: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_env(monkeypatch, None)
        assert effective_frontend(None, backend) == backend

    @pytest.mark.parametrize("frontend", _L4_BACKENDS)
    def test_the_answer_selects_that_backend_downstream(
        self, frontend: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ground the name in its real consumer: ``_make_source_extractor``
        special-cases one literal, so the round trip is what makes the
        resolved string mean a backend."""
        _set_env(monkeypatch, "clang" if frontend == "castxml" else "castxml")
        _impl, tool_name = _make_source_extractor(
            effective_frontend(_ctx(frontend), "auto"), "clang"
        )
        assert tool_name == frontend
