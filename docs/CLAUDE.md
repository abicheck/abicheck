# CLAUDE.md — `docs/`

User-facing documentation, published via `mkdocs` (config in
`/mkdocs.yml`). CI runs `mkdocs build --strict`, so dangling
internal links fail the build.

## Layout

Note: file locations and `mkdocs.yml` nav grouping are independent. Several
files live at the docs root or under `concepts/`/`reference/` but are grouped
elsewhere in the nav — keep links pointing at the real file path.

- `index.md` — home / landing page.
- `getting-started.md` — top-level file, but navigated as the **first page of
  the User Guide**.
- `troubleshooting.md` — top-level file, but navigated under **Development**.
- `user-guide/` — end-user docs (getting started, GitHub Action, CLI flags,
  policy files, suppression, output formats). Nav is grouped basics-first:
  Start Here → Everyday Use → CI & Gating → Specialised Checks →
  Integrations & Migration.
- `concepts/` — conceptual docs (verdicts, evidence model, architecture, and
  `abi-api-handling.md` — the consolidated ABI/API handling guide).
  `abi-cheat-sheet.md` and `abi-api-handling.md` are navigated under the
  educational **ABI/API Handling & Recommendations** tab, not Concepts.
- `reference/` — curated reference (change kinds, exit codes, platforms, tool
  comparison, ABICC format compliance). Navigated as its own **Reference** tab.
- `examples/` — per-case Markdown docs that match the binary fixtures
  in `/examples/`. Generated via `scripts/gen_examples_docs.py` —
  regenerate after adding a new example. Navigated as its own **Examples**
  tab (index + by-verdict + by-category; per-case pages are linked, not
  in nav).
- `development/` — contributor-facing docs (architecture, parity status,
  goals, ADRs in `development/adr/`).

## Conventions

- Every page must be reachable from `mkdocs.yml` nav (mkdocs --strict
  enforces this). Exceptions: per-case `examples/*.md` pages are linked from
  the encyclopedia indexes instead of the nav, and this `CLAUDE.md` is
  excluded from the published site via `exclude_docs`.
- The docs tell a two-track story: an **educational track** (ABI/API Handling
  tab — understanding the problem) and a **tool track** (User Guide → Concepts
  → Reference — using and understanding abicheck). Within each track, order
  pages simple → advanced.
- Use relative links (`../user-guide/x.md`), not absolute URLs.
- Prefer pulling from `--help` output rather than hand-rolling CLI
  tables — use the same wording the user sees.
- `ChangeKind` references: use the enum value (e.g. `symbol_removed`)
  or the enum NAME (`SYMBOL_REMOVED`); the AI-readiness check accepts
  either form.

## Regenerating examples docs

```bash
python scripts/gen_examples_docs.py
```

Then commit the resulting `docs/examples/*.md`.
