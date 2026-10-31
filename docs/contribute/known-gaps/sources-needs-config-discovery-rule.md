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

**Status (2026-10-31): OPEN.** `--sources` names a source tree. Moving it into `.abicheck.yml` requires a rule for how a config is discovered relative to that tree (the same question `--sources` raises for its own `sources:` block). Until that rule is defined, the option stays a CLI flag. Not a correctness gap.
