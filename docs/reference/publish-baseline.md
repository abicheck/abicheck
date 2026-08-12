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
`.bundle`). `abicheck.buildsource.baseline_publish.derive_baseline_libraries`
projects that straight into `actions/baseline`'s `libraries` JSON array — one
entry per target, `stage_binary: true` set exactly for targets whose
`bundle` field is non-empty.

> **Not a CLI command.** This used to be `abicheck build-output
> baseline-libraries DIRECTORY`; [ADR-054](../contribute/adr/054-cli-project-integration-surface-consolidation.md)
> removed it from the public CLI — it was a wire-format adapter for exactly
> these two workflows' `actions/baseline` input, not a general-purpose
> operation. Both workflows now call the function directly:

```bash
python3 -c "
import json
from pathlib import Path
from abicheck.buildsource.baseline_publish import derive_baseline_libraries
from abicheck.buildsource.build_output import load_build_output

directory = Path('abicheck-build-linux-x86_64-gcc')
build_output = load_build_output(directory)
report = derive_baseline_libraries(build_output, directory)
Path('baseline-libraries.json').write_text(json.dumps(report.to_dict()))
"
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

`report.ok` is `True` when every target resolved; `False` when one or more
targets could not be resolved (missing/escaping binary or header path — see
`report.errors`). `load_build_output` raises `FileNotFoundError`/`ValueError`
when `DIRECTORY` is not a readable `build-output.json` — both workflows
catch that and skip writing `baseline-libraries.json` at all, deferring to
their own follow-up step's "was not produced" error.

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
| `asset-name-template` | `abicheck-baseline-{profile}.tar.zst` | Release asset filename; `{profile}` is replaced per profile, and `{generation}` (optional) with `baseline-generation`'s value — include it to publish each scanner-compatibility generation as its own asset name rather than overwriting the previous generation's asset (see [Cache key contract](#cache-key-contract-read-before-wiring-a-consumer) below for the accepted-main equivalent, and `docs/use/baseline-management.md`'s "Scanner upgrades and baseline generations"). **Include `{profile}` when a run has more than one contract profile** — omitting it makes every profile in the matrix target the same asset name, and the retry-identity check below rejects (rather than silently discards) a genuine collision between two different profiles. |
| `build-info` | `''` | Path to a shared build/source facts pack, relative to the downloaded build-output artifact. |
| `depth` | `''` | Evidence depth passed to every dump call. |
| `baseline-generation` | `''` | Forwarded to `actions/baseline`'s `baseline-generation` input, and substituted for `{generation}` in `asset-name-template` (see above). Omit to leave the generation unset. |
| `validation` | `strict` | Forwarded to `actions/baseline`'s `validation` input. |
| `snapshot-compression` | `none` | Forwarded to `actions/baseline`'s `snapshot-compression` input (ADR-059) — independent of this workflow's own archive packaging of the *whole* baseline-set directory (encoding chosen from `asset-name-template`'s extension, via [`actions/stage-baseline`](#actionsstage-baseline)); see [Storing Baselines](../use/baseline-storage.md#compressing-stored-snapshots). |

Secret: `github-token` (optional) — falls back to the job's own
`GITHUB_TOKEN` (`permissions: contents: write` on the `publish` job).

For every discovered contract profile: downloads that profile's
build-output artifact, derives `libraries` from it, dumps the baseline-set
via `actions/baseline`, packages it as `<asset-name>` via
[`actions/stage-baseline`](#actionsstage-baseline) (encoding chosen from
the asset name's own extension — `.tar.zst`/`.tar.gz`/`.tgz`/`.tar`), and
uploads it to `release-tag`'s release. **This upload step fails closed on an
immutability violation, it does not `--clobber`:** if no asset of this name
exists yet, it uploads plainly; if one already exists, its manifest.json is
downloaded and checked in two steps. First, a **profile-identity check**:
the existing asset's own `profile` field must match this run's profile, or
the upload fails immediately regardless of content — `compute_content_digest()`
(next paragraph) deliberately excludes `profile`, so without this check two
different profiles that happen to produce identical library/snapshot/binary
content (or a template missing `{profile}` routing two profiles to the same
asset name) could otherwise have one profile's baseline silently discarded
as a "safe retry" of the other's. Second, once the profile matches, the
manifest is compared against this run's own baseline-set by *normalized*
content digest (`actions/baseline/build_manifest.py`'s `compute_content_digest()`
— library names + per-snapshot and per-staged-binary digests, deliberately
excluding volatile fields like `created_at` and the archive's own filesystem
metadata, both of which differ on every run even when the underlying
baseline-set is logically identical). Matching digests are treated as a safe
retry (e.g. after a transient failure) and no re-upload happens; a differing
digest hard-fails rather than silently replacing a published
`release-contract` asset — `release-contract` is documented (ADR-047 §10)
as immutable once published, and a re-run silently overwriting it would
mean an already-resolved consumer's "compatible with v1.0.0" comparison
quietly stopped meaning what it said. To genuinely change a
release-contract baseline-set, delete the existing asset explicitly
(`gh release delete-asset <tag> <asset-name>`) and re-run, or publish under
a new release tag.

### `actions/stage-baseline`

A small composite Action factored out of `publish-baseline.yml`'s own
packaging step: given a baseline-set directory (`actions/baseline`'s
`baseline-path` output) and an `asset-name-template`, it produces a single
archive named and encoded per that template's own extension. Exists so a
caller publishing a baseline-set through a *different* storage backend
(not this repository's release-contract flow — a different Action, a
different CI system, an internal artifact store) doesn't have to
re-implement the suffix-dispatch logic; `publish-baseline.yml` itself calls
this Action rather than keeping an inline copy, so there is one
implementation, not two that can silently drift apart.

| Input | Default | Meaning |
|-------|---------|---------|
| `baseline-path` | *(required)* | Directory containing `manifest.json` plus per-library snapshots. |
| `asset-name-template` | `abicheck-baseline-{profile}.tar.zst` | Archive filename; `{profile}` is replaced with the `profile` input, and `{generation}` (optional) with the `generation` input. The extension selects the encoding — `.tar.zst` (zstd), `.tar.gz`/`.tgz` (gzip), or `.tar` (uncompressed); anything else is a hard usage error. |
| `profile` | `''` | Substituted for `{profile}`. |
| `generation` | `''` | Substituted for `{generation}` — a no-op unless `asset-name-template` references the placeholder. |

Outputs `asset-name` (the resolved filename) and `archive-path` (identical
to `asset-name` — the archive is written to the current working
directory). Read-only over its input: it never commits, pushes, or
uploads the archive it produces.

See [Storing Baselines](../use/baseline-storage.md) for the narrative
picture of where a staged archive fits among abicheck's baseline-storage
backends — this page documents `actions/stage-baseline` itself as a fact
source, not the concepts around it.

## `update-main-baseline.yml`

Reusable workflow (`workflow_call`); wire it to a `push: branches: [<default
branch>]` trigger.

| Input | Default | Meaning |
|-------|---------|---------|
| `build-output-artifact-prefix` | `abicheck-build-` | Same as above. |
| `key-prefix` | `abicheck-baseline-main` | Actions-cache key prefix (§10). |
| `head-sha` | `github.sha` | Recorded as `project-ref`; folded into the cache key. |
| `baseline-generation` | `''` | Forwarded to `actions/baseline`'s `baseline-generation` input, and folded into `key-prefix` as `-g<generation>` (see [Cache key contract](#cache-key-contract-read-before-wiring-a-consumer) below) — two different scanner-compatibility generations never share one cache-key namespace. Omit to leave the generation unset. |
| `build-info` / `depth` / `validation` / `snapshot-compression` | same as above | |

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

**When `baseline-generation` is set, the key gets an extra segment.**
`update-main-baseline.yml`'s "Compute cache key" step folds
`-g<generation>` into the prefix *before* building the key above, so two
different scanner-compatibility generations (docs/use/baseline-
management.md#scanner-upgrades-and-baseline-generations) never share one
cache-key namespace:

```text
<key-prefix>-g<generation>-<profile-id>-<head-sha>
```

Both mirror functions accept this as an explicit keyword argument --
`accepted_main_cache_key(key_prefix, profile_id, head_sha,
generation=3)` / `accepted_main_cache_restore_prefix(key_prefix,
profile_id, generation=3)` — rather than requiring a consumer to
pre-fold `-g3` into `key_prefix` themselves. **A consumer restoring this
cache (`restore-keys: <key-prefix>-<profile-id>-`) must pass the exact
same `generation` this workflow was run with**, or the restore misses
entirely: the un-generation-scoped prefix is a *different* cache-key
namespace, not a superset of the generation-scoped one.

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

**`restore-keys` prefix matching is for freshness comparison, not for a PR
gate.** The prefix restore above resolves to whichever entry is *newest*
under the profile's prefix, regardless of which commit wrote it — correct
for `update-main-baseline.yml`'s own "what changed since the last
`accepted-main` snapshot" freshness diff, but wrong for a PR asking "did
this PR introduce a break relative to *its own base commit*": if `main` has
advanced past the PR's base SHA since the PR branched, a prefix restore
silently compares the PR against a baseline built from a commit its branch
history never contained. A PR gate must restore the **exact** key for
`github.event.pull_request.base.sha` (no `restore-keys` fallback), and pass
that same SHA as `resolve-baseline`'s `expected-project-ref` input so a
restore that somehow still lands on the wrong commit is caught as
`wrong_project_ref` rather than silently resolving — see
[resolve-baseline's own "Known gap" section](resolve-baseline.md#known-gap-accepted-main-restore-by-prefix-can-resolve-the-wrong-commit)
for the full recipe.

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
