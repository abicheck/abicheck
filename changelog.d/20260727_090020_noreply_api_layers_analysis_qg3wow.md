### Fixed

- **`docs/integration/index.md` no longer misstates the Actions composition
  direction** — it claimed both the "Single-Action step" and "Reusable
  workflow" layers are "built out of" the "Primitive Actions" layer below.
  Verified against `actions/check-target/action.yml`'s "Run analysis" step:
  `check-target` (a primitive) itself checks out and invokes the root
  `abicheck/abicheck` composite Action directly, so the root Action actually
  sits underneath `check-target` in the real call graph — the reverse of a
  simple bottom-to-top ladder. Reworded to describe the actual graph
  (Codex review).
