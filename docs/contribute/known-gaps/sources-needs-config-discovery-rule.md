---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `--sources` needs a config discovery rule before it can move

Index: [Known gaps](../known-gaps.md#option-vocabulary-adr-091-follow-ups)

**Status (2026-10-31): OPEN, decision pending.** `--sources` names a source tree. Giving it a config home needs a rule for which config a run uses when the config itself names the tree. That rule is a product decision with materially different options, so it was not made in the change that added the `debug.info`/`deps.*` homes.

What the code already fixes (`config_paths.resolve_project_config`): an explicit `--config` wins, then the `.abicheck.yml` at the `--sources` tree root, then the nearest config at or above the current directory. A tree named *in* a config is circular under that rule: its root config can only be found after the first config was read. Two further constraints:

- `sources:` is already a block (`method`, `public_headers`, `exclude`, `graph`). The tree needs a leaf (for example `sources.root`), and ADR-091 D1 derives `--sources-root` from that leaf, not `--sources`. Either a recorded D1 exception or a CLI rename follows.
- `compare --sources` is per-side. `options/rulings.py` records the tree as a per-run operand, and ADR-091 D4's amendment (a)/(b) keeps such options as flags.

Options:

1. **One config per run, no chaining.** `sources.root` (a list in the `[old=|new=]PATH` grammar `debug.info` uses) is honored from `--config` or the cwd-discovered config, and never triggers discovery of a second config inside the named tree. Relative paths resolve against the config file. When `--sources` is given, today's rule is unchanged. Precedence: `--sources` > `--set sources.root` > file.
2. **Chained discovery.** A config-named tree's own root `.abicheck.yml` is resolved and wins, matching today's `--sources` behaviour. The cost is two configs per run and a config that can redirect to another, with loop and trust questions (an auto-discovered config is passive-only).
3. **Explicit `--config` only.** Honor `sources.root` only from a trusted, explicit config.
4. **By design.** Close the gap: the tree is an operand, not a setting, and for the one stable case (`dump` run in its own checkout) cwd discovery already finds the config at the checkout root.

Not a correctness gap: the flag works today.
