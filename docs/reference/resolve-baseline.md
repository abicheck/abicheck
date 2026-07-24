# `resolve-baseline` Action Reference

`actions/resolve-baseline` resolves one check's baseline — `channel × target
(or bundle) × profile` — against an already-staged baseline-set, returning
one of [ADR-047](../development/adr/047-github-actions-integration-model.md)
§6's typed outcomes. It never produces a compatibility verdict, and a missing
baseline is never silently treated as "compatible."

> **Status.** This page documents the `actions/resolve-baseline` composite
> Action shipped in G30 P1.2. `actions/check-target` (G30 P1.3), the
> primitive that composes this Action with the root `action.yml` and
> `collect-facts` into one check, is documented separately — see the
> [check-target Action reference](check-target.md). `actions/baseline`
> does not yet stage bundle-member ELF binaries into a `binaries/`
> directory (G30 P1.6) — bundle-scoped resolution (below) is defined and
> tested against a hand-authored fixture in the meantime, the same "defines
> the contract, no producer yet" scoping G30 P1.1 used for
> `build-output.json`.

## Why a separate primitive

Baseline resolution used to be inlined once, inside the root Action's
`abi-baseline` handling. Every one of `not_found`/`ambiguous`/`wrong_profile`
in [ADR-047](../development/adr/047-github-actions-integration-model.md)'s
scenario catalog is really a baseline-resolution failure — separating it out
lets a caller treat "baseline not found" as a distinct, typed condition
instead of falling through to whatever `compare`'s own missing-file error
text happens to be.

## What it does *not* do

`resolve-baseline` does not fetch anything from GitHub. Downloading a
`release-contract` archive from a GitHub Release, or restoring an
`accepted-main` entry from Actions cache, is the **calling workflow's** job
(see [ADR-047 §10](../development/adr/047-github-actions-integration-model.md#10-baseline-storage-backends-compared)'s
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

## Outputs

| Output | Meaning |
|--------|---------|
| `outcome` | `resolved` \| `not_found` \| `ambiguous` \| `wrong_profile` \| `stale_schema` \| `incompatible_evidence`. |
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
| `ambiguous` | `1` | `baseline-path` exists but has no `manifest.json` (e.g. an empty/partial cache restore — a different, more concerning failure than "nothing published yet"); or the manifest exists but this target isn't in it; or, for `kind: bundle`, one or more declared members have no staged binary in `binaries/`. |
| `wrong_profile` | `1` | The baseline set was built for a different `profile.id`. |
| `stale_schema` | `1` | `manifest.json`'s `manifest_version` is newer/older than this resolver understands. |
| `incompatible_evidence` | `1` | The baseline's recorded evidence producer (`wrapper`/`clang-plugin`/`replay`) disagrees with the candidate's, per `candidate-build-output`'s `evidence_producer` block — an infrastructure mismatch, not an ABI finding. |
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

## Example

```yaml
- name: Resolve accepted-main baseline for libpvxs
  id: baseline
  uses: abicheck/abicheck/actions/resolve-baseline@v1
  with:
    baseline-path: ./restored-baseline # staged by an earlier actions/cache step
    channel: accepted-main
    target: libpvxs
    profile: linux-x86_64-gcc13-release

- name: Compare against resolved baseline
  if: steps.baseline.outputs.outcome == 'resolved'
  uses: abicheck/abicheck@v1
  with:
    old-library: ${{ steps.baseline.outputs.snapshot-path }}
    new-library: build/lib/libpvxs.so
```
