### Changed

- **Behaviour change: a compatibility claim with no type-level evidence now
  reads `UNCONFIRMED` and exits `1`** (ADR-090, Accepted). A `compare` whose
  verdict would be `NO_CHANGE`, `COMPATIBLE` or `COMPATIBLE_WITH_RISK` while a
  compared side has neither debug info (DWARF, BTF, CTF, PDB) nor a header AST,
  such as a stripped pair compared without headers, used to report a clean
  result with exit `0` and `Gate: ACCEPTED`. The same pair unstripped reported
  `BREAKING`. Such a run now reads `UNCONFIRMED` in the JSON `verdict` and
  `run_outcome.compatibility` (report schema 7.4, with a new `unconfirmed`
  block keeping the observed verdict), in the terminal, Markdown and HTML
  headlines, in SARIF (a `warning` notification, with the axis folded into
  `exitCode`) and in JUnit (a failing suite). It also gets
  `release_recommendation.version_bump: "undetermined"` and the new
  `exit.unconfirmed_contribution` axis, folded with `max` so an observed
  `2`/`4` never moves. Directory/package members, stored `BundleFacts`
  members and `abicheck aggregate` (schema 1.12, per-target `unconfirmed_exit`)
  fold it the same way. The GitHub Action publishes `verdict: UNCONFIRMED`.
  The same input compared with itself stays `NO_CHANGE`. To confirm
  compatibility, supply headers for both sides or compare unstripped debug
  builds. When a stored PE/Mach-O snapshot's headers were supplied but
  dropped by an older header-scoping fallback (no header declaration matched an
  exported symbol), the report says so (`unconfirmed.header_scope_fallback`)
  instead of asking for headers again. To keep exit `0` meanwhile, set `assurance.unconfirmed: warn` in
  `.abicheck.yml`, pass `--set assurance.unconfirmed=warn`, or use the typed
  `CompareRequest.unconfirmed`; the label stays. A consumer that matches
  `verdict == "NO_CHANGE"`, or that enumerates verdicts, must handle
  `UNCONFIRMED`. See `docs/reference/exit-codes.md` ("Unconfirmed
  compatibility").
