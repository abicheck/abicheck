# CLAUDE.md — `skills-src/`

Editable source for abicheck's published Agent Skills (ADR-058, plan G36).
This is the **only** hand-authored copy. Everything under `.agents/skills/`,
`.claude/skills/`, and `.gemini/skills/` is generated from here by
`scripts/gen_agent_skills.py` — never hand-edit those trees.

## Layout

```text
skills-src/
  shared/                 Layer B — domain knowledge every skill draws on
    *.md                  one fragment per concept; exactly one copy of each fact
  <skill-name>/
    SKILL.md              Layer A — the skill itself (frontmatter + workflow)
    references/           material specific to this one skill only
```

Generated output, per skill, per target tree:

```text
.agents/skills/<skill-name>/         # canonical portable target
.claude/skills/<skill-name>/         # Claude Code's own read path
.gemini/skills/<skill-name>/         # Gemini CLI's own read path
  SKILL.md
  references/<skill-specific>.md
  references/shared/<fragment>.md    # copied, not symlinked
```

## The three layers (ADR-058)

- **Layer A — `SKILL.md`.** The only layer whose `name`/`description`
  competes for triggering. Contains: user-intent framing, the decision tree,
  tool-selection principles, the outcome shape, and termination criteria.
  Nothing long or reference-shaped.
- **Layer B — `shared/*.md`.** Reusable domain knowledge, written once and
  cited by link. A fact that more than one skill needs lives here, never
  paraphrased into two `SKILL.md` files.
- **Layer C — execution backends.** The abicheck CLI (normative), git, the
  project's build system, `nm`/`readelf`/`objdump`. A `SKILL.md` that reads
  like documentation for Layer C has drifted out of Layer A.

## Rules

1. **The CLI is the execution backend.** A skill's workflow drives
   `abicheck` through the shell and nothing else — no protocol server, no
   setup beyond installing the skill. (ADR-058 framed this as "CLI
   normative, MCP an optional adapter"; abicheck has since removed its MCP
   server, so the CLI is simply the backend.)
2. **Only reference surface that exists today.** `tests/test_agent_skills_drift.py`
   checks every CLI command/option and every report-JSON field path a skill
   names against the live Click tree and the live report schema. A renamed
   flag fails CI here rather than rotting in prose.
3. **Link shared fragments, never inline them.** From a `SKILL.md`, write
   `../shared/<fragment>.md`; from a `references/*.md` file, write
   `../../shared/<fragment>.md`. The generator resolves these transitively,
   copies what is actually cited, and rewrites the links.
4. **Every `shared/*.md` fragment must be cited** by at least one skill,
   directly or transitively. An orphaned fragment fails generation.
5. **Links to `docs/` are repo-relative** (`../../docs/learn/foo.md`). The
   generator rewrites them to the published documentation site so an
   installed skill has no dangling reference.
6. **Safety invariants live in `shared/safety-invariants.md`.** That file —
   not ADR-058's frozen prose — is where a safety correction is made.
7. **Every `SKILL.md` declares an abicheck version range** in its `metadata`
   frontmatter (`abicheck-version-range`), naming the release that actually
   provides the CLI surface its workflow drives — **not** whatever version
   happens to be installed here. In an unreleased tree
   `importlib.metadata` reports the *last published* version, so calibrating
   the range to it would approve an installation lacking the surface: that is
   how the range first came to say `>=0.5.0` while every workflow depended on
   `aggregate`, `project plan`, `--report-mode root-cause`,
   `--diagnostic-comparison`, and `--contract`, none of which
   0.5.0 shipped. `tests/test_agent_skills_structural.py` enforces the
   minimum against its own `SURFACE_FLOOR` constant — a stated fact about
   which release contains which commands, since nothing in the working tree
   can answer that. Do not re-derive it from the installed version or from
   whether `changelog.d/` is non-empty; both were tried, and each is wrong in
   one direction (see the constant's own comment). Raise it when a skill
   starts depending on surface that first ships in a later release, not when
   the tree's own version moves.

## Portfolio status (content rewrite, 2026-08-20 — "PR 2")

The four-skill portfolio the earlier (2026-08-11) freeze shipped was itself
frozen precisely because none of the four had measured evidence that it
improved agent behavior over a well-documented CLI/`AGENTS.md` alone — every
gate that existed then (`test_gen_agent_skills.py`,
`test_agent_skills_structural.py`, `test_agent_skills_drift.py`,
`test_agent_skills_triggers.py`) proved the *artifact* well-formed, never
that the *behavior* it produced beat the unequipped baseline. Per
[ADR-058's 2026-08-20 amendment](../docs/contribute/adr/058-native-compatibility-agent-skills.md),
that observation was carried to its conclusion: publishing three more
prototypes ahead of any such evidence was scaling packaging ahead of
validated product value, so the portfolio was reset to **one** internal
candidate. A second, same-date amendment ("PR 2") then rewrote that
candidate's workflow content — the four-step plan the reset amendment
implied is reset → rewrite → evaluate → publish, and this is step two.

| Skill | Status | Meaning |
|---|---|---|
| `review-native-library-change` (formerly `native-binary-compatibility-review`) | **Internal candidate — not yet validated, not for external publication.** | The sole published skill and the sole subject of any future G37 behavioral/comparative-lift evaluation. Not to be cited as validated in any user-facing claim until that evidence exists. |

**What "PR 2" integrated, over the bare rename the reset amendment left in
place:**

- A **customer-outcome framing**: the workflow opens on the decision being
  served ("will this break existing users, why, and what's the least costly
  safe fix"), not a CLI preflight, and ends on a structured decision report
  (`references/abicheck-adapter.md`'s field map; `SKILL.md`'s own decision
  table) rather than a command transcript.
- A **real, integrated named-consumer branch** (`SKILL.md`'s own step on
  narrowing to one consumer), not just the single linking citation to
  `shared/consumer-scoping.md` the reset amendment left behind. States
  inline when to reach for `--used-by` versus `--required-symbol`/
  `--required-symbols`, and the `verdict`-is-scoped /
  `full_verdict`-is-global reading rule.
- A new `references/remediation-patterns.md` (pImpl, reserved slots,
  versioned interfaces, capability negotiation, deprecation lifecycle,
  anti-patterns), cited from the recommendation step — harvested from
  `native-api-evolution`'s removed `design-patterns.md`, not a second copy
  of that skill's own workflow.
- An explicit **v0.1 validated-scope statement**: C/C++ shared libraries,
  Linux ELF, GCC/Clang, built artifacts plus public headers, matched
  profiles, PR/branch/candidate-build review. Everything else (Mach-O,
  PE/COFF, DPC++, cross-compiler migration, headerless review) can still be
  attempted, but the skill now says explicitly not to report that attempt
  with validated confidence.
- All exact CLI invocations, flag combinations, and report-JSON field
  paths moved out of `SKILL.md`'s body into a new
  `references/abicheck-adapter.md`, per the Layer A/B/C split above.

**Still open, unattempted by PR 2** (the ADR amendment above is the fuller
account): `native-release-compatibility`'s whole-release-matrix-qualification
concern remains explicitly *not* folded in. **PR 3 landed** (a complete G37
evaluation corpus, 12 scenarios, plus a real 48-run pilot) — see the ADR's
"PR 3" amendment and `agent-evals/skills/pilot-results/README.md`; its
dominant finding is a harness turn-budget confound, not a skill-quality
result, so the skill is still not behaviorally validated. PR 4 (a thin
external-distribution repository, removing the internal-candidate marker)
remains fully open.

`native-api-evolution`, `native-consumer-compatibility`, and
`native-release-compatibility` are no longer published — their source is
recoverable from git history (the commit preceding this reset on this
branch), not from the live `skills-src/` tree. Their valuable parts are not
lost: the flagship's remediation guidance and consumer-scoping dial already
draw on the same `shared/remediation-catalog.md` and
`shared/consumer-scoping.md` fragments those three cited, and
`native-release-compatibility`'s whole-release-matrix-qualification concern
remains a distinct future second-skill candidate (`qualify-native-library-
release`, not yet built) rather than something folded into this skill's
scope. See the ADR amendment for the full accounting of what was deferred.

**What this means in practice:**

- Don't add a second published skill. Rebuilding
  `native-release-compatibility` (or anything else) as a public skill needs
  its own pass through ADR-058's five-criteria admission bar, informed by
  whatever this one candidate's evaluation actually finds — not a
  restoration from git history.
- Don't cite `review-native-library-change` as validated in any user-facing
  claim; no behavioral evidence exists yet.
- This reset does not reopen ADR-058's five-criteria admission bar for a
  *new* skill on its own — see "Adding a public skill" below, unchanged.

## Adding a public skill

Do not, by default. ADR-058's admission bar requires a candidate to clear
**all five** criteria — distinct user intent, distinct decision tree,
distinct user-visible outcome, useful standalone, and enough specialized
domain knowledge to justify a skill. A candidate failing any one becomes a
`shared/` fragment or a branch inside an existing skill, not a fifth skill.
P2 candidates and the evidence each needs are recorded in
`docs/contribute/plans/g36-native-compatibility-agent-skills.md`. The
portfolio status above is a stronger, additional bar on top of this one:
even a candidate that clears all five criteria should not be pursued while
the sole candidate skill's own evaluation is still open.

## Workflow

```bash
python scripts/gen_agent_skills.py            # regenerate all three trees
python scripts/gen_agent_skills.py --check    # verify committed output in sync (CI)
pytest tests/test_gen_agent_skills.py tests/test_agent_skills_structural.py \
       tests/test_agent_skills_drift.py tests/test_agent_skills_triggers.py -q
```

`scripts/verify.py --profile pr` runs the same generation check as the
`agent-skills-generated` step. Commit the regenerated trees alongside any
`skills-src/` edit — the same contract every other generated artifact in this
repository is under.

## Cross-agent validation log

G36 P1.5 requires each P0 skill to be exercised end-to-end on Claude Code,
Codex, Copilot, and Gemini CLI (Cursor if current) before external
publication, with any agent-specific friction fixed here rather than forked
into a per-vendor copy. No target has been validated yet — record results
here as they are run.

| Target | Skills validated | Date | Notes |
|---|---|---|---|
| Claude Code | — | — | not yet run |
| Codex | — | — | not yet run |
| GitHub Copilot | — | — | not yet run |
| Gemini CLI | — | — | not yet run |
| Cursor (conditional) | — | — | not yet run |
