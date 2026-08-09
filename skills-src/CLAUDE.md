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

1. **CLI is the normative backend.** No skill's correctness may depend on
   MCP being configured.
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
   frontmatter (`abicheck-version-range`). The structural test enforces
   containment of the installed version in both directions.

## Adding a public skill

Do not, by default. ADR-058's admission bar requires a candidate to clear
**all five** criteria — distinct user intent, distinct decision tree,
distinct user-visible outcome, useful standalone, and enough specialized
domain knowledge to justify a skill. A candidate failing any one becomes a
`shared/` fragment or a branch inside an existing skill, not a fifth skill.
P2 candidates and the evidence each needs are recorded in
`docs/contribute/plans/g36-native-compatibility-agent-skills.md`.

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
