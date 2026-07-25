---
doc_type: explanation
audience:
  - library-maintainer
  - ci-owner
level: intermediate
canonical_for:
  - baseline-lifecycle
depends_on:
  - abicheck/model.py
  - abicheck/serialization.py
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
pack, one baseline file per library).
