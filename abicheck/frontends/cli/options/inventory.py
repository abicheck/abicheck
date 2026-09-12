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

"""ADR-037 D10 CLI-contract metadata: the ``cli-contract`` gate's tables.

The per-option ADR-068 D5 rulings that used to live here as
``COMPARE_FLAG_BUDGET_BASE``/``_RAISES`` now live in the sibling
``rulings.py``; see its docstring for why the shape changed.

Split out of ``cli_options.py`` when that module reached the 2000-line hard
cap (CLAUDE.md "Files that are large — edit carefully"). This is pure
data (family → flags, family → decorator, the flag-count budget ledger)
plus one small reader function (:func:`count_visible_options`) — no Click
decorators, no dependency on the rest of ``cli_options.py`` — so it is a
leaf module re-exported from ``cli_options`` for every existing caller
(``tests/test_cli_contract.py``, ``tests/test_config_rebalance.py``), the
same pattern ``cli_profiles.py`` already established for the run-profile
table.
"""

from __future__ import annotations

from .rulings import (
    COMPARE_FLAG_BUDGET as COMPARE_FLAG_BUDGET,
    COMPARE_OPTION_RULINGS as COMPARE_OPTION_RULINGS,
    DUMP_FLAG_BUDGET as DUMP_FLAG_BUDGET,
    DUMP_OPTION_RULINGS as DUMP_OPTION_RULINGS,
    RULINGS_BY_COMMAND as RULINGS_BY_COMMAND,
    OptionRuling as OptionRuling,
)

# ── ADR-037 D10: contract metadata (single source of truth for the gate) ──────
#
# The ``cli-contract`` AI-readiness gate (D10.2 decorator coverage, D10.4
# one-default-per-flag) and its test mirror key on these tables. Keeping them
# beside the decorators means adding/renaming a family is a one-place edit.

#: Family name → the long ``--flag`` names that family contributes. The gate
#: checks a verdict-emitting command carries the *whole* family (composed via the
#: matching decorator) or is allowlisted in ``INTENTIONAL_SUBSET``.
FAMILY_FLAGS: dict[str, frozenset[str]] = {
    "two_sided_input": frozenset(
        {
            "--header",
            "--include",
            "--version",
        }
    ),
    "policy": frozenset({"--policy", "--suppress"}),
    # Only ``--severity-preset``: the four per-category overrides were hidden
    # duplicates of ``.abicheck.yml``'s ``severity:`` block and have been
    # removed from the CLI (see ``cli_options.severity_options``).
    "severity": frozenset({"--severity-preset"}),
    "scope": frozenset({"--scope-public-headers"}),
    # Plan slice 7m: one flag, one grammar -- `--format` retired into
    # `-o FORMAT=DESTINATION`, so this family is a single member.
    "output": frozenset({"--output"}),
    # Two-sided evidence family (ADR-037 D3 ``@evidence_options``): registered
    # but *not* required — only commands that take source depth (``compare``)
    # compose it.
    "evidence": frozenset(
        {
            "--depth",
            "--sources",
            "--build-info",
        }
    ),
    # Local-ELF debug-resolution family: registered but *not* required either — it
    # resolves local ELF debug artifacts the package/snapshot-oriented commands
    # do not take. ``--dwarf-only``/``--debuginfod``/``--debuginfod-url``/
    # ``--debug-format`` were hidden, config-backed duplicates removed outright
    # in ADR-068 D5 / Phase 7a; only the coarse ``--debug-root`` remains.
    "debug_resolution": frozenset(
        {
            "--debug-root",
        }
    ),
}

#: Family name → the decorator callable that supplies it (used by the gate's
#: AST coverage check, which keys on the decorator applied to a command).
FAMILY_DECORATOR: dict[str, str] = {
    "two_sided_input": "two_sided_input_options",
    "policy": "policy_options",
    "severity": "severity_options",
    "scope": "scope_options",
    "output": "export_options",
    "evidence": "evidence_options",
}

#: Families every verdict-emitting command must compose (unless allowlisted).
#: ``debug_resolution`` is deliberately *not* required — it resolves local ELF
#: debug artifacts that the package/snapshot-oriented commands do not take.
#: ``evidence`` is likewise registered-but-not-required — only commands that take
#: source depth (``compare``) compose ``@evidence_options`` (ADR-037 D3).
REQUIRED_FAMILIES: frozenset[str] = frozenset(
    {
        "two_sided_input",
        "policy",
        "severity",
        "scope",
        "output",
    }
)

#: command name → package-relative module path, for the gate to locate each
#: command's source. `appcompat` folded into `compare --used-by` (ADR-043) and
#: no longer has its own registered command. ADR-061 Phase 4 moved `compare`'s
#: body out of `cli.py`, which is now a registration facade.
VERDICT_EMITTING_COMMANDS: dict[str, str] = {
    "compare": "frontends/cli/commands/compare.py",
}

#: (command, family) → reason. A deliberate, reviewed omission of a shared
#: family from a verdict-emitting command (ADR-037 D3: opt out *explicitly*).
#: Empty today — every verdict-emitting command carries the full required set.
INTENTIONAL_SUBSET: dict[tuple[str, str], str] = {}

#: ADR-037 D10.5's per-command visible-option ceiling. **Superseded shape**
#: (plan Phase 7k): this was ``COMPARE_FLAG_BUDGET_BASE + len(
#: COMPARE_FLAG_BUDGET_RAISES)``, an opaque base count plus a partial ledger,
#: and the test asserted ``visible <= budget``. Every flag removed from the
#: base surface without lowering ``BASE`` turned into permanent slack a later
#: flag could occupy silently -- measured at replacement time: ``visible=48``,
#: ``BASE=41``, ``len(RAISES)=16``, budget ``57``, i.e. **nine flags of
#: slack**, with ``--budget`` already landed as a visible option carrying no
#: ledger entry at all. ``BASE`` naming no flags is also why "which flags are
#: ruled?" had no answer.
#:
#: The ceiling is now exactly the number of options carrying a written
#: ADR-068 D5 ruling in ``rulings.py``, and ``tests/test_config_rebalance.py``
#: asserts an *exact bijection* in both directions, for ``dump`` as well as
#: ``compare`` (``dump`` previously had no ledger at all). A new flag cannot
#: be added without stating in writing which guard lets it in.
#:
#: History that used to fold into ``BASE`` -- the per-family bulk moves from
#: ADR-037 D3/D7, ADR-040 Levers 1/2, and plan Phases 7a-7j -- is recorded in
#: this file's git history and in ``docs/contribute/plans/
#: one-comparison-product.md``'s own §6 Phase 7 narrative, which is where a
#: reader looking for "why did this number move" is actually served. It is
#: not restated here, because a per-flag ruling table replaces the need for a
#: running total to be self-explaining.
#: Navigational meta-options excluded from the flag-count budget: they are
#: not a per-run analysis input the ADR-037 D10.5 budget is bounding, just a
#: help-screen escape hatch (G21.8 collapse M2's curated/full `compare --help`
#: split, mirroring how Click's own auto-added ``--help`` was never a real
#: ``cmd.params`` entry and so never counted either).
_HELP_META_OPTION_NAMES = frozenset({"help", "help_all"})


def count_visible_options(cmd: object) -> int:
    """Count a Click command's user-visible (non-hidden) options (ADR-037 D10.5)."""
    n = 0
    for p in getattr(cmd, "params", []):
        if getattr(p, "name", None) in _HELP_META_OPTION_NAMES:
            continue
        if getattr(p, "param_type_name", None) == "option" and not getattr(
            p, "hidden", False
        ):
            n += 1
    return n
