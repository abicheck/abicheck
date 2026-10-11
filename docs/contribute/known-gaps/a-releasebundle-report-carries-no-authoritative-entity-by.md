---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A release/bundle report carries no authoritative entity-by-operation counts

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** report/pr_comment_aggregate.py:575-588 still marks the release rollup exact=False when targets cannot be itemized. The release JSON has an `uncapped` findings path (cli_compare_release_matrix.py:634), but no authoritative entity-by-operation count field was found; pr_comment._release_change_summary named in the entry no longer exists under that name.


`report/change_summary.py` can summarise a single comparison exactly,
because `compare`'s JSON carries the complete `changes` list. A release
(directory/package fan-out) report does not: the only itemized, kind-level
view of a library's findings is `cli_compare_release.py`'s `findings` list,
capped at ten per library. `pr_comment._release_change_summary` therefore
builds the rollup from that sample and marks it `exact=False` with a stated
reason, and the renderer prints "Not exact totals — …" rather than
presenting a floor as a total.

Closing it needs an authoritative per-entity/per-operation count in the
release JSON schema itself — a `cli_compare_release.py` change, not a
rendering-only one. It is the same shape of gap `pr_comment._from_release`'s
own docstring already records for the evidence-kind bucket, and it should be
closed in the same pass as that one.
