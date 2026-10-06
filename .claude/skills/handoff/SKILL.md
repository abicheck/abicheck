---
name: handoff
description: Compact the current conversation into a handoff document for another agent to pick up.
argument-hint: "What will the next session be used for?"
disable-model-invocation: true
---

Write a handoff document summarising the current conversation so a fresh agent can continue the work.

Where to put it depends on where the next session runs:

- **Local session**: save it to the OS temporary directory (`$TMPDIR`, else `/tmp`; `%TEMP%` on Windows), not the workspace.
- **Cloud session** (claude.ai/code; the container is discarded when the session ends): save it to the session scratchpad, then make it reachable from a new container. If the work has a PR, post the document as a PR comment. Otherwise print it in full in your reply so the user can paste it into the next session. Commit it to the repository only if the user asks.

Contents:

- Goal, current state (branch, PR, last pushed commit, CI status), what is done, what remains, open decisions and blockers.
- A "suggested skills" section naming the skills the next agent should load with the Skill tool (for example `diagnosing-bugs`, `writing-for-agents`).
- Pointers to the scoped docs the next agent needs, such as `docs/contribute/agent-guide/*.md` or a layer `AGENTS.md`, rather than restating them.

Reference other artifacts (specs, plans, ADRs, issues, commits, diffs, PRs) by path or URL instead of duplicating their content.

Redact any sensitive information, such as API keys, passwords, or personally identifiable information.

If the user passed arguments, treat them as a description of what the next session will focus on and tailor the document accordingly.
