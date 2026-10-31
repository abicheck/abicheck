---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Some `deps.*` keys have no config home yet

Index: [Known gaps](../known-gaps.md#option-vocabulary-adr-091-follow-ups)

**Status (2026-10-31): OPEN.** Dependency settings reachable only from the CLI have no `.abicheck.yml` spelling, so they are missing from the option registry's config tier (`architecture/option-vocabulary.yaml`). Each needs a config key and a registry row before it can join the config tier. Not a correctness gap: the CLI spellings work today.
