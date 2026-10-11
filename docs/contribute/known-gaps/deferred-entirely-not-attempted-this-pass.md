---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Deferred entirely, not attempted this pass

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** Deferred-scope note. .devcontainer/ still does not exist (ls fails).

(heavier structural
changes, each needing its own scoped design rather than a drive-by
addition):
- *Devcontainer image* — a maintained `.devcontainer/` needs a decision on
  which system tools (castxml, libabigail, abi-compliance-checker,
  compilers) ship baked-in vs. installed on first use, and upkeep as those
  pins drift; `pixi` (see CONTRIBUTING.md) already solves the "one command
  gets you a working dev environment" problem this would target, without
  the image-maintenance burden.
- *Trend-reporting database* — persisting `scripts/check_tier_accuracy.py`
  /`check_fp_rate.py`/mutation-score history across runs (rather than each
  CI run only gating against a static baseline) needs a storage decision
  (artifact-based vs. external DB) and a retention/access policy before
  it's worth building.
- *Full behavioral baseline* — `skills-src/evaluation/agents/` (this pass, M1-5) is a real
  but minimal harness with one task; a "full behavioral baseline" implies
  a broad task suite plus a scoring/leaderboard story, which should grow
  from real usage of the one-task harness rather than being speculatively
  built out now.
