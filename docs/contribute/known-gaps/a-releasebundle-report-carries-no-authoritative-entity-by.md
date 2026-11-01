---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: historical
generated: false
---

# A release/bundle report carries no authoritative entity-by-operation counts

Index: [Known gaps](../known-gaps.md) (closed; see the [archive](../archive/known-gaps-closed.md))

**Status (2026-10-31): CLOSED.** The release JSON carries `change_counts` (release schema 2.7) on every compared `libraries[]` entry and at the top level: `{"unit": "findings", "counted", "rows": [{"entity", "removed", "modified", "added"}]}`. A member's block counts its complete finding pool before any `show=` view, keeps only the findings the comparison introduced (`report/change_summary.is_this_changes_own`, the one predicate the scalar PR comment's rollup now imports too: `persistent`, `resolved` and `not_evaluated` hygiene findings are left out), and classifies each the way the scalar report's `entity`/`operation` fields are; the top-level block adds the complete bundle and matrix findings. `report/change_summary.release_change_summary` reads it as an exact rollup for the PR comment (and so for an `aggregate` fan-in of release documents); a document without a well-formed block (older than 2.7) is still summarized from its finding lists and marked `exact=False`. `pr_comment._release_change_summary` is gone. Tests: `tests/test_release_change_counts.py` (each member == its own scalar PR comment's rollup, over real persistent / not_evaluated / introduced hygiene findings, with no disposition, a suppression -- out of the counts, still in `disposition_audit` -- and an acknowledgment; every evolution stamp admitted alike by all three rollups; a `show=` view leaves the counts unchanged; malformed blocks are not trusted). The evidence-kind bucket `_from_release`'s docstring records is a separate gap and is not closed here.


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
