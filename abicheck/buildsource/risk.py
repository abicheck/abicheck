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

"""Path-glob risk scoring for the ``scan`` orchestrator (ADR-035 D3, G19.3).

The numeric risk score is the **opt-in** half of the deterministic ``scan``
level model: it drives *only* ``--source-method auto`` (local/dev convenience)
and points-of-interest focusing (D7). A pinned CI level never consults it, so the
exact weights here can never change a deterministic gate — they only steer the
``auto`` escalation (ADR-035 D3).

This module is the scored generalization of
:func:`buildsource.source_replay.recommend_collect_mode` (the ADR-033 D3 PR-diff
localizer): instead of a fixed file-type heuristic, the changed-path set is
scored against a tunable ``risk_rules`` profile and the score is mapped to an
S-method. ``recommend_collect_mode`` stays the canonical *pinned* mapping; this
is the *auto* one.

**The default profile is illustrative and tunable** (ADR-035 D3): the spec fixes
only the *ordering* of signals — public header > export map > ABI flag > internal
source > docs/tests — not the exact numbers. The score of a changed-path set is
the **strongest single signal present** (the max matching rule weight over all
paths), so the ordering is what actually decides the level; a docs-only change
scores at the docs weight (negative → ``s0``) while any public-header touch wins
at the header weight regardless of how many docs files ride along.

Everything here is a pure function over path strings — no I/O, no tools — so the
whole module is exercised by fast unit tests.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any

#: Risk-rule schema version. Independent of every other buildsource schema
#: version (see ``buildsource/CLAUDE.md`` "Versioning").
RISK_RULES_VERSION: int = 1


@dataclass(frozen=True)
class RiskRule:
    """One named path-glob signal and the weight a match contributes.

    ``paths`` are ``fnmatch`` globs matched against the forward-slashed changed
    path *and* its basename (so a bare ``CMakeLists.txt`` matches anywhere in the
    tree). ``weight`` may be negative to *de-escalate* (a docs-only change).
    """

    name: str
    paths: tuple[str, ...]
    weight: int

    def matches(self, path: str) -> bool:
        """Whether *path* (or its basename) matches any of this rule's globs."""
        norm = path.replace("\\", "/")
        base = norm.rsplit("/", 1)[-1]
        for glob in self.paths:
            g = glob.replace("\\", "/")
            if fnmatch.fnmatch(norm, g):
                return True
            # A slash-free glob (``CMakeLists.txt``, ``*.map``) matches by
            # basename anywhere in the tree, not only at the repo root.
            if "/" not in g and fnmatch.fnmatch(base, g):
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "paths": list(self.paths), "weight": self.weight}


#: The illustrative, tunable default ``risk_rules`` profile (ADR-035 D3). Ordered
#: strongest-signal-first; only the *ordering* (public header > export map > ABI
#: flag > internal source > docs/tests) is normative, the numbers are defaults.
_DEFAULT_RULES: tuple[RiskRule, ...] = (
    RiskRule(
        "public_headers",
        ("include/**", "public/**", "*/include/**", "api/**"),
        50,
    ),
    RiskRule(
        "export_map",
        ("*.map", "*.sym", "*.def", "*.ver", "*.exports", "*.symbols"),
        45,
    ),
    RiskRule(
        "build_abi_flags",
        (
            "CMakeLists.txt",
            "cmake/**",
            "*.cmake",
            "BUILD",
            "BUILD.bazel",
            "*.bazel",
            "*.bzl",
            "meson.build",
            "meson_options.txt",
            "configure",
            "configure.ac",
            "Makefile",
            "GNUmakefile",
        ),
        40,
    ),
    RiskRule(
        "internal_source",
        (
            "src/**",
            "lib/**",
            "source/**",
            "*.c",
            "*.cc",
            "*.cpp",
            "*.cxx",
            "*.c++",
        ),
        20,
    ),
    RiskRule(
        "docs_tests",
        (
            "docs/**",
            "doc/**",
            "*.md",
            "*.rst",
            "*.txt",
            "test/**",
            "tests/**",
            "*_test.*",
            "*.test.*",
        ),
        -100,
    ),
)

#: Score thresholds → S-method for ``--source-method auto`` (ADR-035 D3). The
#: ``auto`` escalation is capped at ``s5`` — full ``s6`` is a baseline/manual
#: decision (ADR-035 D9), never risk-picked on a PR. Monotonic in the score.
_AUTO_THRESHOLDS: tuple[tuple[int, str], ...] = (
    (50, "s5"),  # public-header / export-map class signal: targeted semantic AST
    (30, "s5"),  # build-ABI-flag class: still warrants a semantic pass
    (1, "s3"),  # weak/internal-only signal: lexical pre-scan is enough
)
#: Score at or below this contributes no escalation — only the always-on S0/S3
#: tier runs (docs-only / no relevant change).
_AUTO_FLOOR_METHOD = "s0"


@dataclass(frozen=True)
class RiskRules:
    """An ordered set of :class:`RiskRule`s plus the auto-escalation thresholds."""

    rules: tuple[RiskRule, ...] = _DEFAULT_RULES
    version: int = RISK_RULES_VERSION

    @classmethod
    def default(cls) -> RiskRules:
        """The shipped illustrative default profile (ADR-035 D3)."""
        return cls()

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RiskRules:
        """Parse a ``risk_rules:`` config block, defaulting to the shipped profile.

        Accepts the ``.abicheck.yml`` shape (ADR-035 D6)::

            risk_rules:
              public_headers: { paths: ["include/**"], weight: 50 }
              docs_only:      { paths: ["docs/**", "*.md"], weight: -100 }

        An empty / missing block returns the default profile so a partial config
        never silently drops every signal.
        """
        if not data or not isinstance(data, dict):
            return cls.default()
        rules: list[RiskRule] = []
        for name, body in data.items():
            if not isinstance(body, dict):
                continue
            raw_paths = body.get("paths")
            paths: tuple[str, ...]
            if isinstance(raw_paths, str):
                paths = (raw_paths,)
            elif isinstance(raw_paths, list):
                paths = tuple(str(p) for p in raw_paths if p)
            else:
                paths = ()
            if not paths:
                continue
            weight = body.get("weight", 0)
            try:
                w = int(weight)
            except (TypeError, ValueError):
                w = 0
            rules.append(RiskRule(str(name), paths, w))
        if not rules:
            return cls.default()
        return cls(tuple(rules))

    def best_weight(self, path: str) -> tuple[int, str | None]:
        """The strongest (max-weight) rule matching *path*, or ``(0, None)``.

        Ties break toward the *earlier* rule in profile order so the signal
        ordering is deterministic.
        """
        best: tuple[int, str | None] = (0, None)
        chosen = False
        for rule in self.rules:
            if rule.matches(path):
                if not chosen or rule.weight > best[0]:
                    best = (rule.weight, rule.name)
                    chosen = True
        return best

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "rules": [r.to_dict() for r in self.rules],
        }


@dataclass(frozen=True)
class RiskScore:
    """The scored outcome of a changed-path set (ADR-035 D3).

    ``total`` is the strongest single signal present (the max matching rule
    weight). ``matched`` counts how many paths each rule matched, for an
    explainable report; ``n_paths`` is the size of the scored set.
    """

    total: int
    matched: dict[str, int] = field(default_factory=dict)
    n_paths: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "n_paths": self.n_paths,
            "matched": dict(self.matched),
            "recommended_method": self.recommended_method,
        }

    @property
    def recommended_method(self) -> str:
        """The S-method ``--source-method auto`` would select for this score."""
        return recommend_source_method(self)


def score_changed_paths(
    changed_paths: list[str] | tuple[str, ...],
    rules: RiskRules | None = None,
) -> RiskScore:
    """Score a changed-path set against *rules* (default: the shipped profile).

    The total is the **strongest single signal present** — ``max`` of every
    path's best matching rule weight — so the signal *ordering* (D3) decides the
    level and a low-weight docs change cannot dilute a high-weight header change.
    An empty set scores 0 (no signal); a docs-only set scores negative.
    """
    profile = rules or RiskRules.default()
    paths = [p for p in changed_paths if p]
    matched: dict[str, int] = {}
    best_total: int | None = None
    for path in paths:
        weight, name = profile.best_weight(path)
        if name is not None:
            matched[name] = matched.get(name, 0) + 1
        if best_total is None or weight > best_total:
            best_total = weight
    return RiskScore(total=best_total or 0, matched=matched, n_paths=len(paths))


def recommend_source_method(score: RiskScore) -> str:
    """Map a :class:`RiskScore` to an S-method for ``--source-method auto`` (D3).

    Monotonic in ``score.total`` and capped at ``s5`` (full ``s6`` is a
    baseline/manual decision, never risk-picked on a PR — ADR-035 D9).
    """
    for threshold, method in _AUTO_THRESHOLDS:
        if score.total >= threshold:
            return method
    return _AUTO_FLOOR_METHOD
