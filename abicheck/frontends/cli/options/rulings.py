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

"""ADR-068 D5 per-option rulings for ``compare`` and ``dump``.

**Every** visible option on either command has an entry here saying why it
is still a CLI option, checked against D5's three guards:

1. a genuine **per-invocation operand** (a per-run analysis input), not a
   stable project property that belongs in ``.abicheck.yml``;
2. **not one-for-one** — not a second spelling of a concept the surface
   already represents, and not a config key with a CLI twin;
3. **not an escape hatch that disables real analysis** — a flag whose whole
   job is to switch a decision off is a policy/suppression question, never
   a flag.

Why this table exists in this shape (plan Phase 7k)
---------------------------------------------------

It replaces ``inventory.COMPARE_FLAG_BUDGET_BASE``/``_RAISES``, the derived
ADR-037 D10.5 budget ledger, which had a hole its own docstring claimed it
did not: the ceiling was ``BASE + len(RAISES)`` and the test asserted
``visible <= budget``, so every flag *removed* from the base surface without
lowering ``BASE`` (or removed from ``RAISES``) turned into permanent slack a
later flag could occupy silently. Measured at the time of the replacement:
``visible=48``, ``BASE=41``, ``len(RAISES)=16``, budget ``57`` — **nine
flags of slack**, and ``--budget`` had in fact landed as a visible option
with no ledger entry. ``BASE`` being an opaque *count* rather than a list is
also why "which flags are ruled?" was unanswerable: it named none of them.

``dump`` had no ledger at all — nineteen visible options, zero written
rulings — even though ADR-037 D8.1 requires the two commands' shared
families not to drift.

So the ceiling is now exactly ``len(rulings)``, and
``tests/test_config_rebalance.py`` asserts an **exact bijection** in both
directions for both commands: an option with no ruling fails, and a ruling
naming an option that no longer exists fails. There is no slack to consume,
which is the whole point — a new flag cannot be added without stating, in
writing and in this file, which guard lets it in.

A ``deferred`` ruling is *not* a keep. It records an option this audit
judged demotable or removable, with the named, unlanded thing blocking it —
so a deferral cannot quietly become a permanent keep by nobody re-reading
it. ``blocker`` is mandatory for those and forbidden for the rest, enforced
at import time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: ``per_run_operand`` — clears all three guards; stays on the CLI.
#: ``deferred`` — ruled demotable/removable, blocked by a named prerequisite.
Disposition = Literal["per_run_operand", "deferred"]


@dataclass(frozen=True)
class OptionRuling:
    """One option's ADR-068 D5 ruling.

    *rationale* states which guard lets the option stay (or, for a
    ``deferred`` ruling, what it would become). *blocker* names the
    unlanded prerequisite — required for ``deferred``, rejected otherwise,
    so a keep can never be written as if it were pending someone else's
    work and a deferral can never lose its owner.
    """

    disposition: Disposition
    rationale: str
    blocker: str | None = None

    def __post_init__(self) -> None:
        if self.disposition == "deferred" and not self.blocker:
            raise ValueError("a deferred ruling must name the prerequisite blocking it")
        if self.disposition != "deferred" and self.blocker:
            raise ValueError(
                f"only a deferred ruling may name a blocker (got {self.blocker!r})"
            )


def _keep(rationale: str) -> OptionRuling:
    return OptionRuling("per_run_operand", rationale)


def _deferred(rationale: str, *, blocker: str) -> OptionRuling:
    return OptionRuling("deferred", rationale, blocker)


#: Every visible ``compare`` option, ruled. Alphabetical, so a reader can
#: check the table against ``compare --help-all`` without a diff tool.
COMPARE_OPTION_RULINGS: dict[str, OptionRuling] = {
    # ── Evidence inputs: what this run was given to look at ──────────────
    "--header": _keep(
        "The canonical L2 evidence input -- which headers describe each "
        "side's declared surface. Side-scoped (`old=`/`new=`), and the "
        "paths differ on every comparison of two different releases. Since "
        "Phase 7n it carries that evidence over both of its transports: a "
        "header file/directory, and a development package that ships them "
        "(the former --devel-pkg), which is the same per-run evidence under "
        "a different wrapper, not a second decision."
    ),
    "--include": _keep(
        "The include search path the -H headers parse under. Travels with "
        "--header (a checkout's include dirs move with its headers), so it "
        "is per-run for the same reason and side-scoped the same way. Not "
        "the same concept as `compile.options` in config: those are the "
        "*toolchain*'s stable flags, demoted in Phase 7b."
    ),
    "--sources": _keep(
        "The L4/L5 source checkout for this side. A source tree path is a "
        "per-run operand by construction -- it is the thing being compared."
    ),
    "--build-info": _keep(
        "The one build-evidence input for this side, over both of its kinds "
        "since Phase 7n: the L3 build directory / compile_commands.json / "
        "prebuilt pack (same per-run reasoning as --sources, whose tree it "
        "is auto-discovered inside when omitted) and the probe-matrix "
        "snapshot the separate --probe-matrix used to carry (which probes "
        "were run for this comparison is a property of the run, not the "
        "project). Routed on the document, and both may be given per side."
    ),
    "--debug-info": _keep(
        "The one separate-debug-info input for this side, over all three of "
        "its transports since Phase 7n: a debug package, a directory of "
        "debug files, and a detached debug file (the last two were "
        "--debug-root, whose per-side spelling also replaced --pdb-path in "
        "Phase 7i, since debug_resolver already searches a debug root for a "
        "PDB). Real per-run evidence either way: which .ddeb/.rpm carries "
        "this release's DWARF, and where this run's artifacts live, are not "
        "project properties."
    ),
    "--dump-manifest": _keep(
        "ADR-050 D3: a real multi-translation-unit dump for one side, in "
        "place of a single -H/--header list. Which side(s) need a manifest "
        "(and which manifest) varies per comparison, not a stable project "
        "setting."
    ),
    "--depth": _keep(
        "ADR-037 D5's single evidence dial. What a given invocation can "
        "afford to collect -- a fast PR check vs. a release gate -- is the "
        "archetypal per-run choice, and pinning it also declares an "
        "evidence contract this run must meet (exit 7)."
    ),
    "--include-system-declarations": _keep(
        "Shared with dump (cli_options.include_dependencies_option): whether "
        "to include toolchain/system-header declarations in a live-binary "
        "side's dependency scope for this comparison. Which mode a given "
        "invocation needs varies per run (matching whatever a baseline was "
        "dumped with), not a stable project setting."
    ),
    "--version": _keep(
        "The version label to attach when an input is a bare .so with no "
        "package metadata. It labels *these two operands*, so it is per-run "
        "by definition; side-scoped since the two sides differ."
    ),
    # ── The dependency walk: one measured keep, and its only two inputs ──
    "--follow-deps": _keep(
        "Measured keep -- §4.1 classified it AUTO with a cost check, the "
        "check was run (Phase 7i) and it rejects the classification. A dump "
        "with and without the flag differs by exactly one snapshot field, "
        "`provenance.dependency_info`, which embeds absolute host paths and "
        "whatever the host loader resolves. Unconditional would put "
        "host-specific paths in every snapshot (breaking dump "
        "reproducibility and the ADR-050 comparability contract), add a "
        "filesystem walk to every dump and both compare sides, and let "
        "findings depend on which libraries happen to be installed on the "
        "runner -- the exact 'never fabricate a break from host evidence' "
        "rule this workstream is subordinate to. So it selects real "
        "per-run evidence rather than merely enabling a useful analysis. "
        "Revisit only if the dependency graph is made host-independent "
        "(sonames + resolution reasons, no absolute paths), which is a "
        "snapshot-schema change, not a CLI one."
    ),
    "--search-path": _keep(
        "An input to --follow-deps' walk, kept with it: §4.3's reasoning "
        "for `deps` (*the environment is the operand*) applies here "
        "whenever the walk is requested at all. Audited against --ld-"
        "library-path for a possible merge and ruled distinct, not "
        "duplicate: this appends to the loader's step 4 (default "
        "directories), while --ld-library-path inserts at step 2, ahead of "
        "DT_RUNPATH and the defaults (`resolver._candidate_dirs`). They "
        "also record different `resolution_reason` values on the resolved "
        "node. Collapsing them would silently change which library a run "
        "resolves -- an analysis consequence, not a spelling change."
    ),
    "--ld-library-path": _keep(
        "The other input to --follow-deps' walk. Kept for the same "
        "environment-is-the-operand reason as --search-path, and "
        "deliberately *not* merged with it: see that entry for the "
        "loader-step measurement (step 2 vs step 4) that makes the two a "
        "real semantic pair rather than two spellings of one concept."
    ),
    # ── Operand and scope selectors ──────────────────────────────────────
    "--no-baseline": _keep(
        "ADR-068 D2: declares that OLD is absent for this invocation -- the "
        "audit mode that replaces `scan`. Whether a baseline exists is a "
        "fact about this run (a first release, a new library in a PR), and "
        "it is an acquisition state ADR-065 records, never a project "
        "setting that would silently make every future run baseline-free."
    ),
    "--variant": _keep(
        "ADR-062 A1.7: which VariantRef to compare when a stored "
        "ProjectSnapshot package declares more than one. Comparison scope "
        "(ADR-065) is per-run by definition. Phase 7j collapsed the former "
        "--old-variant/--new-variant pair into this one side-scoped option "
        "-- the spelling was the duplicate, not the capability."
    ),
    "--select": _keep(
        "ADR-065 S1: declares an expected release member by its canonical "
        "matching key, narrowing which members this run compares. Audited "
        "against --select-required for a merge and ruled distinct: this one "
        "declares scope, that one declares a *completeness obligation* "
        "whose breach feeds the orthogonal scope exit axis (D6). Folding "
        "them would need an invented `KEY:required` grammar for a "
        "distinction two flags already state plainly."
    ),
    "--select-required": _keep(
        "ADR-065 S1/D6: like --select, but a missing declared member "
        "contributes to the completeness gate. See --select for why the "
        "pair is not one concept spelled twice."
    ),
    "--since": _keep(
        "ADR-068 Phase 2c: the git ref this run's changed-path scope is "
        "computed against. A PR's own diff is the archetypal per-run "
        "operand -- it differs on every single invocation and is never a "
        "property of the project. Scoping input only: it narrows which "
        "translation units the L4/L5 replay examines and produces no "
        "finding of its own, so it is not an analysis-disabling hatch."
    ),
    "--changed-path": _keep(
        "ADR-068 Phase 2c: the explicit form of --since, for a caller that "
        "already knows the changed files (a CI job that computed the diff "
        "itself, or a non-git checkout). Same per-run rationale, same "
        "scoping-only effect; one of the two is redundant only if abicheck "
        "assumes every consumer has a git working tree, which it does not."
    ),
    "--abi3": _keep(
        "ADR-068 Phase 2d: activates the candidate-side stable-ABI audit "
        "for this run and, when given, overrides the project's own declared "
        "floor. The *floor* is the stable half and lives in .abicheck.yml's "
        "`python.abi3_floor` -- what stays per-run is whether to audit at "
        "all and against which experimental floor ('what would raising us "
        "to 3.12 cost?'). Deliberately visible rather than hidden: D5 "
        "counts a hidden-but-accepted option as public surface anyway, so "
        "hiding it would understate the surface instead of documenting it."
    ),
    # ── Consumer contracts ───────────────────────────────────────────────
    "--used-by": _keep(
        "ADR-043: folds the removed `appcompat` command into compare -- "
        "scopes the comparison to one or more applications' actual imports. "
        "Which application(s) to check against varies per run, not a "
        "project setting. Genuine consumer evidence (vision D-S1), and "
        "never verdict-suppressing: the full library comparison still "
        "determines the run's own exit code."
    ),
    "--used-by-manifest": _keep(
        "Workstream D-S1: a JSON document naming one or more consumer "
        "binaries with optional digest/platform/profile/provider-baseline "
        "provenance and an advisory/required distinction, merged into the "
        "same --used-by pipeline. Audited against --used-by for a "
        "`@FILE` merge on the Phase 7h --required-symbols precedent and "
        "ruled *not* the same case: --required-symbols FILE fed the "
        "identical contract as an inline symbol, whereas a manifest carries "
        "provenance and an advisory/required requirement a bare consumer "
        "path cannot express. Collapsing them would either drop that "
        "content or overload one flag with two value grammars."
    ),
    "--required-symbol": _keep(
        "ADR-043: folds the removed `plugin-check` command into compare -- "
        "an explicit required-entrypoint contract for a plugin-host "
        "pairing. Varies per run (which symbols a given host resolves), not "
        "a project setting. Already absorbed Phase 7h's --required-symbols "
        "as its `@FILE` value form."
    ),
    # ── Document operands: a flag naming a document is still per-run ─────
    "--config": _keep(
        "Which .abicheck.yml this run reads. The one option that cannot be "
        "demoted into .abicheck.yml without a bootstrap paradox, and the "
        "trust boundary for ADR-032 D5: an explicit --config is what "
        "authorizes executing a `build.query`, so it is load-bearing "
        "security surface, not a convenience path."
    ),
    "--policy": _keep(
        "Names the policy document this run is judged under. The 7d-ruled "
        "class: a CLI flag naming a *document* stays CLI even though the "
        "document itself is a stable project artifact, because which "
        "document applies to this invocation is the per-run choice."
    ),
    "--suppress": _keep(
        "Names this run's suppression document -- the same document-operand "
        "class as --policy, and the supported route for a finding a user "
        "wants gone (which is what lets D5 reject analysis-disabling "
        "flags outright)."
    ),
    "--pack": _keep(
        "ADR-049 D8: selects a reusable configuration pack (policy/contract/"
        "gate) for this comparison. Which packs apply varies per run -- the "
        "same library is checked against a vendor SDK contract in one "
        "invocation and an internal CI gate in another. Revisit if a "
        "project-config `packs:` key lands: D7 already reserves the "
        "`project_config` tier below packs, so a permanent project-wide "
        "selection would belong there and this flag would become its "
        "per-run override."
    ),
    "--bundle-facts-out": _keep(
        "Ruled in Phase 7d and re-affirmed: PATH names where *this "
        "invocation's* evidence capture lands, exactly -o/--output's shape. "
        "`dump` has no directory/package fan-out at all -- no dump "
        "capability produces a multi-library BundleFacts document -- so "
        "this is not a duplicate spelling of a dump capability, and there "
        "is no config vocabulary to merge into without inventing one purely "
        "to move a path string. Revisit only if dump grows a release "
        "fan-out."
    ),
    # ── Gate, contract and rendering surface ─────────────────────────────
    "--contract": _keep(
        "ADR-049: opts one invocation into the contract evaluator and picks "
        "which evidence domain it judges each finding against (public/"
        "exports/all). What a given run is asking varies with it -- 'what "
        "does my declared header surface promise' vs. 'what does this "
        "binary actually export' -- so it is a per-invocation choice, not a "
        "stable project default."
    ),
    "--severity-preset": _keep(
        "Selects the gate scheme this invocation is scored under. A release "
        "gate and an exploratory local diff want different strictness from "
        "the same project, and the *stable* half already lives in "
        ".abicheck.yml's `severity:` block -- this is the per-run override "
        "of it, not a second copy (the four per-category overrides were "
        "that, and were removed)."
    ),
    "--budget": _keep(
        "ADR-068 SS3 #19: a wall-clock guard on this run's deadline-aware "
        "stages, so a CI job fails clearly (exit 5) instead of running "
        "unbounded. How long *this* job may take is a property of the "
        "runner and the job, not of the library's compatibility contract. "
        "Not an analysis-disabling hatch: overflow is a dedicated, loud "
        "exit axis, never a silently truncated comparison reported as "
        "clean. (This is the flag that landed with no ledger entry at all "
        "under the superseded BASE+RAISES budget -- see this module's "
        "docstring.)"
    ),
    "--diagnostic-comparison": _keep(
        "ADR-050 D2's sanctioned escape hatch, and the calibrated, bounded "
        "kind D5 explicitly permits: it never disables analysis, it "
        "downgrades one hard comparability failure into a diff stamped "
        '`assurance: "none"` everywhere. Whether a given OLD/NEW pair '
        "happens to be incomparable varies per run, not per project."
    ),
    "--output": _keep(
        "The one export request: -o FORMAT=DESTINATION, repeatable, with "
        "'-' for stdout. Which artifacts this invocation produces, and "
        "where -- a per-run presentation choice by construction, since the "
        "same project renders markdown for a PR comment and SARIF for code "
        "scanning from the identical analysis. Plan slice 7m rewrote this "
        "ruling when it collapsed four mechanisms into this one: --format "
        "(the renderer), the path-only -o (one destination), --write "
        "FORMAT=PATH (a second artifact) and --output-dir (the "
        "per-component fan-out) were four ways to answer one question, and "
        "are now one grammar plus two destination shapes ('-' and a "
        "trailing '/'). Phase 7k's own reason for declining the "
        "--write/--format merge -- that --format with no -o renders to "
        "stdout, which --write's PATH-only grammar could not express -- is "
        "what '-' dissolves. Guard 2 in particular is now *satisfied* "
        "rather than argued: there is exactly one spelling for 'produce "
        "this artifact there', so no two of them can drift."
    ),
    "--view": _keep(
        "ADR-068 D4 / Phase 5: the single 'render which parts, how' "
        "selector that absorbed --report-mode, --show-only, "
        "--explain-patterns and --demangle/--no-demangle. Per-run "
        "presentation, and deliberately presentation *only* -- decoupling "
        "--explain-patterns from --pattern-verdicts is what stopped a "
        "rendering choice from changing the analysis."
    ),
    "--dry-run": _keep(
        "ADR-043: resolve and validate the invocation without running the "
        "diff. A per-run preview toggle, not a stable project setting."
    ),
    "--verbose": _keep(
        "Per-run diagnostic verbosity on stderr. Changes no finding, "
        "verdict, gate or exit code."
    ),
    # ── Deferred: ruled demotable/removable, each with a named blocker ───
    # `--env-matrix` (PR #1221) and `--require-complete-analysis` (this PR)
    # have both since been fully retired -- `deployment:`/
    # `assurance.require_complete` config-key resolver wiring landed and
    # each CLI flag/Action input was removed, closing the followups their
    # entries here used to track. No entry remains for either, matching
    # the precedent of every other fully-retired option (e.g.
    # `--build-target`, PR #1219) never appearing in this dict at all.
    "--scope-public-headers": _deferred(
        "Phase 9 collapses this into `--contract public`/`--contract all` "
        "so there is one contract mechanism rather than two. Explicitly "
        "out of scope for this audit: the governing rule is 'never trade a "
        "possible false negative for a shorter CLI', and pulling it "
        "forward would do exactly that.",
        blocker="public-contract-default.md Phase 6's open relevance defects",
    ),
    "--post-manifest": _deferred(
        "A second contract/scope mechanism next to --contract; Phase 9 "
        "re-expresses it as a contract overlay. Same gate and same "
        "reasoning as --scope-public-headers -- not touched here.",
        blocker="public-contract-default.md Phase 6's open relevance defects",
    ),
    "--instantiation-manifest": _deferred(
        "A declared contract document is a project property (§4.1's CONFIG "
        "row), but its config home needs the ADR-049 contract-vocabulary "
        "coordination that has not landed, and inventing a lone path key "
        "ahead of it is the ad hoc config plumbing this workstream warns "
        "against (guard 2: not one-for-one).",
        blocker="ADR-049 contract-document config home",
    ),
    "--use-cases": _deferred(
        "The same document-operand class as --policy/--suppress, which is "
        "why Phase 7i kept it -- but unlike those two it has a real "
        "`use_cases:` destination waiting in the G29/ADR-057 attribution "
        "surface, so it is recorded as a deferral rather than a permanent "
        "keep. Worth landing with the rest of that surface, not as a lone "
        "path key.",
        blocker="G29/ADR-057 attribution surface (`use_cases:` block)",
    ),
    "--bundle-facts-library-manifest": _deferred(
        "Ruled in Phase 7d and re-affirmed: its per-library header/include/"
        "compile-context override shape has no existing .abicheck.yml home "
        "-- `bundle: {system_providers, cohorts}` is an unrelated concept "
        "-- and inventing one now would be exactly the ad hoc plumbing "
        "guard 2 rules out.",
        blocker="G42 (named deployment environments and provider resolution)",
    ),
}


#: Every visible ``dump`` option, ruled. ``dump`` had no ledger of any kind
#: before Phase 7k, despite ADR-037 D8.1 requiring its shared families not to
#: drift from ``compare``'s -- so a shared option's entry here deliberately
#: points at ``compare``'s rather than restating it, and the two must move
#: together.
DUMP_OPTION_RULINGS: dict[str, OptionRuling] = {
    # ── Shared with compare, ruled identically (ADR-037 D8.1) ────────────
    "--header": _keep(
        "Same ruling as `compare --header`: the canonical L2 evidence input."
    ),
    "--include": _keep("Same ruling as `compare --include`: travels with -H, per-run."),
    "--sources": _keep(
        "Same ruling as `compare --sources`: the L4/L5 checkout being captured."
    ),
    "--build-info": _keep(
        "Same ruling as `compare --build-info`: this run's L3 evidence. "
        "Single-sided and compile-context-only here -- a probe matrix is "
        "folded across two sides, so it has no meaning on a one-artifact "
        "capture and `dump` never took one."
    ),
    "--depth": _keep(
        "Same ruling as `compare --depth`: ADR-037 D5's single evidence dial."
    ),
    "--debug-info": _keep(
        "Same ruling as `compare --debug-info`, minus the package transport: "
        "a per-run artifact location, in either of the two transports a "
        "single-binary operand can resolve (a directory to search or the "
        "detached file itself). Phase 7n renamed it from --debug-root; a "
        "debug package is a release transport and a usage error here."
    ),
    "--dump-manifest": _keep(
        "Same ruling as `compare --dump-manifest`: ADR-050 D3 multi-TU operand."
    ),
    "--include-system-declarations": _keep(
        "Same ruling as `compare --include-system-declarations`: the "
        "dependency-scope mode this capture is taken under, which a later "
        "compare must match (`AbiSnapshot.dependency_scope`, schema v18)."
    ),
    "--follow-deps": _keep(
        "Same measured keep as `compare --follow-deps` -- and `dump` is "
        "where the measurement was actually taken (a real dump of a fixture "
        ".so with and without the flag, differing by exactly the "
        "host-path-bearing `provenance.dependency_info` field). It also has "
        "a dedicated Action input (`follow-deps`), so demoting it would "
        "leave a documented input with nothing to drive."
    ),
    "--search-path": _keep(
        "Same ruling as `compare --search-path`, including the loader-step measurement that keeps it distinct from --ld-library-path."
    ),
    "--ld-library-path": _keep(
        "Same ruling as `compare --ld-library-path`; see that entry and --search-path's."
    ),
    "--config": _keep(
        "Same ruling as `compare --config`, including its ADR-032 D5 role as the only authorization for executing a `build.query`."
    ),
    "--output": _keep(
        "Where this invocation's snapshot is written. Deliberately NOT "
        "`compare --output`'s grammar: this names a *snapshot* destination, "
        "not a report format and destination, so plan slice 7m's "
        "FORMAT=DESTINATION export request left it alone -- `dump` produces "
        "one artifact of one kind, and there is no 'which projection' "
        "question here to answer."
    ),
    "--version": _keep("Same ruling as `compare --version`: labels *these* operands."),
    "--dry-run": _keep(
        "Same ruling as `compare --dry-run` (ADR-043 D9): resolve and "
        "validate the invocation without writing a snapshot."
    ),
    "--verbose": _keep(
        "Same ruling as `compare --verbose`: per-run diagnostic verbosity on "
        "stderr, changing no captured fact."
    ),
    # ── dump-only ────────────────────────────────────────────────────────
    "--provenance": _keep(
        "Phase 7f's repeatable KEY=VALUE merge of --git-tag/--build-id/"
        "--no-git -- three spellings of 'stamp this snapshot' collapsed "
        "into one. What stamps *this* capture (a tag, a build id, whether "
        "to consult git at all) is per-run by construction: the tag differs "
        "on every release and the build id on every build."
    ),
    "--compression": _keep(
        "§4.2 classified this AUTO ('inferred from the -o suffix; `auto` is "
        "already the default and already correct') and this audit "
        "**rejects** that row, on the counter-argument Phase 7i recorded "
        "but left unresolved: the row assumes the -o suffix always encodes "
        "the intent, and `-o build/abi.json --compression zstd` -- a CI job "
        "publishing a fixed artifact name -- is a real case where it does "
        "not. Inference cannot express 'this name, that encoding', so "
        "removing the flag would remove a capability rather than derive it. "
        "Ruled a per-run operand: which encoding this artifact is published "
        "in is a property of the publishing job. Its Action input "
        "(`snapshot-compression`) therefore stays too, with something real "
        "to drive."
    ),
}


#: command name → its ruling table, for the gate and its test mirror.
RULINGS_BY_COMMAND: dict[str, dict[str, OptionRuling]] = {
    "compare": COMPARE_OPTION_RULINGS,
    "dump": DUMP_OPTION_RULINGS,
}

#: ADR-037 D10.5's per-command visible-option ceiling, now *exactly* the
#: number of options carrying a written ruling. Kept under the old
#: ``COMPARE_FLAG_BUDGET`` name in ``inventory``/``cli_options`` so ADR-040's
#: machine-checked scoreboard keeps its identifier -- what changed is that
#: the number can no longer exceed the ruled set, so there is no slack.
COMPARE_FLAG_BUDGET = len(COMPARE_OPTION_RULINGS)
DUMP_FLAG_BUDGET = len(DUMP_OPTION_RULINGS)
