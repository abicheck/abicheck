---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `DEFAULT_SYSTEM_PROVIDERS` (`bundle_models.py`) is a hand-maintained soname allow-list, not a real topology model for bundle-level system-provider classification -- a tactical fix that has grown, not a designed feature (Codex review on #791, fresh evidence)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** bundle_models.py:58 DEFAULT_SYSTEM_PROVIDERS is still a hand-maintained frozenset allow-list, a known tactical design limitation.


`bundle.compare_bundle()`'s unresolved-import check
(`ChangeKind.BUNDLE_INTRA_DEP_REMOVED`, `_detect_intra_dep_removed()`)
does not exempt an import merely for being "against any" soname in this
union -- it suppresses a consumer's finding only when that consumer's
remaining non-intra `DT_NEEDED` edges are all non-empty AND every one of
them matches `set(DEFAULT_SYSTEM_PROVIDERS) | set(bundle_system_providers)`
(or the `_looks_system` heuristic), AND EITHER no in-bundle sibling
version-compatibly provided the symbol and was reachable from *this*
consumer OR the user explicitly named one of the remaining sonames via
`explicit_providers` -- `ever_provided_in_bundle` is `compatible_old &
_old_reachable(consumer.library)`, scoped to what this specific consumer
could reach, not "any" in-bundle sibling regardless of reachability; an
unreachable sibling exporting the symbol does not, by itself, block the
allow-list suppression (Codex review, PR #910, fresh evidence, correcting
this exact paragraph's own prior "no in-bundle sibling ever" wording). A
consumer with a mixed intra-bundle/external dependency set can still
produce the finding, and a match against the allow-list alone is not
sufficient. Correct for the ordinary libc/libstdc++/libpthread
runtime set the
constant started from, but each addition since (oneTBB's `libtbb.so.*`
and its allocator-proxy libs, oneMKL/Intel-runtime and Level Zero
entries, PR #883) was a real, reported false positive fixed by naming one
more vendor runtime rather than by asking what "system-provided" actually
means for a bundle -- and a growing vendor runtime not yet on the list
still reproduces the exact false positive it exists to prevent. **Already
designed, not a fresh gap to sketch a fix for here**: `docs/contribute/
plans/g42-check-identity-environments-and-provider-resolution.md` names
this exact limitation as one of its own three motivating problems and
lays out the real fix ("Environment-aware system-provider resolution") --
resolving each external dependency edge against a declared environment's
sysroot/runtime matrix (presence, SONAME, export, symbol version, runtime
floor), with the static allowlist demoted to a fallback for a project
that doesn't opt into a declared environment. Route any work on this to
that plan rather than reinventing a provider-classes registry here.
**This gap is bundle-wide, not just `compare_bundle()`'s.** `audit_bundle()`
(`scan --artifact-set`'s one-sided entry point) unions the identical
`set(DEFAULT_SYSTEM_PROVIDERS) | set(bundle_system_providers)` allow-list
and feeds it to its own predicate, `_detect_unresolved_intra_dependency()`
-- not a call into `_detect_intra_dep_removed()`, since an audit has no old
side to diff against. That predicate's own docstring documents three real
differences from the diff-driven one (version-aware and reachability-
constrained provider matching, a suppression path that additionally
requires the consumer have zero intra-bundle `DT_NEEDED` edges, and a
`COMPATIBLE_WITH_RISK`-not-`BREAKING` verdict), so a G42 fix landing only
on the `compare_bundle()` path would leave `scan --artifact-set` audit-mode
classification on the static allow-list with its own, differently-shaped
false-positive exposure (Codex review, PR #910, fresh evidence). Any work
on this gap needs both call sites in scope.
