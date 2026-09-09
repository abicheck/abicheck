#!/usr/bin/env python3
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

"""`check_docs_contract.py`'s retired-CLI-surface registry and sweep, split
out purely to keep that file under the AI-readiness `file-size` gate's own
2000-line hard cap -- the same mechanical-extraction move `adr_status_sync.py`/
`pipeline_status_ledger.py`/`engine_cli_boundary.py` already made for their own
callers. Every constant/function here is unchanged from its original home;
`check_docs_contract.py` keeps its own `_RETIRED_SURFACES`/
`_retired_surface_scan_targets`/`_check_retired_surfaces` names as thin
wrappers so its existing tests and call sites (including
`_check_config_keys_as_cli_operands`, which reuses the same scan targets) are
unaffected.

Uses a `Findings` `Protocol` (mirroring `pipeline_status_ledger.py`'s own
decoupling) instead of importing the caller's concrete `Findings` class, and
takes the caller's `_has_generated_marker`/`load_front_matter`/`_rel` helpers
and stale-process-language exempt sets as explicit parameters rather than
duplicating or re-importing them -- both are tiny, but re-importing them back
from `check_docs_contract.py` would make the two modules import each other.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol


class Findings(Protocol):
    """The error/warning sink `check_docs_contract.py`'s own `Findings`
    (a `findings_report.Findings` subclass) already satisfies structurally."""

    def warn(self, check: str, msg: str) -> None:
        """Record a non-blocking finding under `check`."""
        ...


# Retired-surface registry: a literal identifier (file path, CLI/API name)
# that names something genuinely removed from the shipped product. Unlike
# a general topic word (e.g. "MCP" alone, which legitimately appears in
# historical framing all over contribute/adr and contribute/plans), each
# string below only ever makes sense today in a "this used to exist"
# sentence -- there is no live code, doc, or command it could otherwise be
# referring to. Deliberately narrower than a full "no present-tense claim
# about a retired capability" detector (which would need per-document
# semantic judgment this pure-text scan can't do) -- this catches exactly
# the class of bug found in a documentation review: a manual page in
# start/, learn/, use/, or reference/ still pointing at a since-deleted
# file or command as if it were live (docs/reference/mcp-tools-reference.md,
# scripts/gen_mcp_reference.py, --source-abi, and similar were all found
# and fixed this way before this check existed). contribute/adr,
# contribute/plans, and contribute/archive are skipped for the same reason
# the caller's stale-process-language exempt prefixes skip them: they are
# allowed to discuss retired surfaces in their own historical-record
# capacity. Add an entry here whenever a PR deletes a CLI flag/command, a
# file, or a public API name that any doc might still reference by that
# exact spelling.
RETIRED_SURFACES: tuple[tuple[str, tuple[str, ...], frozenset[str]], ...] = (
    (
        "abicheck-mcp (MCP server, removed by #684)",
        (
            "abicheck-mcp",
            "mcp_server.py",
            "gen_mcp_reference.py",
            "mcp-tools-reference.md",
            "abicheck[mcp]",
        ),
        frozenset(
            {"start/upgrading-to-0.6.md", "AGENTS.md", "contribute/known-gaps.md"}
        ),
    ),
    (
        "--source-abi / --source-graph (pre-ADR-043 `collect` command flags)",
        (
            "--source-abi-cache-dir",
            "--source-abi-cache",
            "--source-abi-scope",
            "--source-abi-extractor",
            "--source-abi",
            "--source-graph",
        ),
        frozenset(
            {"use/build-evidence-setup.md", "reference/environment.md", "AGENTS.md"}
        ),
    ),
    (
        "--gcc-options/--gcc-option/--gcc-path/--gcc-prefix (the whole legacy"
        " cross-toolchain family, superseded by --compiler-option/--compiler/"
        " --compiler-prefix -- the Action's own `gcc-options`/`gcc-path`/"
        " `gcc-prefix` inputs, without the leading dashes, are unaffected and"
        " still valid)",
        ("--gcc-options", "--gcc-option", "--gcc-path", "--gcc-prefix"),
        frozenset(
            {
                "AGENTS.md",
                "contribute/known-gaps.md",
                "use/github-action.md",
                # Names the retired spellings once, to point a reader at the
                # --compiler* replacements -- the page documenting the family.
                "use/dump-compare-flags.md",
                # A point-in-time "shipped in PR #422" use-case record: names
                # the flag as it was called then, in its own historical-
                # record capacity. Line-scoped (not the whole multi-entry
                # registry), so a live `--gcc-option` added to a different
                # entry later still gets flagged (CodeRabbit review).
                "docs/contribute/usecase-registry.yaml#L428",
            }
        ),
    ),
    (
        "--verify-runtime (the consumer-execution probe; it had already been"
        " reduced to a safety no-op, and the static --used-by scanner answers"
        " the same undefined-symbol question without executing anything)",
        ("--verify-runtime",),
        frozenset({"AGENTS.md"}),
    ),
    (
        "--contract-evaluation (folded into --contract, which now both turns"
        " the ADR-049 evaluator on and selects its evidence domain; the"
        " former domain-less form is --contract auto)",
        ("--contract-evaluation",),
        frozenset({"AGENTS.md", "use/contract-evaluation.md"}),
    ),
    (
        "--show-impact (folded into --report-mode impact, which was already"
        " documented as its exact equivalent)",
        ("--show-impact",),
        frozenset(
            {
                "AGENTS.md",
                # A point-in-time design review: §3.3 describes the surface as
                # it was and recommends exactly this fold, so it names the
                # flag in its own historical-record capacity.
                "contribute/config-key-review.md",
            }
        ),
    ),
    (
        "--report-mode/--show-only/--demangle/--no-demangle/--explain-patterns"
        " (ADR-068 D4/Phase 5: collapsed into one repeatable --view option)",
        (
            "--report-mode",
            "--show-only",
            "--demangle",
            "--no-demangle",
            "--explain-patterns",
        ),
        # config-key-review.md/goals.md: historical-record framing (design
        # review / milestones ledger). what-each-level-sees.md: `nm -D
        # --demangle` is the standalone `nm` tool's own flag, not abicheck's.
        # output-formats.md/api-surface-intelligence.md: each names the old
        # flag only in the sentence explaining its own fold into --view.
        frozenset(
            {
                "AGENTS.md",
                "contribute/config-key-review.md",
                "contribute/goals.md",
                "learn/what-each-level-sees.md",
                "use/output-formats.md",
                "use/api-surface-intelligence.md",
            }
        ),
    ),
    (
        "--dwarf-only/--no-dwarf-only/--debuginfod/--no-debuginfod/"
        "--debuginfod-url/--debug-format (compare/dump's Phase 7a/7c"
        " debug-resolution family, ADR-068 D5 -- fully config-backed via"
        " debug.*, and never a scan flag, so this isn't a partial retirement)",
        (
            "--dwarf-only",
            "--no-dwarf-only",
            "--debuginfod-url",
            "--debuginfod",
            "--no-debuginfod",
            "--debug-format",
        ),
        # config-key-review.md/known-gaps.md: historical-record framing.
        # companion-commands.md/dump-compare-flags.md/config-file.md each
        # name the old flag only while documenting its fold into debug.*.
        frozenset(
            {
                "AGENTS.md",
                "contribute/config-key-review.md",
                "contribute/known-gaps.md",
                "use/companion-commands.md",
                "use/dump-compare-flags.md",
                "reference/config-file.md",
            }
        ),
    ),
    (
        "aggregate --expect/--optional/--report-prefix (the expected-target"
        " set is declared by --manifest or --run-plan, or waived with"
        " --discovered-only; the report-filename prefix is fixed)",
        ("--report-prefix", "--expect", "--optional"),
        frozenset({"AGENTS.md"}),
    ),
    (
        "the four per-category --severity-<category> flags (hidden duplicates"
        " of .abicheck.yml's own severity: block, which is now their one"
        " spelling; --severity-preset stays as the coarse per-run override)",
        (
            "--severity-abi-breaking",
            "--severity-potential-breaking",
            "--severity-quality-issues",
            "--severity-addition",
        ),
        frozenset({"AGENTS.md"}),
    ),
    (
        "--strict-suppressions/--require-justification/--public-symbol/"
        "--public-symbols-list/--show-redundant/--collapse-versioned-symbols"
        " (hidden duplicates of the suppression:/scope: config blocks, which"
        " are now their one spelling)",
        (
            "--strict-suppressions",
            "--require-justification",
            "--public-symbols-list",
            "--public-symbol",
            "--show-redundant",
            "--collapse-versioned-symbols",
        ),
        frozenset({"AGENTS.md"}),
    ),
    # `dump --public-header`/`--public-header-dir` are gone too (declaration
    # provenance comes from -H/--header itself now), but they are deliberately
    # NOT registered here: `scan --public-header-dir` is still live, and this
    # gate matches plain substrings, so a pattern that catches the retired
    # `dump` spelling necessarily catches the surviving `scan` one. The
    # executable guard for that pair is `tests/test_cli_contract.py`'s
    # per-command option-set snapshot.
    (
        "dump -p/--build-dir and --compile-db, and scan --compile-db"
        " (--build-info already takes a build dir, a compile_commands.json,"
        " or a pack -- the one flag for that operand; --compile-db-filter"
        " still scopes it)",
        ("--build-dir", "--compile-db"),
        frozenset(
            {
                "AGENTS.md",
                "contribute/known-gaps.md",
                # A point-in-time design review: it inventories the surface
                # as it was, in its own historical-record capacity.
                "contribute/config-key-review.md",
            }
        ),
    ),
    (
        "--policy-file (folded into --policy, which now takes a built-in"
        " profile name or a policy document -- a path, or a packaged built-in"
        " like 'security'; the Action's own `policy-file` input, without the"
        " leading dashes, is unaffected and still valid)",
        ("--policy-file",),
        frozenset(
            {
                "AGENTS.md",
                "contribute/known-gaps.md",
                "reference/github-action-inputs.md",
            }
        ),
    ),
    (
        "--secondary-format/--secondary-output (folded into --write"
        " FORMAT=PATH -- half the pair was a usage error either direction, so"
        " they were one option spelled as two)",
        ("--secondary-format", "--secondary-output"),
        frozenset({"AGENTS.md"}),
    ),
    (
        "--old-ast-frontend/--new-ast-frontend (--ast-frontend is side-aware"
        " on compare: --ast-frontend old=castxml --ast-frontend new=clang,"
        " ADR-040 Lever 1's prefix convention)",
        ("--old-ast-frontend", "--new-ast-frontend"),
        frozenset({"AGENTS.md"}),
    ),
    (
        "--include-dependencies (renamed --include-system-declarations: it"
        " restores the declarations a system/toolchain header contributed to"
        " the AST, which is unrelated to the DT_NEEDED library graph"
        " --follow-deps walks)",
        ("--include-dependencies",),
        frozenset({"AGENTS.md"}),
    ),
    (
        "project validate-use-cases --against/--against-new (resolving a"
        " manifest against a real library, and attributing a comparison's"
        " findings to the use cases that reach them, is compare --use-cases)",
        ("--against-new",),
        frozenset({"AGENTS.md"}),
    ),
    (
        "compare --stat/--recommend (CLI cleanup phase two, PR 1): --stat's"
        " one-line summary moved to the built-in --profile quick; the"
        " release recommendation is now unconditional in json/markdown/"
        " review output, so --recommend has nothing left to opt into",
        ("--stat", "--recommend"),
        frozenset(
            {
                "AGENTS.md",
                # A point-in-time design review, same reasoning as the
                # --show-impact entry above: §3.3 describes the surface as
                # it was, so it names the retired flags in its own
                # historical-record capacity rather than as live usage.
                "contribute/config-key-review.md",
                # Explicit migration notes naming the retired flag and its
                # replacement in the same breath ("--stat was removed ...
                # use --profile quick instead"), not stale live usage.
                "use/output-formats.md",
                "tests/scenarios/ci_gating.yaml",
                # The *left* column of a libabigail-to-abicheck migration
                # table: libabigail's own `--stat` flag (a different tool,
                # same spelling), mapped to abicheck's `--profile quick` in
                # the very next column -- not a stale abicheck mention.
                "use/from-libabigail.md",
            }
        ),
    ),
    (
        "project plan --gate-missing-required/--gate-unexpected-target (CLI"
        " cleanup phase two, PR 2 follow-up): the policy moved to"
        " .abicheck.yml's aggregate: gate: block, durable project config"
        " project plan sources instead of a per-invocation flag",
        ("--gate-missing-required", "--gate-unexpected-target"),
        frozenset(
            {
                "AGENTS.md",
                "contribute/known-gaps.md",
                # Explicit migration notes naming the retired flags and
                # their aggregate: gate: replacement in the same breath,
                # same reasoning as the --stat/--recommend entry above.
                "reference/project-targets-schema.md",
                "reference/run-plan-schema.md",
            }
        ),
    ),
    (
        "compare/compare-release --annotate/--annotate-additions (CLI cleanup"
        " phase two, PR E: the composite Action now renders annotations"
        " itself from the persisted `annotations` report field via its own"
        " `annotate`/`annotate-additions` inputs, so the CLI flags were"
        " removed entirely)",
        ("--annotate", "--annotate-additions"),
        frozenset(
            {
                "AGENTS.md",
                # Migration guidance: names the retired CLI spelling once,
                # to point a reader at the `annotate`/`annotate-additions`
                # Action inputs that replaced it.
                "use/annotations.md",
                # A point-in-time design review predating the removal: it
                # names the flags in their own historical-record capacity
                # (verifying stderr consistency, proposing the
                # --annotate-additions-could-be-inferred idea), same
                # reasoning as the --show-impact entry above.
                "contribute/config-key-review.md",
            }
        ),
    ),
    (
        "compare --old-bundle-facts (G38 Phase 17's single-invocation stored-"
        "BundleFacts flag, superseded by CLI cleanup phase two's PR I -- "
        "automatic operand classification from the `artifact_type` marker,"
        " see `bundle_compare_operand.py`)",
        ("--old-bundle-facts",),
        frozenset(
            {
                "AGENTS.md",
                # G38's own phased plan: the flag's design, its later shift
                # to a boolean, and the 2026-09-03 note recording it as
                # since-superseded -- all in this plan's own historical-
                # record capacity.
                "contribute/plans/g38-bundle-facts-model-and-multibuild-comparability.md",
                # This plan's index row for G38 and for CLI cleanup phase
                # two both name the flag to describe what shipped and was
                # later removed.
                "contribute/plans/index.md",
                # The removal's own plan: names the flag throughout as the
                # subject being deleted (before/after tables, the deletion
                # checklist, the worked example of the second-engine
                # problem it existed to retire).
                "contribute/plans/cli-cleanup-phase-two.md",
                # A worked example predating the removal, illustrating the
                # (now-superseded) `--bundle-facts-out`/`--old-bundle-facts`
                # round trip.
                "contribute/plans/learning-series-page-specs.md",
            }
        ),
    ),
    (
        "compare --bundle-cohort/--bundle-system-providers (CLI cleanup"
        " phase two, PR J: bundle topology moved to .abicheck.yml's"
        " bundle.cohorts:/bundle.system_providers:, since it's a stable,"
        " reviewed-in-a-PR release property, not a per-run analysis input;"
        " scan --artifact-set's --bundle-system-providers was retired the"
        " same way)",
        ("--bundle-cohort", "--bundle-system-providers"),
        frozenset(
            {
                "AGENTS.md",
                "contribute/plans/cli-cleanup-phase-two.md",
                # Names the retired spelling once, in its own "formerly ..."
                # historical-framing sentence, to point a reader coming from
                # an old invocation at the `.abicheck.yml` replacement.
                "use/multi-binary.md",
                # Same "formerly ..." historical framing, in the use-case
                # registry's bundle_soname_skew entry. Line-scoped, not the
                # whole multi-entry registry (CodeRabbit review) -- see the
                # --gcc-* entry above for why.
                "docs/contribute/usecase-registry.yaml#L395",
                # Same "replacing the removed ..." historical framing, in
                # the canonical config-file reference's own bundle: section.
                "reference/config-file.md",
            }
        ),
    ),
    (
        "--exit-code-scheme and .abicheck.yml's top-level exit_code_scheme:"
        " key (ADR-064 / CLI cleanup phase two PR G2 -- there is no manual"
        " gate-algorithm override any more; the algorithm is fully"
        " determined by whether a severity setting is in effect. Note this"
        " is distinct from the still-live, purely-derived report field"
        " `gate.exit_code_scheme`/`scoped_exit_code_scheme`, which is not"
        " a settable surface and is not matched by these patterns)",
        ("--exit-code-scheme", "exit_code_scheme:"),
        frozenset(
            {
                # This file's own "Exit codes"/task-routing sections and
                # duplication-and-convergence-assessment notes name the
                # retired flag/key in the identical "no manual override any
                # more"/"deleted"/"back when that flag existed" historical
                # framing every other allowed page below already uses --
                # only reached by this sweep once it started scanning the
                # repository root, not `docs/`, alongside every other page.
                "AGENTS.md",
                # Historical "what changed" migration note explaining the
                # old scoped-severity fix, including that its manual pin
                # was later removed.
                "start/upgrading-to-0.6.md",
                # Explains why `gate.exit_code_scheme` carries no
                # `field_provenance` entry any more -- names the retired
                # flag/key as the thing that used to populate it.
                "reference/compatibility-evaluation-config.md",
                # The "no config key any more" explanation itself names
                # the retired key and flag.
                "reference/config-file.md",
                # The release-path exit-code section explains there is no
                # manual override any more, by naming what was removed.
                "reference/exit-codes.md",
                # "there is no separate `exit_code_scheme:` key" sentence
                # explaining the config file's own severity block.
                "learn/rollout-and-governance.md",
                # Same "no such key any more" explanation as config-file.md.
                "use/build-evidence-setup.md",
                # The rewritten "the two exit-code schemes" section states
                # there is no manual override any more, by naming it.
                "use/ci-gating.md",
                # Historical G22 changelog-style row: names the CLI as it
                # was designed at the time (an explicit --exit-code-scheme),
                # accurate to that point in history.
                "contribute/usecase-coverage-evaluation.md",
            }
        ),
    ),
    (
        "compare --on-incomplete-scope/--fail-on-removed-library/"
        "--no-fail-on-removed-library/--dso-only/--include-private-dso"
        " (Phase 7d, ADR-068 D5: demoted to CONFIG-only -- scope."
        "on_incomplete/gate.fail_on_removed_library/release.dso_only/"
        "release.include_private_dso in .abicheck.yml, no CLI override)",
        (
            "--on-incomplete-scope",
            "--fail-on-removed-library",
            "--no-fail-on-removed-library",
            "--dso-only",
            "--include-private-dso",
        ),
        frozenset(
            {
                # Each config-key section names "the former `compare ...`"
                # flag it replaces, in its own historical-record capacity.
                "reference/config-file.md",
                # Historical migration note, line-pinned so a later, live
                # mention added elsewhere in this file still gets flagged.
                "reference/exit-codes.md#L99",
            }
        ),
    ),
)


def retired_surface_scan_targets(
    docs: Path, cases: Path, scenarios: Path, catalog: Path, root: Path
) -> list[tuple[Path, str]]:
    """Every page the retired-surface sweep reads, with its allowlist key.

    `docs/**/*.md` is the hand-authored narrative tree, keyed docs-relative
    (what `RETIRED_SURFACES`'s allowlists already spell).

    `catalog/cases/case*/README.md` is here because it is the *generator
    source* for the published `docs/reference/examples/case*.md` pages: those
    carry the generated marker and are skipped below, so scanning only the
    output tree left a stale flag in a case README reproducing into a public
    page on the next `gen_examples_docs.py` run while this guard stayed green
    -- which is exactly what happened to Case 148's `--compile-db`
    recommendation (Codex review). Checking the source rather than the
    artifact is the same direction every other generated-file gate in this
    repo takes.

    `tests/scenarios/*.yaml` is here for the same reason one step further out:
    it is the repository's user-flow catalogue, and each entry's `flow:` is a
    command a reader is meant to be able to run. Its structural tests check
    that a flow *has* an automated counterpart, not that the command it prints
    still parses -- so a scenario kept advertising a removed `scan
    --compile-db` while both this sweep and those tests stayed green (Codex
    review). YAML rather than Markdown, but the same failure and the same fix.

    `docs/contribute/usecase-registry.yaml` is here for the identical reason:
    it is the machine-checked use-case source of truth, and its free-text
    `note:` fields document real invocations a reader can copy -- CLI cleanup
    phase two's PR J retired `--bundle-cohort` while this registry's
    UC-WF-bundle-related entry kept advertising it, invisible to this sweep
    because it scanned Markdown/case-README/scenario YAML only (Codex
    review, fresh evidence).

    `catalog/ground_truth.json` is here for the same reason one further
    step out: it is the *other* machine-checked example-catalog source of
    truth (alongside the case READMEs above), and its per-case
    `description` fields document real invocations too -- PR J's
    `--manifest` rename left case93's description advertising the retired
    spelling, invisible to this sweep the same way (Codex review, fresh
    evidence).

    The root `README.md`/`AGENTS.md` are here too: both entirely outside
    `docs/`, each kept advertising a retired flag after removal, invisible
    above (Codex review, fresh evidence -- twice, one file each).

    `tools/**/*.md` is here for the identical reason one step further out
    still: a first-party companion tool's own README documents real
    invocations of the main CLI too (e.g. `tools/clang-layout-tool/
    README.md`'s "Using it with abicheck" section), and this sweep
    previously stopped at the repository root -- `--ast-frontend`'s
    removal from `compare` left that page advertising it, invisible here
    the same way every other one-directory-further-out gap in this
    function's own history was (Codex review, fresh evidence).

    Keyed repo-relative (`catalog/cases/caseNN.../README.md`,
    `tests/scenarios/x.yaml`, `docs/contribute/usecase-registry.yaml`,
    `catalog/ground_truth.json`, `README.md`, `AGENTS.md`,
    `tools/<tool>/README.md`), which cannot collide with a docs-relative
    key, so an allowlist entry stays unambiguous.
    """
    targets = [(p, p.relative_to(docs).as_posix()) for p in sorted(docs.rglob("*.md"))]
    targets += [
        (p, f"catalog/cases/{p.relative_to(cases).as_posix()}")
        for p in sorted(cases.glob("case*/README.md"))
    ]
    targets += [
        (p, f"tests/scenarios/{p.name}") for p in sorted(scenarios.glob("*.yaml"))
    ]
    usecase_registry = docs / "contribute" / "usecase-registry.yaml"
    if usecase_registry.is_file():
        targets.append((usecase_registry, "docs/contribute/usecase-registry.yaml"))
    ground_truth = catalog / "ground_truth.json"
    if ground_truth.is_file():
        targets.append((ground_truth, "catalog/ground_truth.json"))
    for name in ("README.md", "AGENTS.md"):
        if (root / name).is_file():
            targets.append((root / name, name))
    tools_dir = root / "tools"
    if tools_dir.is_dir():
        targets += [
            (p, f"tools/{p.relative_to(tools_dir).as_posix()}")
            for p in sorted(tools_dir.rglob("*.md"))
        ]
    return targets


def check_retired_surfaces(
    f: Findings,
    targets: list[tuple[Path, str]],
    *,
    has_generated_marker: Callable[[Path], bool],
    load_front_matter: Callable[[Path], dict[str, object] | None],
    front_matter_errors: tuple[type[Exception], ...],
    stale_exempt_prefixes: tuple[str, ...],
    stale_exempt_lifecycles: frozenset[str],
    rel: Callable[[Path], str],
) -> None:
    """Flag a manual, non-historical page that still names a retired CLI
    flag/command/file by its exact dead spelling, as if it were live surface.
    Deliberately scans fenced code blocks too (unlike the caller's own
    stale-process-language sweep, which blanks them) -- a stale command
    inside a ```bash example is exactly the worst place to miss one, since a
    reader is likely to copy-paste it verbatim. Exempts the same lifecycle/
    generated pages that sweep does -- a page marked historical/migration is
    allowed to discuss a retired surface in its own historical-record
    capacity, same reasoning as the ADR/plans/archive directory exemption
    below. WARN-only: a hit needs a human read to add historical framing or
    an allowlist entry, not an automatic rewrite."""
    for path, page_rel in targets:
        if page_rel.startswith(stale_exempt_prefixes):
            continue
        if has_generated_marker(path):
            continue
        try:
            fm = load_front_matter(path)
        except front_matter_errors:
            fm = None  # front-matter errors are reported by another check
        if fm is not None:
            if fm.get("generated") is True:
                continue
            lifecycle = fm.get("lifecycle")
            if isinstance(lifecycle, str) and lifecycle in stale_exempt_lifecycles:
                continue
        text = None
        for surface_name, patterns, allowed_paths in RETIRED_SURFACES:
            if page_rel in allowed_paths:
                continue
            if text is None:
                text = path.read_text(encoding="utf-8")
            # An entry can also allow one specific line (`"<rel>#L<n>"`)
            # instead of the whole file -- for a multi-entry catalogue like
            # `docs/contribute/usecase-registry.yaml`, exempting the whole
            # file for one historical mention would silently blind this
            # sweep to a genuinely live occurrence of the same retired flag
            # added to a different entry later (CodeRabbit review, fresh
            # evidence). Line-pinned the same way `CLI_CONTRACT_ALLOWLIST`
            # in check_ai_readiness.py is: a later edit shifting the line
            # makes the allowlist entry stop matching, which re-surfaces the
            # warning for a human to re-pin rather than silently drifting.
            # Longest-first, and skip a shorter pattern's match when it falls
            # entirely inside a longer pattern's already-reported span (e.g.
            # a bare "--source-abi" match sitting inside an already-flagged
            # "--source-abi-cache-dir" occurrence) -- one real dead-surface
            # mention should produce one warning, not one per overlapping
            # registry entry that happens to match the same text.
            reported_spans: list[tuple[int, int]] = []
            for pattern in sorted(patterns, key=len, reverse=True):
                search_from = 0
                while True:
                    idx = text.find(pattern, search_from)
                    if idx == -1:
                        break
                    end = idx + len(pattern)
                    search_from = end
                    # A flag pattern must match a whole token, so a retired
                    # `--compile-db` is found in "`--compile-db`" and
                    # "-p/--compile-db" but not inside the still-live
                    # `--compile-db-filter`. Registering the trailing space
                    # instead (the first attempt) matched only one of those
                    # three and let punctuation-delimited live references
                    # through the gate entirely (Codex review).
                    if pattern.startswith("--") and end < len(text):
                        nxt = text[end]
                        if nxt.isalnum() or nxt in "-_":
                            continue
                    if any(idx >= s and end <= e for s, e in reported_spans):
                        continue
                    reported_spans.append((idx, end))
                    line_no = text.count("\n", 0, idx) + 1
                    if f"{page_rel}#L{line_no}" in allowed_paths:
                        continue
                    f.warn(
                        "retired-surfaces",
                        f"{rel(path)}:{line_no}: {pattern!r} names a retired "
                        f"surface ({surface_name}) outside its allowed pages -- "
                        "add historical framing or an allowlist entry in "
                        "_RETIRED_SURFACES if this mention is intentional",
                    )
