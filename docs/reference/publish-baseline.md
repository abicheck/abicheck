# `publish-baseline.yml` / `update-main-baseline.yml` Reference

These two reusable workflows produce the baseline-sets [`resolve-baseline`](resolve-baseline.md)
resolves against, one per contract profile: `publish-baseline.yml` writes an
immutable `release-contract` archive as a GitHub Release asset;
`update-main-baseline.yml` refreshes a mutable `accepted-main` entry in
GitHub Actions cache on every default-branch push. Both implement
[ADR-047](../contribute/adr/047-github-actions-integration-model.md)
§6/§10's baseline lifecycle.

> **Status.** Shipped in G30 P1.6. Neither workflow builds anything itself
> ("build once, scan many," the same boundary
> [`check-project.yml`](reusable-workflows.md) draws) — both expect the
> calling repository's own build job(s) to have already uploaded one
> `<build-output-artifact-prefix><profile-id>` [`build-output.json`](build-output-schema.md)
> artifact per contract profile earlier in the same workflow run.
> `actions/baseline` now stages bundle-member ELF binaries into a
> `binaries/` directory (the gap `resolve-baseline.md` previously flagged as
> "not yet") — see "Bundle members" below.

## Why two workflows, not one

The two channels have genuinely different write targets, trigger contexts,
and freshness semantics (§6): `release-contract` is immutable and writes to
a GitHub Release (`contents: write` on a release), while `accepted-main` is
continuously refreshed and writes to Actions cache (no push at all). Folding
both into one workflow would mean threading a channel-selector input through
every step instead of each file's steps being a direct, linear translation
of its own channel's contract.

## How each derives `actions/baseline`'s `libraries` input

Neither workflow re-reads the project's `.abicheck.yml`. Every library a
contract profile's build produced — and whether it is a release-bundle
member — is already recorded in that profile's own `build-output.json`
(`targets[].id`, `.binary`, `.public_header_roots`/`.generated_header_roots`,
`.bundle`). `abicheck build-output baseline-libraries DIRECTORY` (backed by
`abicheck.buildsource.baseline_publish.derive_baseline_libraries`) projects
that straight into `actions/baseline`'s `libraries` JSON array — one entry
per target, `stage_binary: true` set exactly for targets whose `bundle`
field is non-empty.

```bash
abicheck build-output baseline-libraries abicheck-build-linux-x86_64-gcc/
```

```json
{
  "ok": true,
  "entries": [
    {"name": "libpvxs", "artifact": "/…/artifacts/libpvxs.so", "stage_binary": true},
    {"name": "libutil", "artifact": "/…/artifacts/libutil.so"}
  ],
  "errors": []
}
```

Exit codes: `0` every target resolved; `1` one or more targets could not be
resolved (missing/escaping binary or header path — see `errors`); `64`
usage error (`DIRECTORY` is not a readable `build-output.json`).

## Bundle members: why `stage_binary` matters

`abicheck/bundle.py`'s `build_bundle_snapshot()` builds its cross-library
graph from real **ELF binaries** and explicitly skips non-ELF (including
`.abicheck.json` snapshot) inputs — a bundle baseline-set containing only
snapshots would silently produce no old-side bundle data. `actions/baseline`'s
`libraries[].stage_binary: true` (new in G30 P1.6) copies that library's
real binary into `<output-dir>/binaries/<name>` alongside its snapshot and
records `binary`/`binary_sha256` in `manifest.json`, closing the gap
[`resolve-baseline`'s bundle-scoped resolution](resolve-baseline.md#bundle-scoped-resolution-s14)
depends on.

**Depth scope, unchanged by this item.** A bundle-scoped check is still
restricted to `requested-depth: binary` (`abicheck/buildsource/
project_targets.py`'s `BUNDLE_CHECK_DEPTHS`) — this predates P1.6 and closes
[ADR-047 §8's "binaries only is not the full answer for every requested
depth"](../contribute/adr/047-github-actions-integration-model.md#8-condensed-scenario-catalog-s1s28)
open gap by construction: since a bundle check never requests header/build/
source depth in the first place, the archive never needs a per-member
`headers/` directory or a `compare-release` snapshot-consuming input path
either. If a future item lifts that restriction, staging old-side headers
per bundle member becomes this workflow's job to add, not a pre-existing gap
to rediscover.

## `publish-baseline.yml`

Reusable workflow (`workflow_call`); wire it to a `release: types:
[published]` trigger in your own repository — never `pull_request`/
`pull_request_target` ([ADR-047 §12](../contribute/adr/047-github-actions-integration-model.md#12-security-and-reproducibility)).

| Input | Default | Meaning |
|-------|---------|---------|
| `build-output-artifact-prefix` | `abicheck-build-` | Each contract profile's build-output.json is downloaded from `<this><profile-id>`. |
| `release-tag` | `github.ref_name` | Release tag to publish assets to; also recorded as each baseline-set's `project-ref`. |
| `asset-name-template` | `abicheck-baseline-{profile}.tar.zst` | Release asset filename; `{profile}` is replaced per profile. |
| `build-info` | `''` | Path to a shared build/source facts pack, relative to the downloaded build-output artifact. |
| `depth` | `''` | Evidence depth passed to every dump call. |
| `validation` | `strict` | Forwarded to `actions/baseline`'s `validation` input. |

Secret: `github-token` (optional) — falls back to the job's own
`GITHUB_TOKEN` (`permissions: contents: write` on the `publish` job).

For every discovered contract profile: downloads that profile's
build-output artifact, derives `libraries` from it, dumps the baseline-set
via `actions/baseline`, packages it as `<asset-name>` (`tar --zstd`), and
uploads it to `release-tag`'s release via `gh release upload --clobber` — a
re-run may overwrite this profile's own previously uploaded asset, scoped to
this one release, not a rewrite of history.

## `update-main-baseline.yml`

Reusable workflow (`workflow_call`); wire it to a `push: branches: [<default
branch>]` trigger.

| Input | Default | Meaning |
|-------|---------|---------|
| `build-output-artifact-prefix` | `abicheck-build-` | Same as above. |
| `key-prefix` | `abicheck-baseline-main` | Actions-cache key prefix (§10). |
| `head-sha` | `github.sha` | Recorded as `project-ref`; folded into the cache key. |
| `build-info` / `depth` / `validation` | same as above | |

### Cache key contract (read before wiring a consumer)

A GitHub Actions cache entry is immutable once written — a new version needs
a new key, not an overwrite. `update-main-baseline.yml` therefore writes a
**new key on every run**:

```text
<key-prefix>-<profile-id>-<head-sha>
```

A consumer restoring the latest entry (this workflow's own freshness step,
or a future caller wiring `accepted-main` into `check-project.yml`/
`check-single.yml`) must use `restore-keys: <key-prefix>-<profile-id>-` to
find it. A workflow that instead used one fixed key across refreshes would
silently keep resolving the *first* entry ever written, forever — the cache
action reports that as a hit, not an error, exactly the silent-shallow-
success failure mode this ADR exists to close.

`abicheck.buildsource.baseline_publish.accepted_main_cache_key(key_prefix,
profile_id, head_sha)` / `accepted_main_cache_restore_prefix(key_prefix,
profile_id)` are this format's pure-Python mirror.

Each run: computes this run's own key, restores the newest entry matching
the restore-keys prefix into a freshness-comparison staging directory, dumps
the new baseline-set with `--previous-manifest` pointed at that restored
`manifest.json` when one was found, then saves the fresh `.abicheck-baseline`
directory under this run's own key. Note `head-sha` is unique per *commit*,
not per *run*: a rerun/retrigger of the same commit, or an explicit
caller-supplied `head-sha` input, reuses the same key — the exact-key restore
can hit an entry that commit already wrote, falling through to
`restore-keys` only on an actual miss. The restore step is written to behave
correctly either way (a hit on this run's own previously written entry is
still the newest matching entry), so this doesn't affect correctness, only
the "always a miss" framing above.

### Known gap: nothing restores `accepted-main` from cache yet

`check-project.yml`'s baseline-set staging (P1.4) only ever downloads a
`<baseline-artifact-prefix><profile-id>-<channel>` **artifact** — it has no
built-in Actions-cache restore step. A project wiring `accepted-main` today
must add its own `actions/cache/restore` step (using the key contract above)
before calling `check-project.yml`, staging the restored directory at the
same `baseline-sets/<profile-id>-<channel>` path `check-project.yml` reads
from. Wiring cache-based staging directly into `check-project.yml` is
deferred, the same "defines the producer, a later item wires up direct
consumption" scoping [`build-output.json`](build-output-schema.md) and
[`resolve-baseline`](resolve-baseline.md)'s bundle path used before their
own producers shipped.

### Known gap: restoring immediately after a same-run write can miss

A GitHub Actions cache entry saved by one job is not always immediately
restorable via `actions/cache/restore` from a *different job within the
same workflow run* — observed directly while building this reusable
workflow's own live test fixture
(`.github/workflows/test-baseline-rotation.yml`): a restore attempt in a
downstream job missed an entry a sibling job had just finished saving
moments earlier, and `update-main-baseline.yml`'s own unmodified
"Restore previous accepted-main baseline-set" step (used for the
freshness comparison) failed identically when a second same-run
invocation tried to see the first's entry. This did not reproduce as a
simple propagation delay — a direct List Actions Caches API query
confirmed the entry already existed at the moment the restore missed it.
If you wire a custom `actions/cache/restore` step to consume
`accepted-main` immediately after calling `update-main-baseline.yml` in
the *same* workflow run (rather than in a later, separate run — the
normal case for a day-to-day `push`-triggered refresh), be aware this
same-run restore can transiently miss even though the entry is really
there. A later run, or a retried restore, resolves it.

## See also

- [`resolve-baseline` Action Reference](resolve-baseline.md) — consumes what
  these two workflows produce.
- [`check-target` Action Reference](check-target.md)
- [Reusable Workflows Reference](reusable-workflows.md) — `check-single.yml`/`check-project.yml`.
- [ADR-047 §6](../contribute/adr/047-github-actions-integration-model.md#6-baseline-lifecycle) — the baseline lifecycle this implements.
- [ADR-047 §10](../contribute/adr/047-github-actions-integration-model.md#10-baseline-storage-backends-compared) — storage backend comparison + the cache-key contract.
