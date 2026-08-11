---
doc_type: how-to
audience:
  - library-maintainer
  - ci-owner
level: intermediate
lifecycle: active
generated: false
---

# Agent Skills

abicheck publishes four [Agent Skills](https://agentskills.io) — portable,
triggerable packages of native-compatibility expertise that a coding agent
(Claude Code, GitHub Copilot, OpenAI Codex, Cursor, Gemini CLI) loads when a
user asks a compatibility question in their own words.

The skills are named after the **user's job**, not after abicheck commands.
Someone who has never heard of abicheck can ask "will this change break
existing consumers?" and get a workflow that reaches for abicheck as its
deterministic verification engine. See
[ADR-058](../contribute/adr/058-native-compatibility-agent-skills.md) for why
the portfolio is shaped this way.

**Portfolio status (2026-08-11):** none of the four skills has measured
evidence yet that it improves agent behavior over a well-documented CLI
alone. `native-binary-compatibility-review` is the sole flagship subject of
[G37](../contribute/plans/g37-agent-skill-quality-evaluation.md)'s ongoing
behavioral evaluation; the other three are published but frozen at
**prototype** status — working, reviewed content, but not validated and not
receiving new scope — until the flagship experiment demonstrates a
measurable lift. See ADR-058's 2026-08-11 amendment and
[`skills-src/CLAUDE.md`](https://github.com/abicheck/abicheck/blob/main/skills-src/CLAUDE.md#portfolio-status-g37-scope-freeze-2026-08-11)
for the full rationale.

## The catalogue

| Skill | Status | The question it answers |
|---|---|---|
| [`native-binary-compatibility-review`](https://github.com/abicheck/abicheck/blob/main/.agents/skills/native-binary-compatibility-review/SKILL.md) | **Flagship** | "Will this change break existing consumers?" — review a diff, branch, commit, or PR, ending in a verdict plus a root-cause explanation. Also handles "why did this suddenly report dozens of breaks?" |
| [`native-api-evolution`](https://github.com/abicheck/abicheck/blob/main/.agents/skills/native-api-evolution/SKILL.md) | Prototype | "How do I make this API change *without* breaking compatibility?" — design-time guidance (pImpl, reserved slots, versioned interfaces, deprecation lifecycles), ending by verifying the resulting change. |
| [`native-release-compatibility`](https://github.com/abicheck/abicheck/blob/main/.agents/skills/native-release-compatibility/SKILL.md) | Prototype | "Can we ship this as a minor version, or does it need a major bump?" — a release-level decision across every library, platform, and profile. |
| [`native-consumer-compatibility`](https://github.com/abicheck/abicheck/blob/main/.agents/skills/native-consumer-compatibility/SKILL.md) | Prototype | "Will *this specific* application, plugin, or host keep working?" — a per-consumer answer that can differ from the library's global verdict. |

## Installing them

The skills are committed to this repository in three trees, all generated
from one source:

| Tree | Read by |
|---|---|
| `.agents/skills/` | GitHub Copilot, OpenAI Codex, Cursor — the portable, cross-vendor convention |
| `.claude/skills/` | Claude Code, which does not scan `.agents/skills` |
| `.gemini/skills/` | Gemini CLI, which does not either |

Each skill directory is fully self-contained: copy
`.agents/skills/<skill-name>/` into your own project (or your personal skills
directory) and every reference it needs comes with it. There are no symlinks
and no cross-skill paths, so a single skill installs and works on its own.

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
`--contract-evaluation`) postdate the 0.5.0 release, so an older installation
is refused up front instead of failing partway through a workflow.

## Contributing

Edit `skills-src/`, never the generated trees. `skills-src/CLAUDE.md` is the
contributor contract — the three-layer model, the shared-fragment rules, and
the admission bar a fifth public skill would have to clear. The phased plan is
[G36](../contribute/plans/g36-native-compatibility-agent-skills.md).

```bash
python scripts/gen_agent_skills.py          # regenerate all three trees
python scripts/gen_agent_skills.py --check  # what CI gates on
```
