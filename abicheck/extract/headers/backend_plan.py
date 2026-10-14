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

"""The environment-derived header-backend decisions, read in one place.

Whether an ``auto`` request may fall back from castxml to clang, whether an
out-of-policy castxml is tolerated, and which backend ``ABICHECK_AST_FRONTEND``
pins are decisions several callers must agree on: the dump itself, the L2
cache key (which has to name the same frontend the dump will run), and the
preflight that reports it. Each of them used to re-read the same three
variables. They now all go through the functions here, so they agree by
construction rather than by keeping copies in step.

:class:`BackendPlan` is the frozen snapshot of those decisions for one
request. Code that consumes a decision takes a plan, so a test can build one
directly instead of setting process environment or patching a reader.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..env_flags import env_flag

#: Backends an ``ABICHECK_AST_FRONTEND`` value may pin; anything else is unset.
PINNABLE_BACKENDS = frozenset({"castxml", "clang", "hybrid"})


def ast_frontend_pin() -> str:
    """The normalized ``ABICHECK_AST_FRONTEND`` value (``""`` when unset)."""
    return os.environ.get("ABICHECK_AST_FRONTEND", "").strip().lower()


def ast_fallback_enabled() -> bool:
    """Whether ``ABICHECK_ALLOW_AST_FALLBACK`` opts in to castxml->clang fallback."""
    return env_flag("ABICHECK_ALLOW_AST_FALLBACK")


def allow_unsupported_castxml_enabled() -> bool:
    """Explicit opt-in override for the CastXML version gate
    (``castxml_policy``). A hard failure by default, degraded only on
    deliberate request."""
    return env_flag("ABICHECK_ALLOW_UNSUPPORTED_CASTXML")


def auto_selected(backend: str, pin: str) -> bool:
    """Whether *backend* is genuinely unpinned ``auto`` selection, given the
    ``ABICHECK_AST_FRONTEND`` value *pin*."""
    choice = (backend or "auto").strip().lower()
    return choice == "auto" and pin not in PINNABLE_BACKENDS


@dataclass(frozen=True)
class BackendPlan:
    """One request's environment-derived backend decisions, frozen.

    Build it with :meth:`from_environment` at the entry point, then pass it
    down. Consumers read the fields and never touch ``os.environ``.
    """

    ast_frontend_pin: str = ""
    ast_fallback_enabled: bool = False
    allow_unsupported_castxml: bool = False

    @classmethod
    def from_environment(cls) -> BackendPlan:
        """Resolve every backend decision from the live process environment."""
        return cls(
            ast_frontend_pin=ast_frontend_pin(),
            ast_fallback_enabled=ast_fallback_enabled(),
            allow_unsupported_castxml=allow_unsupported_castxml_enabled(),
        )

    def auto_selected(self, backend: str) -> bool:
        """Whether *backend* is unpinned ``auto`` under this plan."""
        return auto_selected(backend, self.ast_frontend_pin)
