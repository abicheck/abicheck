---
name: retro
description: "Conduct a retrospective on one or more coding-agent sessions (local or cloud) to improve this repository's agent environment."
argument-hint: "Which session(s)? A session id/URL, a PR, a time range, or 'current'."
disable-model-invocation: true
---

The user has asked for a **retrospective**. You are suggesting improvements to the coding agent's **environment** (instructions, docs, checks, tooling) so future runs on this repository are cheaper and more correct. You propose; the user decides what lands.

## Steps

1. Call the Skill tool with `writing-for-agents` for the writing style guide.

2. **Collect the primary sources** for the session(s) the user named. Default to the current session if none is named. Read the transcript itself, not a summary, and note each session's id so findings can cite it.
   - **Current session**: your own context, only if it still holds the full transcript (a compacted context is a summary). Otherwise retrieve the transcript (`list_events` with `get_session`'s id) or list the session as unreadable with the reason.
   - **Other cloud sessions** (claude.ai/code): use the `claude-code-remote` MCP tools. With no session named, take the last 7 days. `list_sessions` (with `mine: true`) mixes every repository the account works in and its output overflows to a file: parse the ids from that file, then keep only sessions whose `get_session` sources include `abicheck/abicheck`. Read each kept transcript with `list_events` and `kinds: ["user", "assistant", "result"]`, paging with `before_id`/`after_id` until `has_more` is false. Pages overflow too, so hand each one or two sessions to a reader subagent that returns the step 3 metrics with event-id citations, and keep the transcripts out of your own context.
   - **Linked PRs**: review threads, CI failures and fix-up commits on the session's PR (GitHub MCP tools) show what slipped through.
   - **Local sessions**: transcripts under `~/.claude/projects/<project-dir>/*.jsonl`.
   - **Shared session archive**: <!-- TODO: location to be provided by the maintainer --> not configured yet. If the user names a location, read it from there.

   This step is done when every requested session has been read end to end, or listed as unreadable with the reason.

3. **Measure before judging.** For each session, record: turns; tool calls by type; files and docs read (and how many reads were re-reads or dead ends); the largest tool outputs; full test-suite runs and their durations; failed pushes or CI rounds; notification or stop-hook wake-ups that ended with no action, and the session's `cost_usd`; and the moments the user had to correct the agent. These numbers rank the findings.

4. Look for candidates for improvement in these categories:

- **Navigation**: how easy was it for the agent to find the right files? Would a **navigation pointer** in the root `AGENTS.md` "Where to read next" table or a layer `AGENTS.md` help? _Use when_ the session took a long time to find a piece of information.
- **Automated checks**: could a check have caught an error the agent made? Read `scripts/verify.py`'s profiles, `scripts/check_ai_readiness.py`, `scripts/check_architecture.py` and `.github/workflows/ci.yml` first, so a check that already exists but is unwired, skipped locally or silently broken is the finding rather than a reinvention. A **mechanical** violation (a syntactic pattern, a banned API, an import shape, a file-location rule) gets a deterministic check, usually a new `check_ai_readiness.py` gate or a `verify.py` step. Prose rules are for judgement calls only. _Use when_ the agent made a mistake a check could have caught.
- **Review rules**: should the reviewer (Claude Code Review, `/code-review`) enforce a new rule, or should an existing one be removed or clarified? _Use when_ review missed a mistake or kept flagging non-issues.
- **Always-loaded instructions**: is the root `AGENTS.md` (imported by `CLAUDE.md`, loaded every turn) or a scoped `AGENTS.md`/`CLAUDE.md` carrying material only some tasks need? Move it behind a pointer into `docs/contribute/agent-guide/`. _Use when_ a steering file is large, or a session never used most of what it loaded.
- **Tool economy**: expensive tool calls that could be streamlined, such as the full ~12 min unit suite run where a targeted test file would do, whole-file reads of 1500+ line modules, repeated searches for the same thing, or oversized MCP outputs. _Use when_ the agent made an expensive call.
- **Environment**: the cloud setup itself (`.claude/hooks/session-start.sh`, `scripts/setup_dev_env.sh`, network policy, missing tools, slow installs). _Use when_ time went to setup, a missing tool, or a blocked host.
- **No-ops**: steering text that changes no behavior, or restates what a gate already enforces. _Use when_ steering files are large and unwieldy.
- **Information access**: was a crucial piece of information unavailable (CI logs, a stored baseline, a toolchain)? _Use when_ the agent had to guess.

5. **Present the candidates** to the user, ordered by severity. Weigh how often the problem recurs across the sessions read against its cost per occurrence. For each candidate give the evidence (session id, the turn or tool call, its cost), the proposed change (the file and the wording or check), and which of the two loads it spends (context or cognitive). Make the changes only after the user picks which ones to apply.

## Reference

### Implementation vs review

Work goes through two stages. The implementation agent carries the most **context pressure**: it explores, writes code and debugs. The review agent receives a diff and carries the least. So coding standards belong to review and to automated checks, not to always-loaded implementation instructions.

### Where things live in this repository

- `AGENTS.md` (root, via `CLAUDE.md`): every task's essentials plus the "Where to read next" pointer table. Keep it about 20 KB.
- `abicheck/<layer>/AGENTS.md`, `tests/CLAUDE.md`, `scripts/CLAUDE.md`, …: scoped context that loads when the agent works in that tree.
- `docs/contribute/agent-guide/`: disclosed reference reached through pointers.
- `docs/contribute/known-gaps.md`: investigated-but-unfixed gaps and reverted fixes.
- `.claude/skills/`: hand-authored skills for developing this repository. Generated product skills from `skills-src/` are gitignored there by name.
- Gates: `scripts/verify.py` (definition of done), `scripts/check_ai_readiness.py`, `scripts/check_architecture.py`.
