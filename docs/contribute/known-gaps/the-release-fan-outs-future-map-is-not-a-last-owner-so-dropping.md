---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# The release fan-out's future map is not a last owner, so dropping it saves nothing (2026-09-17)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): NEGATIVE_RESULT.** A negative result: dropping futures saves nothing because results_by_key holds the same objects. abicheck/cli_compare_release_pairwise.py still exists.


Checked while looking for lifetime wins after the encoder/archive work above,
and recorded as a negative result so it is not "fixed" speculatively later.

`cli_compare_release_pairwise.py`'s parallel fan-out builds
`futures = {executor.submit(...): key for key in matched_keys}` and keeps the
whole mapping alive until the `with ThreadPoolExecutor(...)` block exits. That
looks like per-member results being retained for the length of the run, and it
is a real retention — but not of the results. `results_by_key[key] =
future.result()` stores *the same object* the completed `Future` holds in its
own `_result` slot, so the dict and the future are two references to one
result. Releasing the future frees the `Future` wrapper and nothing else; the
per-library result dict stays owned by `results_by_key`, which the function
returns.

So the available saving here is a few hundred bytes per member, not a member's
worth of findings, and any patch that deletes futures as they complete while
claiming a memory reduction would be measuring its own wrapper objects. A real
reduction on this path has to shrink or stream what `results_by_key` holds —
the per-library result payloads themselves — which is a change to what the
release fan-out returns to its caller, not a lifetime tweak inside the loop.
