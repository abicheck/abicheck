### Added

- **New live E2E fixture workflow** (`test-baseline-publish-e2e.yml`,
  `workflow_dispatch`-only): creates a real, disposable GitHub Release,
  publishes a baseline-set to it twice via the real `publish-baseline.yml`,
  and confirms the second (identical-content) publish is a genuine no-op
  retry rather than a duplicate or a failure — proving the real
  `gh release upload`/`gh api` authentication and response-shape path that a
  stubbed-`gh` unit test can't exercise. Cleans up its own release/tag
  unconditionally.
