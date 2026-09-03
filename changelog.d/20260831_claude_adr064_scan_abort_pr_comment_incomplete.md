### Fixed

- **A `scan` abort's sticky PR comment rendered "No ABI changes" instead of
  a blocking "analysis incomplete" finding** (PR review finding). The
  native CLI's `scan --format json` abort envelope
  (`cli_scan._emit_scan_abort_report`) shapes `report["diff"]` as
  `{"exit": {...}}`, with no `findings`/`additions`/`quality`/`reason` key
  at all -- `pr_comment_scan.from_scan` read those empty buckets as an
  ordinary, clean, zero-findings comparison, since it only recognized the
  `NOT_COMPARABLE` shape (`{"reason": ...}`) as needing special handling.
  Reachable for `EVIDENCE_CONTRACT_ERROR` through the GitHub Action, and for
  either abort sentinel through `cli_pr_comment` directly; under the
  default `--on=changes` this could even delete a prior sticky failure
  comment because the model reported zero changes. `from_scan` now
  recognizes the abort envelope the same way it already recognizes
  `NOT_COMPARABLE`, via a new `abicheck.pr_comment_scan_abort.
  scan_abort_incomplete_reason` helper, and renders the same single
  blocking "analysis incomplete" finding. A promoted cross-check finding
  also stays in the review bucket rather than breaking under
  `--gate-api-break`, matching `NOT_COMPARABLE`'s own treatment (an
  aborted scan's cross-check evidence never actually gated its real exit
  code).
