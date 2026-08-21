---
doc_type: how-to
audience:
  - library-maintainer
  - ci-owner
canonical_for:
  - agent-skills
level: intermediate
lifecycle: active
generated: false
---

# Agent Skills

abicheck publishes one [Agent Skill](https://agentskills.io) — a portable,
triggerable package of native-compatibility expertise that a coding agent
(Claude Code, GitHub Copilot, OpenAI Codex, Cursor, Gemini CLI) loads when a
user asks a compatibility question in their own words.

The skills are named after the **user's job**, not after abicheck commands.
Someone who has never heard of abicheck can ask "will this change break
existing consumers?" and get a workflow that reaches for abicheck as its
deterministic verification engine. See
[ADR-058](../contribute/adr/058-native-compatibility-agent-skills.md) for why
the portfolio is shaped this way.

> **Requires unreleased abicheck 0.6.x to run.** You can install this skill
> (copying a directory works regardless of your abicheck version)
> against the latest published release, **{{ latest_published_version }}**
> — but it checks
> the installed abicheck version against its own declared range —
> `>=0.6.0,<0.7.0` — and refuses to run
> rather than fail partway through, since several commands/options it
> drives postdate 0.5.0 (see "Prerequisites" below). The check runs
> `abicheck --version` — the executable actually on `PATH` when the skill
> runs, not a source checkout's `pyproject.toml` (the two can differ, e.g.
> a 0.6.x checkout with a separately installed 0.5.0 package). If that
> version isn't inside the `0.6.x` range, the installed skill will decline
> to execute.

**Portfolio status (2026-08-20):** the portfolio was reset from four
published skills down to **one internal candidate**. No skill has measured
evidence yet that it improves agent behavior over a well-documented CLI
alone, so publishing three additional, unvalidated skills alongside the one
flagship was scaling packaging ahead of any validated product value.
`native-api-evolution`, `native-consumer-compatibility`, and
`native-release-compatibility` — previously published at prototype status —
are no longer published; their source is recoverable from git history for
whenever a second skill is built. `check-abi-compatibility` (renamed from
`review-native-library-change`, itself renamed from
`native-binary-compatibility-review`) is the sole surviving skill and
is an **internal candidate**: working, reviewed content, but not yet
validated and not for external publication or citation as validated in any
user-facing claim, pending
[G37](../contribute/plans/g37-agent-skill-quality-evaluation.md)'s
still-unrun behavioral evaluation. See
[ADR-058](../contribute/adr/058-native-compatibility-agent-skills.md)'s
2026-08-20 amendment for the full rationale (the same status is also kept in
`skills-src/CLAUDE.md`, the skill's own source tree).

## The catalogue

| Skill | Status | The question it answers |
|---|---|---|
| [`check-abi-compatibility`](https://github.com/abicheck/abicheck/blob/main/skills-src/check-abi-compatibility/SKILL.md) | **Internal candidate** | "Will this change break existing consumers?" — review a diff, branch, commit, or PR, ending in a verdict plus a root-cause explanation. Also handles "why did this suddenly report dozens of breaks?" |

## Installing them

`skills-src/` is the one hand-authored source; the three publication trees
below are **generated build output, not committed to this repository**
(2026-08-21 ADR-058 amendment) — regenerate them with
`python scripts/gen_agent_skills.py` (writes all three) or
`python scripts/install_dev_skill.py --target <name>` (writes one or more by
name: `codex`, `claude`, `gemini`, or `all`):

| Tree | Read by |
|---|---|
| `.agents/skills/` | GitHub Copilot, OpenAI Codex, Cursor — the portable, cross-vendor convention |
| `.claude/skills/` | Claude Code, which does not scan `.agents/skills` |
| `.gemini/skills/` | Gemini CLI, which does not either |

Each generated skill directory is fully self-contained: after generating it,
copy `.agents/skills/<skill-name>/` into your own project (or your personal
skills directory) and every reference it needs comes with it. There are no
symlinks and no cross-skill paths, so a single skill installs and works on
its own.

Skills are executable content. Anthropic's own guidance applies to these as
to any others: install only from sources you trust, and read what you install.
The rules these skills hold *themselves* to — never manufacture a green
result, never widen a suppression, never mutate a project silently — are in
[ADR-058's safety invariants](../contribute/adr/058-native-compatibility-agent-skills.md),
with the operational copy shipped inside every skill as
`references/shared/safety-invariants.md`.

## Prerequisites

The skills drive the abicheck CLI, so they need whatever the workflow they run
needs — see [CLI usage](cli-usage.md) for installation and
[evidence and build-context flags](dump-compare-flags.md) for what deeper
`--depth` levels require. Local CLI invocation is the only execution path a
skill uses, so a skill works in any agent with shell access and needs no
protocol server or other setup beyond installing the skill itself. (ADR-058
wrote this as "CLI normative, MCP an optional adapter"; abicheck has since
removed its MCP server, so the CLI is simply the backend.)

Each skill declares, in its frontmatter, the abicheck version range it
requires — the releases that actually provide the CLI surface it drives — and
checks the installed version before doing anything else rather than degrading
silently. The current portfolio targets **0.6.x**: several commands and
options these workflows depend on (`aggregate`, `project plan`,
`--report-mode root-cause`, `--diagnostic-comparison`,
`--contract`) postdate the 0.5.0 release, so an older installation
is refused up front instead of failing partway through a workflow.

## Contributing

Edit `skills-src/`, never the generated trees. `skills-src/CLAUDE.md` is the
contributor contract — the three-layer model, the shared-fragment rules, and
the admission bar a second public skill would have to clear. The phased plan is
[G36](../contribute/plans/g36-native-compatibility-agent-skills.md).

```bash
python scripts/gen_agent_skills.py          # regenerate all three trees
python scripts/gen_agent_skills.py --check  # what CI gates on
```
