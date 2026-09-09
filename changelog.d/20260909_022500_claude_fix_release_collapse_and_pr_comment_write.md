<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`CompareRequest.collapse_versioned_symbols` never reached a directory/
  package comparison's per-library members** (Codex review). The field
  reached `CompareRequest`/`classify_compare_pair` for a single-pair
  `compare`, but `service_compare_pipeline.run_compare()` — the shim the
  release/package fan-out actually calls per member
  (`cli_compare_release_pairwise._run_compare_pair`) — had no parameter for
  it at all, so a `scope.collapse_versioned_symbols: true` project config
  silently had no effect on any package member: each kept the field at its
  `False` default and could report a version-renamed symbol as a
  spurious removal/addition where the identical scalar comparison
  collapsed it. Threaded the field through the full release-fan-out chain
  (`run_compare()` → `_run_compare_pair` → `_compare_one_library` →
  `_compare_release_libraries` → `compare_release_cmd` →
  `_dispatch_release_compare`), matching the exact shape this codebase
  already uses for `compile_context`/`depth`/`public_header_dirs`.
- **`action/run.sh`'s sticky PR-comment JSON acquisition silently failed
  for a `NOT_COMPARABLE` (or any other early-refusal) `compare` result**
  (Codex review, fresh evidence). `_maybe_post_pr_comment`'s fallback
  rerun (`_build_json_cmd`) stripped `--format`/`-o`/`--output`/
  `--output-file` from the primary run's command before appending its own
  `--format json -o "$PR_JSON"`, but never stripped a pre-existing
  `--write json="$PR_JSON"` (the primary run's own PR-comment sidecar
  injection) — and a `NOT_COMPARABLE` primary run aborts before ever
  reaching that `--write`, leaving `$PR_JSON` empty. The fallback rerun
  then handed the CLI the same path for both its primary (`-o`) and
  secondary (`--write`) output, which the CLI hard-rejects
  (`--write's PATH must differ from --output/-o`, verified live) — the
  rerun always failed and the Action silently skipped the sticky comment
  with a misleading "no JSON report produced" warning. `_build_json_cmd`
  now also strips a pre-existing `--write` (and its value) before
  appending its own `-o`.
