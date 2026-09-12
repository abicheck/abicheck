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

"""``compare --view`` -- ADR-068 D4/one-comparison-product.md Sec 4.1/Sec 6
Phase 5: one repeatable rendering-selection option collapsing
``--report-mode``, ``--show-only``, ``--demangle``/``--no-demangle``, and
``--explain-patterns`` into a single concept, because all four are spellings
of "which parts of the already-computed canonical result do I want
rendered, and how" -- never a decision that changes the result itself.

Grammar (each ``--view TOKEN`` occurrence contributes one of):

* ``full`` / ``leaf`` / ``impact`` / ``root-cause`` -- report mode
  (``--report-mode``'s exact former ``Choice`` values; last one given wins).
* ``show=<tokens>`` -- the ``--show-only`` token vocabulary, unchanged
  within one occurrence (severity/element/action, AND across dimensions, OR
  within a dimension via comma); repeatable, and each ``show=`` occurrence is
  its own AND-group, ORed against every other occurrence's group (a finding
  is shown if it matches ANY occurrence's group) -- joined internally with
  ``reporter_markdown.SHOW_ONLY_GROUP_SEP`` (``";"``), never comma: joining with comma
  would instead AND two different-dimension groups together, or widen the OR
  *within* one dimension for two same-dimension groups, either way losing
  "match either group" (CodeRabbit/Codex review, PR #1154).
* ``demangle`` / ``no-demangle`` -- the demangle tri-state (last one given
  wins); omitted keeps the ``None`` "auto per format" default.
* ``patterns`` -- render the pattern-verdict modulation ledger
  (``--explain-patterns``'s old spelling). Modulation itself always runs
  wherever idiom evidence exists (ADR-068 D4) -- this only controls whether
  its evidence is explained, never whether it happened.
* ``filtered`` -- render the scope/disposition ledger of findings excluded
  from the verdict (``--show-filtered``'s old spelling). The ledger itself
  is unconditional (ADR-067 S1) and always present in ``-o json=...``;
  this only controls whether the markdown/text render echoes it.
* ``suppressions`` -- render the suppression audit section
  (``--audit-suppressions``'s old spelling). The audit is computed on every
  run that was given ``--suppress`` and always present in
  ``-o json=...``/sarif/junit/html; this only controls whether the
  markdown/text/review render echoes it.

The last three are the Phase 5 residue of §4.1's AUTO rows: each used to be
its own flag that gated *rendering* while the data behind it was already
(or, for ``--surface-metrics``, has since become) unconditional analysis.
``--surface-metrics`` needs no token at all -- its findings live in
``result.changes`` and every projection already renders them.

This module is pure parsing over already-typed strings -- it makes no
decision that touches ``compare_snapshots``/``checker.compare`` and holds no
business logic of its own, matching every other option-only module under
``frontends/cli/options/``.
"""

from __future__ import annotations

REPORT_MODES: tuple[str, ...] = ("full", "leaf", "impact", "root-cause")

_SHOW_PREFIX = "show="


def parse_view_tokens(tokens: tuple[str, ...]) -> dict[str, object]:
    """Parse ``--view`` tokens into the four values the old flags produced.

    Returns a dict with keys ``report_mode`` (str), ``show_only``
    (``str | None``), ``demangle`` (``bool | None``),
    ``explain_patterns`` (bool), ``show_filtered`` (bool) and
    ``audit_suppressions`` (bool) -- exactly the dest names
    ``cli_compare_helpers.run_compare`` already expects, so a caller can
    merge this straight into its kwargs.

    Raises ``ValueError`` on an unrecognized token or a malformed
    ``show=`` filter (the same error ``ShowOnlyFilter.parse`` raises for
    the old ``--show-only`` flag) -- callers translate that into a
    ``click.BadParameter``/``click.UsageError`` as appropriate.
    """
    report_mode = "full"
    show_only_parts: list[str] = []
    demangle: bool | None = None
    explain_patterns = False
    show_filtered = False
    audit_suppressions = False
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
        elif token == "demangle":
            demangle = True
        elif token == "no-demangle":
            demangle = False
        elif token == "patterns":
            explain_patterns = True
        elif token == "filtered":
            show_filtered = True
        elif token == "suppressions":
            audit_suppressions = True
        else:
            raise ValueError(
                f"Unknown --view token: {raw!r}. Expected one of "
                f"{', '.join(REPORT_MODES)}, 'show=<tokens>', 'demangle', "
                "'no-demangle', 'patterns', 'filtered', or 'suppressions'."
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
        # message the old `--show-only` flag gave -- a lossless rename, not
        # a new per-group grammar.
        parse_show_only_groups(show_only)

    return {
        "report_mode": report_mode,
        "show_only": show_only,
        "demangle": demangle,
        "explain_patterns": explain_patterns,
        "show_filtered": show_filtered,
        "audit_suppressions": audit_suppressions,
    }
