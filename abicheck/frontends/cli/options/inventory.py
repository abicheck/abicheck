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
    "output": frozenset({"--format", "--output"}),
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
    # do not take.
    "debug_resolution": frozenset(
        {
            "--dwarf-only",
            "--debug-root",
            "--debuginfod",
            "--debuginfod-url",
            "--debug-format",
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
    "output": "output_options",
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

#: ADR-037 D10.5 — soft per-command flag-count budget for ``compare`` (a WARN
#: nudge, enforced by ``tests/test_config_rebalance.py::TestFlagBudget``).
#: Counts only the *visible* options: the families demoted to ``.abicheck.yml``
#: in Phase 5 (per-category severity, scope FP-tuning, suppression hygiene) are
#: hidden and config-bound (D4), so they don't count against the budget. The
#: ADR's end-state target is ~20; this interim ceiling keeps new visible flags
#: from creeping back in while the deprecation window runs.
#:
#: The budget is **derived** from the ledger below, not a hand-set number:
#: ``BASE`` is the visible count that settled after the ADR-037 D7
#: ``compare-release`` fold-in, and every visible flag added since must appear in
#: ``COMPARE_FLAG_BUDGET_RAISES`` with a one-line rationale (why it is a per-run
#: analysis input, not a project setting demotable to config). Because the budget
#: equals ``BASE + len(RAISES)`` and the test asserts ``visible <= budget``, a new
#: visible flag *cannot* be slipped in by silently consuming slack — the only way
#: to raise the ceiling is to add a documented ledger entry (a regression that
#: previously let ``--post-manifest`` land undocumented; see the ledger test).
#:
#: History that folded into ``BASE`` (no per-flag ledger — these predate the
#: ledger and moved the count in bulk): 60→66 when ``@compile_context_options``
#: (--gcc-*/--sysroot/--nostdinc, ADR-037 D3) unified onto ``compare`` for
#: dump/scan L2 parity; 66→76 visible when ``compare-release`` was removed and its
#: release-only knobs (package extraction, DSO selection, removed-library gate,
#: ADR-023 bundle/manifest) folded onto ``compare``'s directory/package path
#: (ADR-037 D7) — genuine release surface, inert on single files.
#: Lowered 76→70 by ADR-040 Lever 1 Phase B: the per-side ``--old/new-header``,
#: ``--old/new-include``, ``--old/new-sources`` and ``--old/new-build-info``
#: triples collapsed into the four side-aware flags ``--header`` / ``--include``
#: / ``--sources`` / ``--build-info`` (``old=``/``new=`` value prefix), a net −6.
#: Lowered 70→65 by ADR-040 Lever 1 Phase C (slice 1): ``--pdb-path`` and
#: ``--debug-root`` collapsed their per-side triples (−2 each) and
#: ``--probe-matrix-old/new`` folded into one side-aware ``--probe-matrix`` (−1).
#: Lowered 65→63 by Phase C (slice 2): ``--debug-info1/2`` and ``--devel-pkg1/2``
#: folded into side-aware ``--debug-info`` / ``--devel-pkg`` (−1 each). The
#: unregistered release engine keeps its per-side ``--debug-info1/2`` etc.
#: Lowered 63→62 by Phase C (slice 3): ``--old-version``/``--new-version``
#: folded into one side-aware ``--version`` (``old=``/``new=`` prefix; per-side
#: defaults ``old``/``new``). The unregistered release engine keeps its per-side
#: ``--old-version``/``--new-version``.
#: Lowered 62→57 by ADR-040 Lever 2 (Phase D, constraint-aware subset): the
#: debug-resolution knobs ``--debug-format``/``--debuginfod``/``--debuginfod-url``/
#: ``--dwarf-only`` demoted to the ``debug:`` config block and ``--show-redundant``
#: to ``scope.show_redundant`` — all now ``hidden`` (they still override config,
#: like the severity family), so they leave the visible surface (−5). The coarse
#: ``--debug-root`` stays visible; the toolchain family and ``--scope-public-headers``
#: are documented carve-outs (shared with dump/scan / everyday on-off switch).
#: Lowered 57→55 by CLI cleanup phase two, PR J: ``--bundle-system-providers``/
#: ``--bundle-cohort`` demoted to ``.abicheck.yml``'s ``bundle:`` block, with
#: no CLI override at all (like ``--show-redundant`` above) — a stable,
#: reviewed-in-a-PR release-topology property, not a per-run input, per this
#: plan's own "belongs somewhere else" test.
COMPARE_FLAG_BUDGET_BASE = 55

#: Per-flag ledger of every visible ``compare`` flag added since the D7 fold-in.
#: flag spelling → rationale (why it is a per-run analysis input, not a stable
#: project setting demotable to ``.abicheck.yml``). Keep in sync with reality:
#: ``tests/test_config_rebalance.py`` asserts each key is a currently-visible
#: ``compare`` option, so demoting one to hidden/config means removing its entry
#: (and lowering ``BASE`` if it belonged to the base surface).
COMPARE_FLAG_BUDGET_RAISES: dict[str, str] = {
    "--allow-ast-frontend-fallback": (
        "Explicitly permits a per-run semantic fallback from CastXML to Clang "
        "when the selected CastXML toolchain cannot parse the headers. This is "
        "an invocation-specific risk decision, not a stable project default."
    ),
    "--post-manifest": (
        "G23 / #492: scopes the comparison to a POST Python export manifest's "
        "committed ABI surface. A per-run scoping input (which manifest to hold "
        "the release to), not a stable project setting — like --instantiation-manifest."
    ),
    "--reconcile-build-context": (
        "ADR-039: clears context-free header-parse false positives using the "
        "build's active preprocessor defines. An invocation-time analysis toggle "
        "like --pattern-verdicts, not a project setting demotable to .abicheck.yml."
    ),
    "--env-matrix": (
        "ADR-020b runtime_floors: declared deployment constraints that turn "
        "version-requirement RISK findings into decidable COMPATIBLE/BREAKING "
        "verdicts. The matrix varies per deployment target checked, so it is a "
        "per-run input, not a stable project setting."
    ),
    "--profile": (
        "ADR-040 Lever 3: a single per-run bundle of workflow defaults "
        "(ci-gate/release/quick) that explicit flags always override. One visible "
        "flag replaces the habit of typing 4-6; the reductions in ADR-040 Levers "
        "1-2 lower BASE to bring the net well below today."
    ),
    "--write": (
        "Emits a second output format from the same comparison run to its own "
        "file (e.g. --write json=abi.json alongside a --format markdown "
        "report), so a CI caller (the GitHub Action's PR-comment JSON) no "
        "longer has to re-invoke abicheck a second time. One FORMAT=PATH "
        "operand rather than the --secondary-format/--secondary-output pair it "
        "replaces, which was a usage error unless both were given. A per-run "
        "rendering choice, not a stable project setting."
    ),
    "--dry-run": (
        "ADR-043: resolve and validate the invocation without running the diff. "
        "A per-run preview toggle, not a stable project setting."
    ),
    "--used-by": (
        "ADR-043: folds the removed `appcompat` command into compare -- scopes "
        "the comparison to one or more applications' actual imports. Which "
        "application(s) to check against varies per run, not a project setting."
    ),
    "--required-symbol": (
        "ADR-043: folds the removed `plugin-check` command into compare -- an "
        "explicit required-entrypoint contract for a plugin-host pairing. Varies "
        "per run (which symbols a given host resolves), not a project setting."
    ),
    "--required-symbols": (
        "ADR-043: file form of --required-symbol (one symbol per line). Same "
        "per-run rationale."
    ),
    "--diagnostic-comparison": (
        "ADR-050 D2: downgrades a comparability-gate hard failure (mismatched "
        "profile/scope ExtractionContract fingerprints) into a tentative diff "
        "for this one invocation. Whether a given OLD/NEW pair happens to be "
        "incomparable varies per run, not a stable project setting."
    ),
    "--allow-unsupported-castxml": (
        "Explicitly permits proceeding with a CastXML build outside "
        "castxml_policy's supported version range for this one invocation "
        "instead of aborting before headers are parsed. Same category as "
        "--allow-ast-frontend-fallback: an invocation-specific risk decision "
        "(exploratory-mode reproduction of a legacy toolchain), not a stable "
        "project default."
    ),
    "--dump-manifest": (
        "ADR-050 D3: a real multi-translation-unit dump for one side, in "
        "place of a single -H/--header list. Which side(s) need a manifest "
        "(and which manifest) varies per comparison, not a stable project "
        "setting."
    ),
    "--frontend-context": (
        "ADR-050 D3/D5: which AST context the L2 header frontend should "
        "target (host, or a future device/DPC++ selector). A per-run "
        "extraction-target choice, not a stable project setting -- like "
        "--ast-frontend."
    ),
    "--include-system-declarations": (
        "Shared with dump (cli_options.include_dependencies_option): whether "
        "to include toolchain/system-header declarations in a live-binary "
        "side's dependency scope for this comparison. Which mode a given "
        "invocation needs varies per run (matching whatever a baseline was "
        "dumped with), not a stable project setting."
    ),
    "--contract": (
        "ADR-049: opts one invocation into the contract evaluator and picks "
        "which evidence domain it judges each finding against (public/"
        "exports/all). What a given run is asking varies with it -- 'what "
        "does my declared header surface promise' vs. 'what does this binary "
        "actually export' -- so it is a per-invocation choice, not a stable "
        "project default, like --pattern-verdicts/--surface-metrics."
    ),
    "--pack": (
        "ADR-049 D8: selects a reusable configuration pack (policy/contract/"
        "gate) for this comparison. Which packs apply varies per run -- the "
        "same library is checked against a vendor SDK contract in one "
        "invocation and an internal CI gate in another -- so it is a per-run "
        "selection, like --policy-file. Revisit this entry if a project-config "
        "`packs:` key lands: D7 already reserves the `project_config` tier "
        "below packs, so a permanent project-wide selection would belong "
        "there and this flag would become the per-run override of it."
    ),
    "--audit-suppressions": (
        "Opts one invocation into an additional audit of the --suppress "
        "rule file (stale/high-risk/expired/near-expiry rules) against this "
        "run's findings. Whether a given run wants that extra hygiene check "
        "varies per invocation (e.g. a periodic suppression-file review vs. "
        "a routine CI gate), not a stable project default -- like "
        "--contract above."
    ),
    "--require-complete-analysis": (
        "P0.4: opts one invocation into gating its exit code on "
        "analysis_assurance.status being 'complete', orthogonal to the "
        "compatibility verdict. Whether a given run needs that extra "
        "assurance floor varies per invocation (e.g. a release gate vs. an "
        "exploratory local diff), not a stable project default -- like "
        "--contract/--audit-suppressions above."
    ),
}

#: Derived ceiling — never hand-edit; add a ``COMPARE_FLAG_BUDGET_RAISES`` entry.
COMPARE_FLAG_BUDGET = COMPARE_FLAG_BUDGET_BASE + len(COMPARE_FLAG_BUDGET_RAISES)


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
