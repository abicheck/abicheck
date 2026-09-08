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

"""The `BugClass`/`KnownGap` *shape* — the schema half of the bug-class
registry, split out of `manifest.py`.

`manifest.py` is the registry's *data*: one entry per durable bug class,
and it grows with every fix that closes a genuinely new one. The two
dataclasses here are its *contract*, and they change only when the registry
itself gains a field — a different rate of change and a different reason to
edit, which is why they get their own module rather than sitting above
1200 lines of entries (`architecture/debt.yaml`'s `no_growth` baseline;
AGENTS.md "Files that are large" — move responsibility out, never trim the
file to fit).

Named for what it holds rather than the bare `schema.py` it started as:
the repo has no other `schema.*`, and a generically-named one in a test
package reads to path-risk tooling as a wire/report schema — a high-risk
review path — which this test-support module is not.

`manifest.py` re-exports both names, so
`from tests.regressions.manifest import BugClass` still resolves and no
call site changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class KnownGap:
    """A residual the class's current tests deliberately do not close.

    Per AGENTS.md's "Fix the cause, not the instance": a gap is tracked
    here rather than left as prose only. `canary_test`, when set, must be
    a *dedicated* executable canary written specifically for this gap that
    fails loudly if the residual silently closes or silently widens — never
    a pointer to an existing suite that happens to cover the same class but
    doesn't encode this specific gap. Leave it `None` for a gap that is
    tracked but not yet monitored by a canary; `None` is honest, a
    mismatched path is not (Codex review, PR #885).

    "Fails loudly" is a strict requirement, checked by
    `tests/test_regressions_manifest.py::test_known_gap_canaries_exist`, not
    just documented here: an ordinary `@pytest.mark.xfail` is non-strict by
    this repository's own pytest config (no `xfail_strict` ini option), so
    an unexpected pass (XPASS, i.e. the gap silently closed) still reports
    green — use `@pytest.mark.xfail(..., strict=True)` instead. A bare
    `@pytest.mark.skip` is rejected outright: a skipped test never executes
    at all, so it cannot detect the residual closing *or* widening — it
    only proves the file exists. A conditional runtime `pytest.xfail(...)`
    call (`if not fixed_yet(): pytest.xfail(...)`) is **not** an equivalent
    substitute for `strict=True`, despite looking like one: once the
    guarding condition stops being met (the gap closes), execution falls
    through to whatever follows and, if that now passes, pytest records an
    ordinary PASS — not an XPASS — so nothing distinguishes it from any
    other passing test and CI stays green with no alert (Codex review,
    PR #885, fresh evidence after the first review round). A canary with no
    xfail/skip decorator at all must instead directly assert the *residual's
    own bound* (the specific degraded/wrong value the gap currently
    produces) rather than the eventually-correct behavior — asserting the
    bound fails loudly the moment the real behavior diverges from it, in
    either direction.
    """

    #: What's not covered (one sentence — the full account lives in
    #: AGENTS.md's "Known gaps" section or the linked issue/PR).
    description: str
    #: Issue or PR number this gap traces to, e.g. "PR #843".
    reference: str
    #: Path to a *dedicated* canary test for this exact gap, or `None` if
    #: this residual is tracked but not yet monitored by one.
    canary_test: str | None = None


@dataclass(frozen=True)
class BugClass:
    """One durable, cross-PR bug-class entry."""

    #: Stable, dotted identifier — e.g. "identity.environment_taint".
    #: Referenced by a future PR's "Bug class" answer instead of restating
    #: the invariant from scratch.
    id: str
    #: One-sentence statement of the invariant that must hold for every
    #: input, not just the originally reported one.
    invariant: str
    #: Issue/PR numbers this class's own escape history traces through —
    #: for traceability, not for the integrity check to validate against
    #: GitHub (this registry has no network access).
    fixed_by: tuple[int, ...]
    #: Paths to the test(s) carrying the generalized/property/metamorphic
    #: suite for this class. At least one is required — a class with no
    #: test is a "Known gaps" AGENTS.md paragraph, not a registry entry.
    seed_tests: tuple[str, ...]
    #: Documented, user-facing entry points this class's own seed_tests
    #: actually invoke — "cli" only for a real Click/`CliRunner`
    #: invocation, "python-api" only for a call through `abicheck.service`,
    #: "github-action" only for a real execution of a workflow/composite-
    #: action step. A seed test that imports an internal module directly
    #: (`abicheck.checker`, `abicheck.surface`, `abicheck.dumper_clang`,
    #: ...) — which is most of this registry today — exercises none of
    #: these, and this field must stay `()` for it: a claimed surface a
    #: seed test doesn't reach conceals exactly the missing cross-surface
    #: coverage a contributor is supposed to discover here (Codex review,
    #: PR #885). Free-form beyond that rule; not yet cross-checked against
    #: a fixed vocabulary.
    public_surfaces: tuple[str, ...] = ()
    #: Axis name -> the values *actually exercised*, e.g.
    #: {"algorithm": ("zstd", "gzip")} when a seed test genuinely round-
    #: trips through both. The same rule as `public_surfaces` applies to a
    #: "frontend" axis specifically: {"frontend": ("castxml", "clang")}
    #: requires a seed test that invokes the real castxml/clang backend —
    #: not one that feeds a hand-built AST/XML fragment into an internal
    #: parser class directly, which is frontend-agnostic and earns no
    #: frontend axis entry at all. Free-form beyond that rule; documents
    #: *coverage* breadth, not a schema this module enforces.
    axes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Residuals this class's current tests do not close (see `KnownGap`).
    known_gaps: tuple[KnownGap, ...] = ()
