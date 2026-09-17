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

"""Which cross-source checks a *member* comparison owns, and which the
release level owns.

One of the eleven intra-version cross-source checks
(``public_not_exported``) answers a question about the whole *product*, not
about one binary: whether the public contract's declarations are exported by
the release. A multi-library release that answers it per member answers it
against a Cartesian product -- one complete product header surface against
each DSO -- and manufactures one finding per member per declaration another
member provides (787,833 of them on a 28-library Intel MKL release, where a
ScaLAPACK declaration was demanded from ``libmkl_rt`` although a sibling MKL
library exports it).

So in a release comparison it is *moved*, not disabled: the release fan-out
declares it release-owned for the duration of its member pass
(:func:`release_owned_checks_scope`) and answers it once, at release level,
against the union of the bundle's exports
(``policy.release_contract_reconciliation``). A member comparison runs the
other ten unchanged -- each of those really does judge one binary's own
evidence against its own headers, ``exported_not_public`` included (an
undocumented export belongs to the member exporting it; see that module's
docstring for why moving it would change nothing but the amount of
duplicated code).

Generic in the plural, though: the mechanism is a *set* of owned checks,
not one hard-coded name, so a future check that genuinely needs whole-product
evidence moves by being named here rather than by growing a second
mechanism.

**Why a run-scoped ``ContextVar`` and not a parameter.** The fact being
stated is not "skip some analysis" (which ADR-068 D4/D5 would rightly
reject as a flag that merely turns useful analysis off -- and this is not
that: every check still runs, exactly once, with its conclusions
*strengthened* by the union evidence). It is "for this run, another layer
owns this check", which is a property of the enclosing comparison, not of
any one pair. The release fan-out already propagates a copy of the calling
thread's ``contextvars.Context`` into each parallel worker
(``cli_compare_release_pairwise._compare_release_parallel``, for
``policy_file``'s dedup scope), so the same mechanism reaches every member
comparison sequentially and in parallel, without threading a parameter
through five layers of an otherwise untouched call chain. Outside such a
scope the default is empty, so every single-pair ``compare``, ``appcompat``
and typed-API call behaves exactly as before.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

#: Checks an enclosing comparison has taken ownership of. Empty outside a
#: :func:`release_owned_checks_scope`, which is what keeps every
#: single-artifact caller bit-for-bit unchanged.
_RELEASE_OWNED: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "abicheck_release_owned_crosschecks", default=frozenset()
)


@contextmanager
def release_owned_checks_scope(checks: Iterable[str]) -> Iterator[None]:
    """Declare *checks* answered by the enclosing (release) level.

    Restores the previous ownership on exit, so nesting is well-defined and
    an exception inside the member pass cannot leak the scope into
    unrelated later work in the same thread.
    """
    token = _RELEASE_OWNED.set(frozenset(checks))
    try:
        yield
    finally:
        _RELEASE_OWNED.reset(token)


def release_owned_checks() -> frozenset[str]:
    """Checks the enclosing level owns right now (empty by default)."""
    return _RELEASE_OWNED.get()


def member_owned_checks(all_checks: Iterable[str]) -> frozenset[str]:
    """*all_checks* minus whatever the enclosing level has taken over."""
    return frozenset(all_checks) - _RELEASE_OWNED.get()


def release_level_checks() -> frozenset[str]:
    """The checks a release comparison answers at release level.

    A pass-through to ``policy.release_contract_reconciliation.
    release_owned_checks``, which is where the decision belongs. It exists
    because a ``frontends``-classified release module may not import
    ``policy`` at all, while ``workflows -> policy`` is an allowed edge --
    so this is the one hop that keeps the decision in one place instead of
    a second copy of the owned set living in the CLI.
    """
    from ..policy.release_contract_reconciliation import release_owned_checks

    return release_owned_checks()
