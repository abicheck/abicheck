### Fixed

- **Persistent cross-source hygiene is no longer counted, or headlined, as
  this comparison's risk.** `compare()` already refused to charge a
  `persistent` hygiene finding — one whose problem is present identically on
  both sides — to the verdict, but every count a reader saw still folded it
  in: a byte-identical rebuild of a library carrying 32 pre-existing
  `exported_not_public` exports announced `NO_CHANGE: 32 risk (32 total)`, a
  verdict and a count contradicting each other in one line. The new
  `summary.change_inventory` block (report schema 5.3, `report/
  change_inventory.py`) names the five non-overlapping populations —
  `compatibility_changes` with its four verdict counters, plus the four
  `hygiene_*` evolution states — and the `--stat`/`-o oneline=` summary now
  counts only what the comparison observed, stating the standing inventory
  in its own `; hygiene: 32 persistent` clause. `breaking`/`source_breaks`/
  `risk_changes`/`compatible_additions`/`total_changes` keep their existing,
  inclusive meanings.
- **A run given no `--depth` no longer reports its assurance as unanswered.**
  `analysis_assurance.requested_depth` and `depth_satisfied` both read `null`
  beside `status: complete`, so an *unrequested* depth was indistinguishable
  from an *unknown* one. The implicit request is now normalized to the depth
  the run actually reached and labelled by the additive
  `requested_depth_source` (`"explicit"`/`"implicit"`, assurance schema 1.2);
  every gate still reads the explicit value, so no verdict, status, or exit
  code moves.
- **Reduced source-graph evidence is no longer described as a complete
  source-level analysis.** `analysis_assurance.effective_depth` reported
  `"source"` for any run carrying the always-on, header-only L5 declaration
  graph, while the same block's L4 row read `not_collected` — and while the
  CLI's own `--depth source` gate would have refused the run. The report now
  reuses the gate's rule (`evidence_depth.reported_depth_label`), so the two
  can no longer answer the same question differently.
- **An `--exclude-header` pattern that matched nothing is no longer reported
  as lost coverage.** The native path recorded the exclusion *request* on the
  snapshot, so a typo'd or obsolete pattern made the coverage warning tell the
  reader that "anything only they declared was not observed" about headers the
  run had parsed in full, and made the comparability gate refuse an otherwise
  identical operand. Only the achieved narrowing is recorded now
  (`extract.header_exclusions.matched_exclusion_patterns`) — the rule the
  descriptor `<skip_headers>` path has always used. Unmatched patterns keep
  their existing, separate configuration-hygiene warning.
