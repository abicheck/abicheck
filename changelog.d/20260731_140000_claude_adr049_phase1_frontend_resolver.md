### Added

- **ADR-049 Phase 1: one resolved `CompatibilityEvaluationConfig` per run, from
  real front-end input.** `abicheck/compatibility_evaluation_frontend.py` collects
  every contract/surface/assurance/policy/gate/suppression setting a front end can
  state today — `compare`'s own CLI kwargs (`compare_cli_inputs`), a typed
  `CompareRequest` (`compare_request_inputs`/`compatibility_config_from_compare_request`),
  and the project's `.abicheck.yml` (`ProjectCompatibilityInputs.from_build_config`)
  — resolves each field independently through the D7 precedence resolver, and
  returns the typed object plus one provenance receipt entry per field.
  `cross_front_end_differences()` makes Phase 1's own gate executable: a CLI run
  and the equivalent API request must resolve to an equal configuration and
  receipt, modulo only which front end stated a value. Selected `kind: contract`
  and `kind: gate` packs now compose into their typed target fields through an
  explicit per-field route table
  (`compatibility_evaluation_wiring.resolve_pack_field_assignments`), with an
  assignment outside a pack's namespace — including any attempt to set
  `contract.mode` — a hard load error. Resolution only: no command consumes the
  object, so no verdict, finding, or exit code changes. New reference page:
  `docs/reference/compatibility-evaluation-config.md`.
- **`SuppressionList.rule_identities()`** — a canonical, machine-facing identity
  per loaded suppression rule, for the ADR-049 configuration receipt (distinct
  from the human-facing audit label, which prefers a rule's `label`/`reason`).
