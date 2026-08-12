---
doc_type: reference
audience:
  - ci-owner
  - library-maintainer
level: intermediate
summarizes:
  - baseline-storage-backends
lifecycle: active
generated: false
---

# `protect-committed-baseline.yml` Reference

A `workflow_call` reusable workflow that closes a specific self-approval gap
in a *committed* baseline: an ordinary PR that both changes the compared
binary/headers **and** updates the baseline file it's compared against can
make an incompatible change look compatible, because the comparison never
reads the baseline as it stood at the PR's base commit — it reads whatever
the PR's own working tree has, which the same PR just edited.

## The gap this closes

```yaml
- name: ABI compatibility check
  uses: abicheck/abicheck@v0.5.0
  with:
    old-library: abi/libfoo.abicheck.json
    new-library: build/libfoo.so
```

`old-library: abi/libfoo.abicheck.json` resolves that path from whatever is
checked out — on a PR run, that's the PR's own head commit. If the PR also
updates `abi/libfoo.abicheck.json` to match the (possibly incompatible) new
binary, the comparison silently passes, because it never diffs against the
file's content at the PR's *base* commit.

Two independent fixes address this, and they compose:

1. **Read the baseline from the base commit, not the working tree** — see
   [Storing Baselines](../use/baseline-storage.md)'s recipe using
   `git show "${{ github.event.pull_request.base.sha }}:path"`. This is
   correct by construction: the baseline file the comparison reads can never
   be the one the PR itself just wrote.
2. **This workflow** — a defense-in-depth trusted gate for a project that
   hasn't (yet, or ever) adopted recipe 1: an ordinary PR that touches a
   configured baseline path fails outright, forcing a genuine refresh
   through a dedicated trusted (push-triggered, not PR-diff-triggered)
   workflow, or an explicit human-reviewed bypass label.

Use either independently, or both together (recipe 1 removes the risk
entirely; this workflow catches it even for a baseline path recipe 1
wasn't applied to, or a workflow this project doesn't control the source
of).

## Inputs

| Input | Required | Default | Meaning |
|-------|----------|---------|---------|
| `protected-paths` | yes | — | Newline-separated glob patterns naming committed baseline files, e.g. `abi/**` or `baselines/*/*.abicheck.json`. `**` matches zero or more path segments (crosses `/`); a lone `*` matches within one path segment only (never crosses `/`); `?` matches one character (never `/`). |
| `bypass-label` | no | `''` | A PR label that opts a specific PR out of this check (e.g. for an explicitly reviewed, human-approved manual baseline refresh). Empty (default) disables the bypass entirely — every PR touching a protected path fails unconditionally. |
| `base-sha` | no | `github.event.pull_request.base.sha` | Override for a non-`pull_request`-triggered caller. |
| `head-sha` | no | `github.event.pull_request.head.sha` | Override for a non-`pull_request`-triggered caller. |

No outputs — this workflow either passes (job succeeds) or fails (job
exits 1 with an `::error::` naming every protected file the PR touched).

## Fork safety

Runs entirely on an ordinary `pull_request` trigger: read-only
`contents: read`/`pull-requests: read` permissions, the default
`GITHUB_TOKEN`. **Never** wire this into a `pull_request_target` caller —
that would hand a fork PR's own workflow changes elevated permissions for
no benefit this check needs, the same rule
[ADR-047 §12](../contribute/adr/047-github-actions-integration-model.md)
already states for the baseline-publishing workflows.

## Example

```yaml
name: Protect committed ABI baseline

on:
  pull_request:
    paths:
      - 'abi/**'

permissions:
  contents: read
  pull-requests: read

jobs:
  protect-baseline:
    uses: abicheck/abicheck/.github/workflows/protect-committed-baseline.yml@<PINNED_COMMIT_OR_RELEASE>
    with:
      protected-paths: |
        abi/**
      bypass-label: baseline-refresh
```

A maintainer doing a genuine, reviewed baseline refresh in a normal PR adds
the `baseline-refresh` label before merging; every other PR touching
`abi/**` fails until the change is reverted or the baseline update is moved
to a dedicated trusted workflow.

## See also

- [Storing Baselines](../use/baseline-storage.md) — the base-commit-read
  recipe (fix 1 above), and the `abi-baseline`/baseline-set-archive fetch
  recipes this workflow complements.
- [Resolving Baselines](resolve-baseline.md) — the canonical reference for
  how a baseline (committed file, release asset, or baseline-set archive) is
  located and validated in the first place; this workflow only protects the
  committed-file case from silent self-approval once resolved.
