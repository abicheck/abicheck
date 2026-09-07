### Added

- **Multi-source contract conflicts with provenance** (Workstream E slice
  S3) — `compare --contract` now records genuine disagreements between two
  evidence sources about the same entity, with both sides' claims kept
  rather than silently resolved to one (ADR-067 "record before
  disposing"): a symbol the binary exports but no public header declares
  (`exported_but_undeclared`), a `--post-manifest`/`--public-symbol(s)`
  overlay narrowing the committed exports below what a baseline declared
  public (`manifest_narrowed_since_baseline`), and — in the directory/
  package release fan-out — a package's own declared Debian `.symbols`
  contract (SONAME, symbol list) disagreeing with the binary actually
  contained in that same package (`package_claim_vs_binary`). Surfaced as
  `DiffResult.contract_conflicts` / the JSON report's new `contract_conflicts`
  array (schema 3.5) and a new Markdown "Contract Source Conflicts"
  section. Advisory only — never affects verdict, severity, or exit code.
  See `abicheck/contract_conflicts.py` and `abicheck/contract_conflicts_detect.py`.
