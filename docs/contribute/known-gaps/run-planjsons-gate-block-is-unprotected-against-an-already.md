---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `run-plan.json`'s `gate` block is unprotected against an already-shipped, version-skewed reader — investigated end-to-end, confirmed structurally infeasible to close within the document's own shape, not fixed (Codex review, PR #779)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** Records a structural infeasibility: run_plan.py:172 still uses the RUN_PLAN_SCHEMA_GATE discriminator, and old readers cannot be retrofitted.

`aggregate_manifest.py`'s `gate` block gained real
protection against an old `aggregate` binary misapplying a new manifest's
policy: `_check_manifest_version` already existed *before* this PR with
real MAJOR-rejection logic (`major > supported` raises), so bumping
`aggregate_manifest_version` from `1.0` to `2.0` gives an old, already-
installed reader a real, working rejection point — it already knows how to
say "too new for me." `run-plan.json`'s new `RUN_PLAN_SCHEMA_GATE`
(`abicheck.run-plan/v2`) discriminator was modeled on the identical
pattern but does **not** give the same protection, and cannot: traced the
full old (pre-PR779, commit `90e1813`, the last shipped state) `aggregate
--run-plan` pipeline directly. (1) Old `RunPlan.from_dict()`/
`RunPlanCheck.from_dict()` use exclusively total, defensive conversions
(`str()`, `bool()`, `isinstance`-guarded comprehensions, `.get()` with a
default for everything) — by design, matching this package's own
documented forward-compat convention ("every dataclass carries
`to_dict()`/`from_dict()` with defensive `.get()` parsing so a newer/
hand-edited pack never aborts a load" — `abicheck/buildsource/CLAUDE.md`).
Nothing in either function can raise regardless of input shape, so an
unrecognized `schema` string and an unknown `gate` key are both silently
ignored, not rejected. (2) Old `aggregate --run-plan` doesn't validate the
parsed plan directly at all — it projects it via `to_aggregate_manifest
(plan)`, which stamps `"aggregate_manifest_version": AGGREGATE_MANIFEST_
VERSION` using **the old binary's own hardcoded constant, imported fresh
at call time** — never read from or influenced by the run-plan.json's own
`schema` field. The one place a version-rejection check *does* fire
(`ExpectedTargets.from_manifest_data`) is therefore always checking the
old reader's own version against itself, and can never observe a
"too new" value regardless of what the source run-plan.json declares.
**Consequence:** a genuinely version-skewed setup — an already-installed
old `abicheck aggregate --run-plan` reading a `run-plan.json` a newer
`project plan --gate-missing-required`/`--gate-unexpected-target` wrote —
silently drops the requested gate policy and falls back to the old
binary's hard-coded `fail`/`include` defaults, exactly the "silently wrong
gate decision" class of bug the manifest-side `2.0` bump exists to
prevent, just unreachable to prevent here. **Not fixable within the
document's own shape**: every value in both dataclasses is consumed via a
total, defaulting conversion, so no crafted field value can force old,
already-shipped code to raise — this is a property of code that has
already been released and cannot be retroactively patched, not a
correctness gap in this PR's own new code. Closing it for real would need
a mechanism outside the JSON document itself (e.g. a CI-level convention
that `project plan` and `aggregate --run-plan` are always the same
abicheck version/pinned together, enforced or documented at the tooling
level) — out of scope for a schema-shape fix and not attempted here.
