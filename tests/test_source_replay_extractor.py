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

"""Which extractor L4 source replay runs (``source_replay_extractor``).

L4's documented default is clang -- the only extractor that records macros
and inline/template/constexpr bodies. A stated frontend still wins, resolved
as L2 resolves it. Before, a bare ``auto`` resolved to L2's castxml, so a
``depth="source"`` run never saw a ``#define`` change on a C++ tree (and on
a C tree only through a second, hidden clang embed in the ``dump`` CLI).
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.compile_context import CompileContext
from abicheck.service_compare_evidence import source_replay_extractor

_COMPILE = (None, "auto", "AUTO", "castxml", "Clang", "hybrid")
_BACKEND = ("auto", "castxml", "clang")
_ENV = ("", "castxml", "clang", "auto", "AUTO", "nonsense")


def _oracle(compile_frontend: str | None, backend: str, env: str) -> str:
    """First stated frontend in precedence order, lowercased; else clang."""
    for stated in (compile_frontend, backend):
        if stated and stated.lower() != "auto":
            return stated.lower()
    # L2 honors only a concrete backend name in the env var; anything else
    # (`auto`, a typo) leaves it unset.
    if env.lower() in ("castxml", "clang", "hybrid"):
        return env.lower()
    return "clang"


@pytest.mark.parametrize(
    ("compile_frontend", "backend", "env"),
    list(itertools.product(_COMPILE, _BACKEND, _ENV)),
)
def test_l4_runs_the_stated_frontend_else_clang(
    monkeypatch: pytest.MonkeyPatch,
    compile_frontend: str | None,
    backend: str,
    env: str,
) -> None:
    if env:
        monkeypatch.setenv("ABICHECK_AST_FRONTEND", env)
    else:
        monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    compile = (
        None if compile_frontend is None else CompileContext(frontend=compile_frontend)
    )

    assert source_replay_extractor(compile, backend) == _oracle(
        compile_frontend, backend, env
    )
