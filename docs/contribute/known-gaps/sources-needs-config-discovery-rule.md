---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `--sources` needs a config discovery rule before it can move

Index: [Known gaps, closed](../archive/known-gaps-closed.md)

**Status (2026-10-31): CLOSED (by design, maintainer decision)** ([archive](../archive/known-gaps-closed.md)). `--sources` gets no `.abicheck.yml` home, and no `sources.root` key is added. The source tree is a per-side, per-run operand (ADR-091 D4 amendment (a)/(b); `frontends/cli/options/rulings.py`), not a project setting. The one stable case, `dump` run in its own checkout, is already served by config discovery (`config_paths.resolve_project_config` finds the `.abicheck.yml` at the `--sources` tree root or above the current directory). ADR-091 records the decision in a dated D4 amendment.

The options weighed, for the record:

1. One config per run, no chaining: a `sources.root` key honored from `--config` or the cwd-discovered config, never triggering discovery of a second config inside the named tree.
2. Chained discovery: the config-named tree's own root config resolved and winning, with two configs per run and loop and trust questions.
3. Explicit `--config` only.
4. By design (chosen): the tree is an operand, not a setting.

Two further constraints made a key costly: `sources:` is already a block (`method`, `public_headers`, `exclude`, `graph`), so the tree would need a leaf whose D1-derived flag (`--sources-root`) is not `--sources`; and a config naming the tree makes discovery circular, since the tree's own root config can only be found after the first config is read.
