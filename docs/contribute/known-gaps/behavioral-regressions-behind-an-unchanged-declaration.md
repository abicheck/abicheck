---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Behavioral regressions behind an unchanged declaration

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** case208 ground truth records behavioral_break with truth_scope declared-interface and no detector expected. An L4 definition/declaration restrict replay is an unimplemented enhancement, not a regression.


Catalog case208 (restrict added to a function *definition* only) breaks a
valid overlapping consumer at runtime under GCC and Clang, while the
declared interface — everything a binary/header comparison reads — is
identical. Ground truth records it as `behavioral_break: true` with
`truth_scope: declared-interface`; no detector is expected to infer it.
An L4 source-ABI replay *could* see `restrict` on the definition's
parameters and report a definition/declaration contract mismatch; that is
not implemented.
