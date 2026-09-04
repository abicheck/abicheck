<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`analysis_assurance` no longer reports `complete`/`parsed` DWARF
  evidence it did not actually collect** — several review-round follow-ups
  to the analysis-completeness gate (`--require-complete-analysis`):
  `AdvancedDwarfMetadata`'s new provenance fields are now appended after
  every pre-existing field and marked keyword-only, so an external caller
  still constructing it positionally can no longer have its `target_arch`/
  `toolchain` silently bound to the wrong value; a split-DWARF
  (`-gsplit-dwarf`) skeleton compilation unit is now detected and both
  DWARF channels are reported `partial` rather than `parsed`, instead of
  claiming complete evidence for a `.dwo`/`.dwp` file that was never
  consumed; the two standalone (non-unified) `dwarf_metadata.
  parse_dwarf_metadata()`/`dwarf_advanced.parse_advanced_dwarf()` entry
  points now stamp `evidence_state`/`cu_total`/`cu_failed` the same way the
  unified single-pass parser does; a PDB advanced-DWARF-channel receipt is
  now always `partial` (never `parsed`) since the PDB producer never
  populates value-ABI traits, return classification, callee-saved/frame
  registers, or target architecture at all; a BTF/CTF extraction failure in
  any stage now sets `extraction_partial`, which downgrades the converted
  basic-layout receipt from `parsed` to `partial`; `compute_analysis_
  assurance` now also flags the symmetric "parsed `dwarf_advanced` with no
  basic `dwarf` channel on both sides" shape, the receipt-level complement
  of the pre-existing "parsed basic, no advanced" check; and `tests/
  check_stripped_fp.py`'s reduced-evidence false-negative guard now ties a
  `BREAKING`→clean downgrade exemption to the *specific* missing evidence
  channel a finding's kind actually depends on (dropping the incorrect
  `integer_model_changed` → advanced-DWARF mapping, since that detector
  reads header/L2 typedef facts, not DWARF) and refuses to waive a
  basic-DWARF-channel downgrade when header (L2) evidence for the same
  finding is still present and clean.
- **The `mutation.yml` PR lane's `require_baseline` decision is now
  computed per module from the real changed-path set**
  (`scripts/mutation_scope.py`'s new `require_baseline_for_pr()`) instead
  of from aggregate `mutated`/`mutated_tests` path-filter booleans, which
  could not distinguish "the module a changed test pairs with also
  changed" from "a *different* mutated module changed" or "only lane
  infrastructure changed" — either case previously waived the baseline
  requirement for an unguarded, `--diff-scoped`-invisible test-only
  weakening. The job timeout is now strictly above the internal `mutmut`
  subprocess timeout (`270` vs `240` minutes) so a long-but-legitimate run
  can actually finish (or its own timeout fire and be handled) before the
  outer job limit kills the whole job, including its result-export/
  artifact-upload steps.

### Documentation

- **`analysis_assurance.debug_evidence`** is now documented in
  `docs/use/output-formats.md`, and `report_schema_version`/
  `scan_schema_version` were bumped (`2.47`/`1.22`) to reflect the
  `debug_evidence` field this PR added to both report shapes.
