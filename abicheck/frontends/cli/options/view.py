# Copyright 2026 Nikolay Petrov
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

"""``compare --view`` -- the rendering *selection* option, rewritten by
one-comparison-product.md plan slice 7o around what it still carries.

Phase 5 collapsed four flags into this one option; 7i/7k then ruled ``--view``
a keep without looking inside it. Inside, it still carried six independent
decisions. Three of them are gone, and none of them by deletion:

* ``patterns``, ``filtered`` and ``suppressions`` gated *disclosure* of the
  pattern-modulation ledger, the scope/reconciliation ledger, and the
  ``--suppress`` audit. Each is now unconditional. That is ADR-067's
  record-before-disposing accounting rule, not a display preference: an
  accepted break, an exclusion or a suppressed finding may not be invisible
  because a token was not typed, and "100 removals detected, 100 suppressed
  by rule X" has to stay visible on a *passing* run.
* ``demangle``/``no-demangle`` gated whether human output demangled C++
  symbols. Demangling now always applies to a human format and never to a
  machine one, because the reason to choose is gone:
  :func:`~abicheck.demangle.demangle_text` keeps the exact mangled spelling
  beside the readable name (``Foo::bar(int) [_ZN3Foo3barEi]``), and every
  machine projection carries both names (``symbol`` +
  ``demangled_symbol``).

What is left is the one thing ``--view`` is *for* -- which parts of the
already-computed canonical result to render, and in what shape:

* ``full`` / ``impact`` / ``root-cause`` -- report mode (last one given
  wins). ``leaf`` retired with a measurement rather than an argument: over
  the 129 catalog library pairs that build here, ``leaf`` and ``root-cause``
  exposed the identical finding set in all 93 cases that had findings, and
  ``leaf``'s own headline section was empty in 40 of them. See the plan's 7o
  section for the full table.
* ``show=<tokens>`` -- the severity/element/action display filter.
  Unchanged within one occurrence (AND across dimensions, OR within a
  dimension via comma); repeatable, and each ``show=`` occurrence is its own
  AND-group, ORed against every other occurrence's group -- joined
  internally with ``reporter_markdown.SHOW_ONLY_GROUP_SEP`` (``";"``), never
  comma: joining with comma would instead AND two different-dimension
  groups together, or widen the OR *within* one dimension for two
  same-dimension groups, either way losing "match either group"
  (CodeRabbit/Codex review, PR #1154). Its three dimensions now resolve
  through the change catalog's own declared ``ChangeEntity``/
  ``ChangeOperation`` rather than through a second interpretation of a
  ``ChangeKind``'s *name* -- which is also why the element vocabulary grew
  ``build``, ``source`` and ``analysis``: the superseded name-prefix table
  could not express those three dimensions at all, and mapped 238 of 407
  kinds to no element whatsoever.

The six retired tokens are usage errors (exit 64) with no hidden alias, and
are registered in ``scripts/retired_surfaces.py``.

This module is pure parsing over already-typed strings -- it makes no
decision that touches ``compare_snapshots``/``checker.compare`` and holds no
business logic of its own, matching every other option-only module under
``frontends/cli/options/``.
"""

from __future__ import annotations

REPORT_MODES: tuple[str, ...] = ("full", "impact", "root-cause")

_SHOW_PREFIX = "show="

#: Retired ``--view`` tokens -> what replaced each. Every one is a usage
#: error (exit 64) with no hidden alias; each message names the unconditional
#: behaviour that supersedes it, so a user reading the error learns that the
#: thing they asked for already happened.
RETIRED_TOKENS: dict[str, str] = {
    "demangle": (
        "human output always demangles now, and keeps the exact mangled "
        "symbol beside the readable name"
    ),
    "no-demangle": (
        "the exact mangled symbol is always present -- in human output "
        "beside the demangled name, and in every machine projection as "
        "'symbol', with 'demangled_symbol' alongside it"
    ),
    "leaf": (
        "use 'root-cause'. Measured over 129 real library pairs from the "
        "catalog corpus (93 with findings): the two modes exposed the "
        "*identical* finding set in every one of them, and 'leaf' rendered "
        "an empty headline section in 40 of the 93 because it groups only "
        "root-type changes -- so it never showed evidence 'root-cause' "
        "lacked, and often showed less"
    ),
    "patterns": "the pattern-modulation ledger is always disclosed (ADR-067)",
    "filtered": (
        "the scope/reconciliation ledger of findings excluded from the "
        "verdict is always disclosed (ADR-067)"
    ),
    "suppressions": (
        "the --suppress audit is always disclosed when suppression was given (ADR-067)"
    ),
}


def parse_view_tokens(tokens: tuple[str, ...]) -> dict[str, object]:
    """Parse ``--view`` tokens into the two values they still carry.

    Returns a dict with keys ``report_mode`` (str) and ``show_only``
    (``str | None``) -- exactly the dest names
    ``cli_compare_helpers.run_compare`` expects, so a caller can merge this
    straight into its kwargs.

    Raises ``ValueError`` on an unrecognized token or a malformed ``show=``
    filter (the same error ``ShowOnlyFilter.parse`` raises); callers
    translate that into a ``click.BadParameter``/``click.UsageError``.
    A retired token (``demangle``, ``no-demangle``, ``patterns``,
    ``filtered``, ``suppressions``) is rejected with a message naming what
    replaced it, rather than with the generic unknown-token text -- the
    behaviour it asked for still happens, it just no longer needs asking
    for.
    """
    report_mode = "full"
    show_only_parts: list[str] = []
    for raw in tokens:
        token = raw.strip()
        if token in REPORT_MODES:
            report_mode = token
        elif token.startswith(_SHOW_PREFIX):
            value = token[len(_SHOW_PREFIX) :]
            if not value:
                raise ValueError(
                    "--view show=... needs at least one token after 'show=' "
                    "(e.g. --view show=breaking,functions)."
                )
            show_only_parts.append(value)
        elif token in RETIRED_TOKENS:
            raise ValueError(
                f"--view {token} was retired (plan slice 7o): {RETIRED_TOKENS[token]}"
            )
        else:
            raise ValueError(
                f"Unknown --view token: {raw!r}. Expected one of "
                f"{', '.join(REPORT_MODES)} or 'show=<tokens>'."
            )

    # Each `--view show=...` occurrence is joined with the reporter's own
    # OR-of-groups separator (";"), not ",": a comma instead would AND two
    # different-dimension groups (or widen the OR within one dimension for
    # two same-dimension groups) rather than OR the groups themselves
    # together, silently breaking the documented "repeat to OR further
    # groups" contract (CodeRabbit/Codex review, PR #1154).
    from ....reporter_markdown import SHOW_ONLY_GROUP_SEP, parse_show_only_groups

    show_only = SHOW_ONLY_GROUP_SEP.join(show_only_parts) if show_only_parts else None
    if show_only is not None:
        # Reuse ShowOnlyFilter's own per-group validation so a bad token
        # inside any `show=...` occurrence is rejected with the identical
        # message the old `--show-only` flag gave.
        parse_show_only_groups(show_only)

    return {"report_mode": report_mode, "show_only": show_only}
