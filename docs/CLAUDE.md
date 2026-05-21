# CLAUDE.md — `docs/`

User-facing documentation, published via `mkdocs` (config in
`/mkdocs.yml`). CI runs `mkdocs build --strict`, so dangling
internal links fail the build.

## Layout

- `getting-started.md`, `index.md`, `troubleshooting.md` — top-level
  landing pages.
- `user-guide/` — end-user docs (CLI flags, policy files, suppression,
  output formats).
- `concepts/` — conceptual reference (ABI vs API, change taxonomy,
  versioning model).
- `reference/` — generated/curated reference (CLI reference, file
  formats, JSON Schema notes).
- `examples/` — per-case Markdown docs that match the binary fixtures
  in `/examples/`. Generated via `scripts/gen_examples_docs.py` —
  regenerate after adding a new example.
- `development/` — contributor-facing docs (architecture, parity status,
  goals, ADRs in `development/adr/`).

## Conventions

- Every page must be reachable from `mkdocs.yml` nav (mkdocs --strict
  enforces this).
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
