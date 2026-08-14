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

# `resolve-baseline` Action Reference

`actions/resolve-baseline` resolves one check's baseline — `channel × target
(or bundle) × profile` — against an already-staged baseline-set, returning
one of [ADR-047](../contribute/adr/047-github-actions-integration-model.md)
§6's typed outcomes. It never produces a compatibility verdict, and a missing
baseline is never silently treated as "compatible."

> **Status.** This page documents the `actions/resolve-baseline` composite
> Action shipped in G30 P1.2. `actions/check-target` (G30 P1.3), the
> primitive that composes this Action with the root `action.yml` and
> `collect-facts` into one check, is documented separately — see the
> [check-target Action reference](check-target.md). `actions/baseline`
> now stages bundle-member ELF binaries into a `binaries/` directory (G30
> P1.6) — see the [`publish-baseline`/`update-main-baseline` reference](publish-baseline.md)
> for the two reusable workflows that produce a baseline-set this Action
> resolves against.

## Why a separate primitive

Baseline resolution used to be inlined once, inside the root Action's
`abi-baseline` handling. Every one of `not_found`/`ambiguous`/`wrong_profile`
in [ADR-047](../contribute/adr/047-github-actions-integration-model.md)'s
scenario catalog is really a baseline-resolution failure — separating it out
lets a caller treat "baseline not found" as a distinct, typed condition
instead of falling through to whatever `compare`'s own missing-file error
text happens to be.

## What it does *not* do

`resolve-baseline` does not fetch anything from GitHub. Downloading a
`release-contract` archive from a GitHub Release, or restoring an
`accepted-main` entry from Actions cache, is the **calling workflow's** job
(see [ADR-047 §10](../contribute/adr/047-github-actions-integration-model.md#10-baseline-storage-backends-compared)'s
storage-backend table) — `actions/cache`, `actions/download-artifact`, or
`gh release download`. `resolve-baseline` only resolves *within* whatever
`baseline-path` the caller already staged.

## Inputs

| Input | Required | Default | Meaning |
|-------|----------|---------|---------|
| `baseline-path` | yes | — | A directory already containing `manifest.json` (+ per-target snapshots, and `binaries/` for a bundle), **or** a `.tar.zst`/`.tar.gz`/`.tgz`/`.tar` archive of the same, extracted automatically. A path that doesn't exist at all is `not_found`, not a usage error. A path that **does** exist (directory or extracted archive) but has no `manifest.json` inside it — e.g. an empty/partial `actions/cache` restore — is `ambiguous`, not `not_found`: it never bootstraps a `required: false` caller to a green run. |
| `channel` | yes | — | `release-contract` \| `accepted-main` \| `explicit` \| a project-defined custom channel. Recorded on the output only — this Action trusts the caller already staged the right baseline-path for this channel. |
| `kind` | no | `target` | `target` or `bundle`. |
| `target` | when `kind: target` | — | Target id to resolve. |
| `bundle` | when `kind: bundle` | — | Bundle id to resolve. |
| `bundle-members` | when `kind: bundle` | `[]` | JSON array of the bundle's member target ids, e.g. `["libpvxs", "libpvxsIoc"]`. |
| `profile` | yes | — | The build `profile.id` this check expects the baseline to have been built for. |
| `required` | no | `true` | `true` — no baseline set yet is a hard failure. `false` — explicit bootstrap opt-in (e.g. the very first `release-contract` publish); no baseline set yet resolves as an advisory `not_found`/bootstrap pass. |
| `candidate-build-output` | no | `''` | Path to the candidate build's `build-output.json`, read only for its `evidence_producer` block, feeding the `incompatible_evidence` check. Omit to skip that check. |
| `expected-project-ref` | no | `''` | Require the resolved baseline-set's `manifest.json` `project_ref` to match this value exactly, or `wrong_project_ref` is returned instead of `resolved`. Pass e.g. `github.event.pull_request.base.sha` when staging `accepted-main` for a PR gate — see [Known gap: `accepted-main` restore-by-prefix](#known-gap-accepted-main-restore-by-prefix-can-resolve-the-wrong-commit) below. Omit to skip this check (appropriate for `release-contract`, resolved by tag/asset selection rather than a Git ref). |
| `expected-baseline-generation` | no | `''` | Require the resolved baseline-set's `manifest.json` `baseline_generation` (a caller-assigned scanner-compatibility identity, `actions/baseline`'s own `baseline-generation` input — see [baseline-management.md](../use/baseline-management.md#scanner-upgrades-and-baseline-generations)) to match this value exactly, or `stale_generation` is returned instead of `resolved`. Unlike `expected-project-ref`, this check is not scoped to any one channel — a `release-contract` baseline can be just as stale a generation as an `accepted-main` one. Omit to skip this check. |
| `allow-new-target` | no | `'false'` | `'false'` — a target absent from an otherwise-resolved baseline-set always fails `ambiguous` (almost always a staging mistake: wrong channel, wrong `baseline-path`, a typo'd target id). `'true'` — opt in to the `new_target` outcome instead, an advisory, non-fatal lifecycle state for a target this check has explicitly designed to tolerate the first appearance of (e.g. a new library's first release, checked against a baseline-set that predates it). Only meaningful for `kind: target` — ignored for `kind: bundle`, which never supports `new_target` (see [Bundle-scoped resolution](#bundle-scoped-resolution-s14) below). |

## Outputs

| Output | Meaning |
|--------|---------|
| `outcome` | `resolved` \| `not_found` \| `ambiguous` \| `wrong_profile` \| `stale_schema` \| `incompatible_evidence` \| `wrong_project_ref` \| `stale_generation` \| `new_target`. |
| `bootstrap` | `'true'` only when `outcome: not_found` and `required: 'false'`. |
| `channel` | Echoes the `channel` input. |
| `manifest-path` | Path to the resolved baseline-set's `manifest.json`, when one was found. |
| `snapshot-path` | (`kind: target` only) Path to the resolved target's `.abicheck.json` snapshot. |
| `binaries-dir` | (`kind: bundle` only) Path to the directory containing the resolved bundle's staged member binaries. |
| `binary-paths` | (`kind: bundle` only) JSON object mapping each member target id to its staged binary path. |
| `message` | Human-readable explanation of the outcome. |

## Failure taxonomy (ADR-047 §6)

All fail-loud — none of these ever silently degrade to a compatibility
verdict. Only `not_found` has a bootstrap carve-out, and only when the
caller explicitly opts in with `required: false`:

| `outcome` | Job exit | When |
|-----------|----------|------|
| `not_found` (bootstrap) | `0` | `baseline-path` itself does not exist, and `required: false`. |
| `not_found` (required) | `1` | `baseline-path` itself does not exist, and `required: true` (default) — a typo in the channel name, a missing release asset, or a cache-resolution bug must never produce a green branch-protection status with zero comparison performed. |
| `ambiguous` | `1` | `baseline-path` exists but has no `manifest.json` (e.g. an empty/partial cache restore — a different, more concerning failure than "nothing published yet"); or the manifest exists but this target isn't in it (and `allow-new-target` was not set to `'true'`); or, for `kind: bundle`, one or more declared members have no staged binary in `binaries/`. |
| `wrong_profile` | `1` | The baseline set was built for a different `profile.id`. |
| `stale_schema` | `1` | `manifest.json`'s `manifest_version` is newer/older than this resolver understands. |
| `incompatible_evidence` | `1` | The baseline's recorded evidence producer (`wrapper`/`clang-plugin`/`replay`) disagrees with the candidate's, per `candidate-build-output`'s `evidence_producer` block — an infrastructure mismatch, not an ABI finding. |
| `wrong_project_ref` | `1` | `expected-project-ref` was given and the baseline-set's `manifest.json` `project_ref` doesn't match it exactly — the staged `baseline-path` resolved to a baseline-set built from the wrong commit/tag. |
| `stale_generation` | `1` | `expected-baseline-generation` was given and the baseline-set's `manifest.json` `baseline_generation` doesn't match it exactly — this baseline-set was produced by a different scanner-compatibility generation, even though its `snapshot_schema`/`profile`/`project_ref` may be unchanged. |
| `new_target` (`kind: target` only) | `0` | `allow-new-target: 'true'` was given, the baseline-set itself resolved cleanly (schema/profile/`project_ref`/generation all matched), and this target simply has no `artifacts[]` entry in it — an advisory, non-fatal "target new to this baseline-set" pass, distinct from `ambiguous`'s otherwise-identical condition. Never returned for `kind: bundle`. |
| `resolved` | `0` | Success. |

## Bundle-scoped resolution (S14)

A bundle's resolution unit is not one snapshot. `abicheck/bundle.py`'s
`build_bundle_snapshot()` builds its cross-library graph from real **ELF
binaries** and explicitly skips non-ELF (including JSON snapshot) inputs, so
`kind: bundle` resolves to the set of every member's **staged binary** under
the baseline-set's `binaries/` directory instead of a snapshot path. Every
member named in `bundle-members` must have one, or the whole resolution
reports `ambiguous` — a partially-staged bundle baseline would otherwise
silently produce a bundle report missing one member's old-side data.

## Known limitation: `wrapper`/`replay` producer aliasing

The `incompatible_evidence` check (see the outcome table above) compares
each side's recorded evidence producer string. Both the `abicheck-cc`
wrapper and the source-replay (L4) path populate `evidence_producer.tool`
with the same underlying string, `abicheck-cc-clang-extractor` — there is
currently no way to tell "the wrapper captured this" apart from "source
replay reconstructed this after the fact" from the recorded producer alone.
A baseline staged via one path and a candidate produced via the other will
**not** be flagged `incompatible_evidence`, even though their evidence has
different fidelity characteristics. Only a genuinely different tool (e.g.
the Clang facts plugin vs. either of the above) is caught. Tightening this
would need a distinct producer string per path, which is deferred rather
than done in this PR.

## Known gap: `accepted-main` restore-by-prefix can resolve the wrong commit

`update-main-baseline.yml` writes one Actions-cache entry per default-branch
commit (`<key-prefix>-<profile-id>-<head-sha>`, immutable once written) —
see the [publish-baseline reference](publish-baseline.md)'s cache-key
contract. A caller staging `accepted-main` for its own freshness comparison
(or for a PR gate) that restores by **prefix** rather than an exact key
(`restore-keys: <key-prefix>-<profile-id>-`) gets whatever entry is
*newest* under that prefix, regardless of which commit wrote it — GitHub's
own cache-restore semantics, not a bug in this Action. For a PR gate that is
the wrong question: a PR should compare against the baseline for its own
**base commit**, not whatever `main` has most recently advanced to (a
default-branch commit that landed *after* the PR branched can otherwise
silently become the baseline, comparing the PR against code its own history
never actually contained).

`resolve-baseline` cannot detect this by itself — a wrong-commit baseline-set
still has a well-formed manifest, the right profile, and valid digests. Use
`expected-project-ref` to make it detectable: pass the PR's exact base SHA,
and a restore-keys prefix match that landed on any other commit reports
`wrong_project_ref` instead of silently resolving.

```yaml
- name: Restore accepted-main baseline (exact key only — no restore-keys)
  id: restore
  uses: actions/cache/restore@v4
  with:
    path: .baseline-staged
    key: abicheck-baseline-main-${{ inputs.profile }}-${{ github.event.pull_request.base.sha }}

- name: Resolve accepted-main baseline for libpvxs
  id: baseline
  # not yet in a tagged release as of this writing -- pin a commit that
  # actually includes the expected-project-ref input, not just "main or
  # newer" generically.
  uses: abicheck/abicheck/actions/resolve-baseline@f1471d8307cfb1ee085f615f0694350bf3c116d7
  with:
    baseline-path: .baseline-staged
    channel: accepted-main
    target: libpvxs
    profile: ${{ inputs.profile }}
    expected-project-ref: ${{ github.event.pull_request.base.sha }}
```

If the exact-key restore misses (no `accepted-main` baseline was ever
published for that exact base SHA), the safer failure mode for a PR gate is
to fail closed or fall back to a different channel entirely (e.g. a
committed baseline read via `git show base_sha:path`, or `release-contract`)
— not to fall back to a prefix-matched, possibly-wrong-commit cache entry.

## Example

```yaml
- name: Resolve accepted-main baseline for libpvxs
  id: baseline
  uses: abicheck/abicheck/actions/resolve-baseline@f1471d8307cfb1ee085f615f0694350bf3c116d7  # not yet in a tagged release; pin main or newer
  with:
    baseline-path: ./restored-baseline # staged by an earlier actions/cache step
    channel: accepted-main
    target: libpvxs
    profile: linux-x86_64-gcc13-release

- name: Compare against resolved baseline
  if: steps.baseline.outputs.outcome == 'resolved'
  uses: abicheck/abicheck@v0.5.0
  with:
    old-library: ${{ steps.baseline.outputs.snapshot-path }}
    new-library: build/lib/libpvxs.so
```

## See also

- [Storing Baselines](../use/baseline-storage.md) — the narrative guide to
  each storage backend (GitHub Releases, Git-committed files, Actions
  Cache, an external artifact store) this Action resolves *from*; this page
  covers only the resolution Action's own input/output/outcome contract.
