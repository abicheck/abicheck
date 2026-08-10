<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **Native compatibility Agent Skills (ADR-058, G36 P0).** Four portable
  [Agent Skills](https://agentskills.io) — `native-binary-compatibility-review`,
  `native-api-evolution`, `native-release-compatibility`, and
  `native-consumer-compatibility` — publish abicheck as a deterministic
  backend for a coding agent (Claude Code, GitHub Copilot, OpenAI Codex,
  Cursor, Gemini CLI) answering a real compatibility question in the user's
  own words, with no need to already know abicheck exists. Authored once in
  `skills-src/` (one `SKILL.md` per skill plus eleven shared Layer-B domain
  fragments) and published by the new `scripts/gen_agent_skills.py` generator
  into three self-contained, generated trees — `.agents/skills/` (the
  canonical portable target read directly by Copilot/Codex/Cursor),
  `.claude/skills/`, and `.gemini/skills/` — each skill copying, not
  symlinking, exactly the shared fragments it cites, so an installed skill
  never depends on a path outside its own directory. Local CLI invocation is
  the normative execution path for every skill; nothing depends on MCP being
  configured. Backed by a new structural/drift/trigger test suite
  (`tests/test_gen_agent_skills.py`, `tests/test_agent_skills_structural.py`,
  `tests/test_agent_skills_drift.py`, `tests/test_agent_skills_triggers.py`)
  checking every cited CLI command/option and report-JSON field against the
  live Click tree and report schema, and a labelled trigger corpus asserting
  the four skills discriminate real user requests from adjacent
  out-of-scope ones (REST/OpenAPI, database migrations, Java, generic
  JSON-schema compatibility). See
  [Agent Skills](https://abicheck.github.io/abicheck/use/agent-skills/) and
  [ADR-058](https://abicheck.github.io/abicheck/contribute/adr/058-native-compatibility-agent-skills/).
