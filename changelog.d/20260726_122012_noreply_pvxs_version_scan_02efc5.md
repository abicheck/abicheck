### Documentation

- **GitHub Action: clearer `header` input warning, and pin-to-SHA guidance
  tied to elevated permissions** — `action.yml`'s `header` input now warns
  explicitly that it applies to *both* sides, pointing at `old-header`/
  `new-header` when they actually differ (a real point of confusion writing
  a recipe for a project whose new release added a header). The
  [Versioning](use/github-action.md#versioning) section and the SARIF/Code
  Scanning recipes now call out pinning every `uses:` step to a full commit
  SHA — not just `abicheck/abicheck` — whenever the job grants
  `security-events: write` or another elevated permission, a gap found
  while writing a real recipe for a `security-events: write` job.
