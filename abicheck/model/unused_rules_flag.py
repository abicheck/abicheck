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

"""``--fail-on-unused-rules`` as a per-invocation flag (ADR-067 P1).

The flag is a CLI presentation escalation, not a comparison input, so it is
carried as a ``ContextVar`` set for the duration of one ``compare`` invocation
rather than threaded through the typed API. It lives in ``model/`` because both
ends must be able to see it without a forbidden edge: the frontend sets it
(``frontends`` may import ``model``), and the policy gate reads it (``policy``
may import ``model``). It only tightens: ``True`` escalates ``unused_rules`` to
``block``, and nothing here can relax a policy file's own setting.
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator

_FAIL_ON_UNUSED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "abicheck_fail_on_unused_rules", default=False
)


def fail_on_unused_rules_requested() -> bool:
    """True while an invocation that passed ``--fail-on-unused-rules`` runs."""
    return _FAIL_ON_UNUSED.get()


@contextlib.contextmanager
def fail_on_unused_rules(enabled: bool) -> Iterator[None]:
    """Scope ``--fail-on-unused-rules`` to one CLI invocation."""
    token = _FAIL_ON_UNUSED.set(bool(enabled))
    try:
        yield
    finally:
        _FAIL_ON_UNUSED.reset(token)
