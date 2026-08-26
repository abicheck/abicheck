---
doc_type: explanation
audience:
  - library-maintainer
  - ci-owner
level: intermediate
canonical_for:
  - baseline-lifecycle
depends_on:
  - abicheck/model/snapshot.py
  - abicheck/serialization.py
  - abicheck/product_baseline.py
  - abicheck/bundle.py
lifecycle: active
generated: false
---

# Baseline Management

ABI baselines are pre-computed snapshots of a library's ABI surface at a known-good
point (typically a release). Comparing future builds against a baseline detects
breaking changes before they ship.

> The baseline is the input to the CI gating pipeline (classify → suppress →
> severity → exit code) — see [CI Gating](ci-gating.md) for how it combines
> with policies, suppressions, and severity.

> **See also.** For a project publishing baselines through
> `.abicheck.yml`'s `baseline:` block and the G30/ADR-047 integration model
> (`release-contract`/`accepted-main` channels, `resolve-baseline`), see the
> [`publish-baseline`/`update-main-baseline` reference](../reference/publish-baseline.md)
> and [Which Scenario Am I?](../integration/index.md#baselines) — this
> page's own model (baseline identity, storage-agnostic snapshots) is what
> both are built on.

> **The built-in baseline registry command is gone.** The pre-1.0 CLI reset
> (ADR-043) removed the whole `abicheck baseline` subcommand group
> (`push`/`pull`/`list`/`delete`) with no replacement command — abicheck's
> CLI has no opinion on *where* you store a snapshot. [Storing
> Baselines](baseline-storage.md)'s recipes (GitHub Releases, git-committed
> files, Actions cache, external artifact stores) all just move a plain JSON
> file around and continue to work unchanged; only the registry's own
> addressing/integrity layer (`library:version:platform` keys,
> checksum-on-pull) has no direct equivalent. For a one-off "compare against
> a previous build" without managing a baseline file yourself, see
> [`scan --against`](create-baseline.md#scan-against-for-a-one-off-comparison).

> **A whole-product (multi-library) baseline is a separate, library-only
> module, not a CLI feature.** Everything above is per-library. A product
> shipping several interdependent shared libraries — where a symbol one
> library imports from a sibling disappearing is a real cross-DSO ABI
> break no single-library `scan --against` can see — has its own storage
> format and comparison entry point in
> `abicheck.product_baseline`: `pack_product_baseline`/
> `unpack_product_baseline` archive/restore an entire product directory
> as one deterministic `.tar.zst`, and `compare_product_directories`
> runs the bundle-aware comparison (ADR-023) directly, in Python, with
> no CLI subprocess. `abicheck.bundle.build_bundle_snapshot_from_metadata`
> is the underlying primitive that lets that cross-DSO analysis run from
> already-parsed `ElfMetadata` (e.g. a stored `AbiSnapshot.elf`) instead
> of requiring the old release's binaries on disk. See each function's
> own docstring for the full contract; there is no CLI wiring for this
> module and none is planned without a concrete use case (see
> `docs/contribute/plans/product-baseline-per-library-header-roots.md`
> for the one follow-up slice implemented so far, and its own "Out of
> scope" section for what isn't).

This page covers the **lifecycle model**: what a baseline is, why most
projects need two of them, and what makes a baseline comparable across
builds. For the mechanics of producing one, see [Creating and Comparing a
Baseline](create-baseline.md); for where to keep it, see [Storing
Baselines](baseline-storage.md).

## Two kinds of baseline: release contract vs. accepted-main

A single fixed baseline answers only one question well. Most projects
actually need *two* baselines, because they answer different questions and
should behave differently when a PR is labeled as an intentional break:

| Baseline | Question it answers | Where it comes from | What advances it |
|---|---|---|---|
| **Release / contract baseline** | Is the current code still compatible with what we already shipped? | A dump of the last **released** version (a release tag/asset — [Recipe A](baseline-storage.md#recipe-a-github-releases-recommended)) | Only a new project release |
| **Accepted-main baseline** | Did *this PR* introduce a new break (as opposed to one already merged)? | A dump of the last build that passed CI on the default branch | Every PR merged to the default branch |

Conflating them causes a specific, recurring failure: if CI only keeps a
*fixed* release baseline and skips the whole check whenever a PR carries an
`intentional-breaking-change` label, the break lands on the default branch
still relative to the old release. Every subsequent, unrelated PR then
diffs against that same stale release baseline, sees the same break again,
and fails too — even though the break was already reviewed and accepted.
The label suppressed the *check*, not just the *gate*, so nothing ever
re-baselines.

**The fix is to keep both baselines running, and let the label only relax
their gates — never whether either comparison runs:**

- Always run and publish **both** comparisons — the release-contract report
  stays visible even when its gate is relaxed, so "compatible with the last
  release" doesn't silently go unreported.
- On the PR that introduces the break, the label relaxes **both** jobs'
  `fail-on-breaking` — that PR is, by construction, the one case where the
  accepted-main comparison is *expected* to report a break (that's what it's
  for), and the label plus its review is what makes the break "accepted."
  Neither job's *comparison* is skipped, only its gate, for that one PR.
- The accepted-main baseline is what ordinarily gates every other PR:
  refresh it from the default branch after every merge (a lightweight `dump`
  step on a `push` trigger, [Recipe C](baseline-storage.md#recipe-c-github-actions-cache)
  or a git-committed file work well for this since it churns on every merge).
  Once refreshed, the gate is strict again for the *next* PR — the label
  only ever excuses the PR that carries it, not the ones that follow.
- The release-contract baseline advances deliberately, only when you cut a
  new release — treat that refresh as part of the release process, not
  something a regular PR should touch.

```yaml
# PR workflow — both baselines compared, both share the same label-relaxed gate
jobs:
  release-contract:
    steps:
      - uses: abicheck/abicheck@v0.5.0
        with:
          abi-baseline: latest-release       # fixed until the next release
          new-library: build/libfoo.so
          new-header: include/foo.h
          fail-on-breaking: ${{ !contains(github.event.pull_request.labels.*.name, 'intentional-breaking-change') }}

  accepted-main:
    steps:
      - uses: abicheck/abicheck@v0.5.0
        with:
          old-library: main-baseline.json     # refreshed on every merge to main
          new-library: build/libfoo.so
          new-header: include/foo.h
          # Same label relaxes this gate too — this comparison is *expected*
          # to report a break for the one PR that introduces it. Once merged
          # and main-baseline.json is refreshed, every subsequent PR is
          # gated strictly again (the label doesn't carry over).
          fail-on-breaking: ${{ !contains(github.event.pull_request.labels.*.name, 'intentional-breaking-change') }}
```

### Baseline identity is more than a version number

A baseline file name like `2.0.0.abicheck.json` is not self-describing
enough on its own to guarantee two dumps are comparable — a meaningful
identity also includes the platform/architecture, build profile (compiler,
ISA, debug/release), the public-header/source configuration used to dump it,
and (for build-source evidence) the producer and toolchain that collected it
(replay vs. `abicheck-cc` vs. the Clang plugin — see [Producing Source
Facts](producing-source-facts.md) for how each is versioned). If your project ships more
than one platform/architecture/build-profile combination, encode that in the
baseline's path or filename (e.g.
`linux-x86_64-icx-avx2-debug/2.0.0.abicheck.json`), not just the version —
otherwise a baseline dumped on one profile can silently get compared against
a candidate built on another.

For a project that ships several libraries from one build, apply this per
library rather than trying to fold them into a single baseline file — see
[Source Scans → Recommended flow: a multi-library release with one shared
facts
pack](github-action-source-scans.md#recommended-flow-a-multi-library-release-with-one-shared-facts-pack)
for a concrete per-library baseline-set walkthrough (build once, one facts
pack, one baseline file per library). This per-library baseline-set is still
the right workflow for checking each library's own ABI — it is not
superseded by the whole-product `pack_product_baseline`/
`compare_product_directories` module described above, which answers a
different question (cross-library, bundle-aware breakage) rather than
replacing the per-library one.

### A new library's first release

A multi-library baseline-set (the walkthrough referenced just above) has one
`manifest.json` per published product ref, listing every library that
existed *at that ref*. A library that ships for the first time in the
release you're about to cut therefore has no entry in the previous
generation's baseline-set — not because anything is broken, but because it
genuinely did not exist yet.

By default, `resolve-baseline`/`check-target` treat a target missing from an
otherwise-healthy baseline-set as `ambiguous`, not as a special case —
deliberately: far more often, a target absent from a real baseline-set is a
staging/configuration mistake (the wrong channel, the wrong `baseline-path`,
a typo'd target id) than a genuine new-library event, and silently passing
that case would hide real misconfiguration. For a check you've explicitly
designed to tolerate a target's first appearance, opt in per check with
`allow-new-target: true` (`resolve-baseline`/`check-target`'s own input;
`checks[].allow_new_target` in `.abicheck.yml`'s per-target config) —
the outcome becomes `new_target`, an advisory, non-fatal lifecycle state
distinct from both `resolved` (a real comparison ran) and `ambiguous` (a
real problem). See the [resolve-baseline](../reference/resolve-baseline.md)
and [check-target](../reference/check-target.md) references for the full
outcome/report shape.

```yaml
targets:
  libnew:
    kind: library
    binary_pattern: "lib/libnew.so"
    checks:
      - channel: release-contract
        depth: source
        required: false        # pair with allow_new_target -- see below
        allow_new_target: true
```

**Pair `allow_new_target: true` with `required: false`.** A `new_target`
check never produces a compatibility verdict (the same reasoning as a
bootstrap `not_found` pass — see [CI Gating](ci-gating.md) for how the
run-plan's `required:` gates coverage), so a `required: true` check would
still block the release on this target's own coverage gap even though the
`new_target` outcome itself is advisory. `required: false` is what actually
lets the release proceed.

**Never set `allow_new_target: true` on a bundle check.** A bundle
comparison needs one coherent release where every member already
coexisted (`resolve_bundle()` builds a real cross-library dependency graph
from staged ELF binaries, not independent per-member snapshots) — "one of
this bundle's members is new" has no well-defined old side to compare
against, so `allow_new_target` is rejected outright for `kind: bundle` at
config-validation time. Scope the new library individually with a
`kind: library` target check instead, and add it to the bundle once a real
release has published a baseline-set covering every member together.

**After the release publishes**, the new library is a normal artifact in
every baseline-set from that point forward (`update-main-baseline.yml`'s
freshness report reflects it under `libraries added`), and the
`allow_new_target: true`/`required: false` pair on its checks can be
dropped — an ordinary `required: true` check against `accepted-main`/
`release-contract` resolves it the same as any other library from then on.

A library that is *removed* from a release is the converse case, already
covered on the compatibility-evaluation side by `compare`/`scan`'s
`--fail-on-removed-library` (see the [exit-codes
reference](../reference/exit-codes.md)) rather than by the
baseline-resolution layer this section covers.

### Scanner upgrades and baseline generations

Upgrading the abicheck version your CI pins is a separate axis from your
product's own baseline lifecycle above — don't conflate the two. A baseline
identifies `product state × build profile`; it says nothing about which
*scanner* extracted it, and the two questions need different answers to
"does this baseline still need to be regenerated?"

**Most abicheck upgrades don't invalidate an existing baseline.** A report-
format change, a policy/severity/suppression change, or even a new detector
running over already-collected facts can all read an old snapshot exactly as
before — nothing about what the snapshot's own facts *mean* changed. Only a
narrower class of change genuinely does:

| Change | New baseline needed? |
|---|---|
| Report format, SARIF, PR comment, HTML | No — only the presentation changed |
| Policy/severity/suppression | No — the snapshot's facts are unchanged, only their interpretation |
| A new detector over already-captured facts | Usually no |
| A fixed matching/diff algorithm | Usually no, but verify with a shadow run — the verdict can change |
| A newly-extracted fact | Yes, for full coverage — an old snapshot doesn't physically contain it |
| A fix to a previously-*wrong* extracted fact | Yes — the old value can't be trusted |
| A changed normalization/hash recipe | Yes — otherwise scanner drift reads as a product change |
| A snapshot schema bump | Usually yes |
| A different compiler/stdlib/target/flags | This is a different build **profile**, not a version bump — see above |

Tying "regenerate the baseline" to the installed abicheck **package**
version conflates these — a patch release that only touched report
formatting would force an unnecessary rebaseline, while two package
versions sharing the same normalization/hash recipe could be compared
against each other without anyone noticing the recipe itself changed
underneath. `baseline_generation` is a deliberately separate,
**caller-assigned** integer for exactly the subset of changes above that
actually matter:

- `actions/baseline`'s `baseline-generation` input records it in the
  produced `manifest.json` (`baseline_generation`, next to `profile` and
  `snapshot_schema`). Bump it only when you make one of the "Yes" changes
  above — never automatically from the abicheck version string.
- `actions/resolve-baseline`'s `expected-baseline-generation` input (and
  `check-target`/`check-project.yml`'s inputs of the same name) requires the
  resolved baseline-set to carry exactly that generation, failing closed
  with the `stale_generation` outcome otherwise — see the
  [resolve-baseline reference](../reference/resolve-baseline.md).
- A generation bump is also its own `refresh-required` reason from
  `actions/baseline` when a `previous-manifest` is given, independent of
  whether `snapshot_schema`/`fact_set`/the library set happened to change
  too.

**Generator provenance is a separate, purely informational field —
don't conflate it with `baseline_generation`.** Every manifest also records
a `generator` block (`{"tool": "abicheck", "version": "0.6.0", ...}`),
always including the installed abicheck package `version` and, when the
publishing workflow supplies them (`actions/baseline`'s optional
`generator-git-sha`/`generator-action-ref` inputs), the exact commit/ref
that produced the baseline-set — useful for debugging "what actually
extracted this" without guessing from context. `generator.version` is
**never** compared by any resolve/freshness check and never triggers
`refresh-required` on its own: that's precisely `baseline_generation`'s
job, and it stays a caller-assigned decision, not something derived from a
version string.

**A baseline can never be upgraded to a new generation by re-saving it** —
loading an old snapshot with a newer scanner and writing it back out does
not make the missing/previously-wrong facts appear; the serializer stamps
the current schema version, but reliability markers for facts the old
scanner never (or wrongly) captured are preserved rather than silently
upgraded. A real rebaseline for a scanner-generation bump means re-running
`dump` against the original artifact (and, for header/build/source-depth
baselines, the original headers/build inputs) with the new scanner — not
reprocessing the old baseline file.

**Recommended upgrade flow**, when a scanner change does need a new
generation:

1. Regenerate baselines for the same product refs the old generation
   covers, using the new scanner — never mix generations across the two
   sides of one comparison.
2. Review the diff between the old- and new-generation baselines for the
   *same* product ref — that diff is scanner drift, not a product change.
3. Publish the new generation as a new, immutable artifact (e.g. a
   differently-named release asset, or a new cache-key prefix) rather than
   overwriting the old one in place, so in-flight PRs/comparisons against
   the old generation keep working until they're switched over.
4. Only then flip the scanner pin and the active `baseline-generation`
   together — a scanner upgrade with no matching generation bump for a
   baseline-affecting change is the state `expected-baseline-generation`
   exists to catch, not something to leave implicit.

**The PR that upgrades the scanner itself needs two CI lanes, briefly.**
An ordinary PR's `check-project`/`check-single` gate compares the
candidate against `accepted-main`'s *existing* baseline-generation — but
the one PR that bumps the scanner pin (or otherwise causes a
`baseline_generation` bump) would otherwise compare a new-generation
candidate against an old-generation baseline, mixing scanner drift into
the verdict. Run that PR through two lanes instead of one, both driven off
the same `check-project.yml`/`check-single.yml` inputs:

- **Lane A (binding)** — unchanged: `baseline-channel: accepted-main`,
  no `expected-baseline-generation` override, gates the PR exactly like
  any other PR against whatever generation is currently accepted.
- **Lane B (shadow, informational only)** — a second job in the same
  workflow matrix that dumps a *fresh* baseline for the PR's own base
  commit with the new scanner (e.g. `actions/baseline` with
  `project-ref: ${{ github.event.pull_request.base.sha }}` run in this
  same job, or `resolve-baseline`'s `expected-project-ref` pinned to that
  exact SHA if a pre-staged new-generation baseline-set already exists),
  then compares the candidate against *that* instead of the stale
  accepted-main entry. Mark this job `continue-on-error: true` (or run it
  under `gate-mode: advisory`, see [CI Gating](ci-gating.md)) — it exists
  for the reviewer to read the scanner-drift diff before merge, not to
  block the PR.

After merge, `update-main-baseline.yml` runs on the new `main` SHA with
the new scanner pin and writes the new-generation entry; Lane B becomes
unnecessary for every PR after that one, since Lane A now compares against
the new generation like any other PR.

**Storing more than one generation side by side.** Whichever storage
backend you use (see [Storing Baselines](baseline-storage.md)), a scanner
upgrade means publishing the new generation *next to* the old one, not
overwriting it — the recommended flow above's step 3. `{generation}` in
`actions/stage-baseline`'s `asset-name-template` templates this for a
release-asset backend directly (e.g.
`abicheck-baseline-g{generation}-{profile}.tar.zst`); for a
git-committed baseline directory, the same idea without any template
mechanism needed:

```text
abi/
  g2/
    linux-x86_64-gcc14/
  g3/
    linux-x86_64-gcc14/
  active-generation.txt   # e.g. "g3" -- what current CI compares against
```

Flip `active-generation.txt` (and the scanner pin) together, in one
trusted commit/PR — never separately, per the ordering rule above.
