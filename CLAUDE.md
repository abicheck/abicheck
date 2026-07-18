# CLAUDE.md — Claude Code bootstrap for abicheck

Canonical, vendor-neutral repository instructions live in **`AGENTS.md`** —
imported below so Claude Code loads the full contract automatically as part
of this file. `.github/copilot-instructions.md` and `.cursor/rules/` are the
equivalent adapters for Copilot and Cursor; all three point at the same
source instead of keeping independent copies (CLAUDE.md "M1-1"). If you're
updating repository-wide commands or invariants, edit `AGENTS.md`, not this
file.

@AGENTS.md

## Claude Code-specific notes

- Skills, slash commands, and MCP server configuration for a session are
  managed by the environment/user, not by this file.
- `scripts/check_ai_readiness.py`'s `claude-md-coverage` check requires a
  real `CLAUDE.md` inside each major sub-tree (`abicheck/`, `tests/`,
  `docs/`, `scripts/`, `eval/`, `validation/`, `abicheck/compat/`). Those are
  scoped, per-area context — not adapters to this root file — so add
  substantive content there, not another `@AGENTS.md` import.
