# CLAUDE.md — `abicheck/` package

Read the repository-root [`AGENTS.md`](../AGENTS.md), then the scoped
[`AGENTS.md`](AGENTS.md) in this directory. Those vendor-neutral files are
authoritative for module ownership, dependency direction, compatibility, and
verification.

This adapter intentionally adds no tool-specific architecture rules. In
particular, do not treat a legacy file as “large and intentionally so” or split
it only to satisfy the 2,000-line counter; follow the bounded-module migration
plan linked from `AGENTS.md`.
