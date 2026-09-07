### Added

- **G41 Phase 2 (partial): per-target `build-output.json` header-roots
  projection.** `RunPlanCheck` (`abicheck/buildsource/run_plan.py`) gains
  `public_header_roots`/`generated_header_roots`, sourced per-target,
  per-profile from that profile's own validated `build-output.json` entry
  (`BuildOutputTarget.public_header_roots`/`.generated_header_roots`),
  newline-joined the same way the existing `header` field already is.
  Distinct from `header`: that field is `.abicheck.yml`'s *declared*,
  config-level `public_headers:` (the same value for every profile a
  target runs on); these two new fields are the *concrete*, per-profile
  set `build-output.json` itself declares, already validated to exist and
  be non-empty (ADR-047 §2's S10 guard) for that profile's real build —
  the first step of closing G41 Phase 2's "complete concrete per-target
  public/generated header roots... from validated build output" gap.
  Follows the `app-consumer`/`plugin-contract` `library:` redirect the
  same way `header` already does, and stays empty for `kind: bundle`
  cells (no per-bundle-member header staging exists yet). This slice adds
  the run-plan projection only — `check-project.yml`/`actions/check-target`
  forwarding these fields to the analysis step in place of the single
  workflow-global `header` input remains open (see
  `docs/contribute/plans/g41-baseline-consumer-context-and-declarative-
  assurance.md`'s Phase 2 and `docs/contribute/plans/
  product-gaps-2026-09-audit.md`'s "Remaining backlog").
