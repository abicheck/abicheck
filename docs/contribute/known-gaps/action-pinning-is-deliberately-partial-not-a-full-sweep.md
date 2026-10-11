---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Action pinning is deliberately partial, not a full sweep

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** Still accurate. Elevated workflows (ci/publish/security/pages/agentready) and action.yml pin SHAs, while other workflows still use tags such as actions/checkout@v6 (grep of .github/workflows).

Third-party
GitHub Actions in `.github/workflows/agentready.yml`, `ci.yml` (the
`id-token: write` jobs), `pages.yml`, `publish.yml`, `security.yml`, and
`schedule-check-project-failure-path.yml` (its one `dispatch` job) are
pinned to a full commit SHA (with a `# <tag>` comment) rather than a
mutable tag/branch — those carry `security-events:write`,
`pull-requests:write`, `contents:write`, `actions:write`, or
`id-token:write` (OIDC/PyPI Trusted Publishing), so a re-pointed tag there
is a real supply-chain risk.
The root `action.yml` (the composite Action third-party repos consume
directly) is pinned the same way, for the same reason: its final step
conditionally runs `github/codeql-action/upload-sarif` under whatever
`security-events: write` permission the *consuming* workflow grants it, so
it carries the same blast radius as the elevated-permission workflows
above even though this repo's own CI doesn't invoke it with that scope.
Other workflows (`test-action.yml`, `eval-suite.yml`, `performance.yml`,
`realworld-validation.yml`, `dependency-review.yml`, and any future ones)
still use tags — deliberately deferred, since they only run with
`contents: read` and don't touch secrets/publishing/security-event write
access, so the blast radius of a compromised tag there is far smaller.
Extend the same pinning to a workflow only when it gains elevated
permissions, not preemptively.
