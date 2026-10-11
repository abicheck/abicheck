---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# CODEOWNERS risk tiers currently all resolve to one person

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** .github/CODEOWNERS assigns all 19 rules to @napetrov.

The file is
structured by risk tier (CRITICAL/HIGH/STANDARD) so a second maintainer
can be slotted into CRITICAL/HIGH without restructuring, but there is
only one maintainer today — don't read the tiering as "these are reviewed
by different people," it isn't, yet.
