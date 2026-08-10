<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`abicheck project validate-use-cases`** (G29 Phase 4, ADR-057
  amendment): the declared business/runtime use-case manifest
  (`impact-use-cases.yaml`) and its `resolve_use_case_entrypoints` join had a
  fully-tested library API but no CLI front door — a manifest author had no
  way to find out a declared entrypoint failed to resolve short of writing
  their own Python calling the module directly. The new subcommand checks
  the manifest's own structure on its own (empty/malformed/unknown-field
  documents are a clean usage error, exit 64), and, given `--against
  <snapshot>` carrying an embedded L5 source graph, reports which declared
  entrypoints resolved against it and which didn't, per use case, in text or
  `--format json`. An unresolved entrypoint is never a command failure —
  per the manifest format's own "absence is never evidence of a wrong
  answer" discipline — only a malformed manifest or a graph-less/unreadable
  `--against` snapshot is. New library function
  `resolve_use_case_entrypoints()` in `abicheck/impact/use_cases.py` reuses
  the same private resolution `build_use_case_graph()` already performs
  internally, so the report can never disagree with what the graph itself
  records.
