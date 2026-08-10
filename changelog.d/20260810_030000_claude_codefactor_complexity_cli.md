<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **All seven CLI entry points were restructured to cut cyclomatic
  complexity**, with no behaviour, output, or exit-code changes:
  - `cli.dump_cmd` — the `--dump-manifest` load (with the flags it is
    mutually exclusive with) and the debug-format resolution plus its two
    usage-error checks each became their own helper.
  - `cli_scan.scan_cmd` — the operand/flag mutual-exclusion checks, the project
    config discovery (with the digest that parsed it), and the ADR-049
    evaluation-config resolution each became their own helper.
  - `cli_compare_helpers.run_compare` — the heaviest function on the board — is
    now an explicit three-phase pipeline (resolve → compare → report). The
    post-comparison half moved wholesale into `_report_compare_result`, and
    eight resolution steps each became their own helper
    (`_resolve_required_symbol_policy`, `_reject_manifest_header_conflicts`,
    `_reject_manifest_non_elf`, `_reject_flags_unsupported_for_set_inputs`,
    `_embed_inline_source_sides`, `_resolve_evaluation_config`,
    `_apply_scoped_gating`, `_attach_suppression_audit`).
  - The primary (`--format`) and secondary (`--secondary-format`) reports ran
    two byte-identical copies of the same four-step render/fold pipeline; they
    now share one `_render_compare_report`, so the two cannot drift. The
    secondary keeps its deliberate differences — always the full unfiltered
    report, and `demangle` resolved against its own format.
  - `cli_compare_helpers._apply_used_by_scoping` — the OLD/NEW binary-evidence
    precondition and the ADR-044 runtime-probe overlay each became a helper.
  - `cli_scan_baseline._run_baseline_compare` — the old-side header-scope
    resolution, the summary block, and the ADR-049 contract block each became a
    helper.
  - `cli_compare_fold._ScopedFold.into_text` — one method per report section,
    assembled by a single join.
  - `cli_dump_helpers.render_dump_dry_run` — the manifest section, the
    data-layer probe, and the depth-feasibility chain each became a helper.
- Relocated the ADR-043 scoped-gating family (`_apply_used_by_scoping`,
  `_apply_required_symbol_scoping`, the runtime-probe overlay, the worst-wins
  exit-code/verdict ranking and the JSON-safe summaries) from
  `cli_compare_helpers.py` to `cli_helpers_compare.py`. A pure relocation
  behind a re-export shim, needed because `cli_compare_helpers` sits at the
  AI-readiness 2000-line hard cap. It lands in that existing module rather
  than a new one deliberately: the family reaches `service`/`appcompat`
  (through function-local imports, which the cycle check also counts), so a
  *new* module would join the allowlisted CLI import-cycle SCC — the growth
  CLAUDE.md "M1-3" forbids — while `cli_helpers_compare` is already a member.
  `cli_compare_helpers._verdict_exit_code` (which `cli_scan_baseline` imports)
  and the existing test patch targets keep resolving unchanged.
- Relocated the snapshot write path (`_write_snapshot_output`,
  `_missing_requested_evidence_layers`, `_classify_missing_layers`,
  `_layer_payload_empty`) from `cli.py` to `cli_buildsource.py`. `cli.py` was
  within a handful of lines of the 2000-line hard cap, and this cluster is
  cohesive with its new home: writing a snapshot *is* the step that folds
  `cli_buildsource`'s own `embed_build_source`/`embed_inputs_pack` payloads in
  and then enforces the requested evidence depth, and that module already
  imported `_write_snapshot_output` back out of `cli`. `cli` re-exports all
  four names through a lazy module-level `__getattr__` shim (AGENTS.md's
  required pattern for a relocated helper kept at its old path, modelled on
  the one at the tail of `cli_buildsource.py`), so `cli` never grows a
  top-level import of a module that reaches back into it. `dump_cmd`'s own
  two bare-name uses take a function-local import instead — PEP 562's module
  `__getattr__` serves attribute access on the module, never a global lookup
  inside it. The two evidence-layer helpers are additionally called back
  *through* the `cli` module rather than by bare name, so the existing
  `monkeypatch.setattr(cli, "_missing_requested_evidence_layers", ...)` targets
  keep taking effect — the same resolution the neighbouring
  `cli._normalize_binary_input` calls already use.
