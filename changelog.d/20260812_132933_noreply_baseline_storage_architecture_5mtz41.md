<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`actions/stage-baseline/run.sh`** now removes a pre-existing
  `./$asset_name` before archiving, not just after — staging the new
  output externally only prevents self-inclusion of the file the *current*
  run is writing; it did nothing about a *different*, already-on-disk
  file left behind by an earlier invocation. When `baseline-path` is the
  working directory itself, that leftover archive was an ordinary member
  of `baseline-path` as far as `tar -C "$BASELINE_PATH" .` is concerned,
  so a repeated invocation would silently package the previous archive
  into the new one, nesting/growing further on each subsequent run.

### Documentation

- **`protect-committed-baseline.yml`** now documents a residual,
  structural gap the `.github/workflows/**` guard cannot close on its
  own: nothing inside a `workflow_call` reusable workflow can observe or
  prevent the *calling* workflow choosing not to invoke it at all (or
  replacing the invocation with an unrelated job trivially succeeding
  under the same required-check name) — GitHub's "required status check,
  matched by name" model has no notion of which workflow file actually
  produced a check. Closing this needs a mechanism the calling repository
  controls from a ref a PR cannot rewrite: GitHub Repository Rulesets'
  "Require workflows to pass before merging", which pins a required
  workflow to a specific file path *and* ref rather than a bare
  check-name match. Documented in both the workflow's own header comment
  and its reference doc as the recommended mitigation for a repository
  adopting this check.
