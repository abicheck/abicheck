---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Per-side `--debug-info` transports are not in the config tier

Index: [Known gaps](../known-gaps.md#option-vocabulary-adr-091-follow-ups)

**Status (2026-10-31): OPEN.** ADR-091 D4's soft budget kept `--debug-info` as a CLI flag: it is a per-side transport (`old=`/`new=`), and D4's amendment keeps per-side options as flags. Moving it to `.abicheck.yml` needs a per-side config home first (for example `debug_info.old` / `debug_info.new`), which is a schema decision, not part of the ADR-091 rename. Not a correctness gap: the flag works today.
