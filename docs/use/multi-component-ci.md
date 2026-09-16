# Multi-component CI without project-specific glue

A project that ships more than one library, compares against more than one
baseline channel, and reports from a trusted publisher has, historically, had
to write a few hundred lines of its own shell to hold the pieces together:
resolve which headers each component owns, name the snapshots a capture
produced, collect the reports, declare which checks were expected, validate the
aggregate, decide which run a baseline may come from.

None of that is project-specific work, and none of it is yours any more. This
page is the whole shape of such an integration using supported Actions only.

What stays yours: **where your build puts things, and which components own
what.** Everything below is a declaration of that, not a program.

## 1. Declare the components

One JSON document, checked into your repository, says which libraries exist and
what each one owns. Path fields are globs resolved against `library-root`.

```json title=".ci/abicheck-components.json"
[
  {
    "name": "libfoo",
    "artifact": "lib/libfoo.so*",
    "header": ["include/foo/*.h"],
    "header_exclude": ["include/foo/plugin.h"],
    "include": ["include", "/opt/dep/include"]
  },
  {
    "name": "libfooPlugin",
    "artifact": "lib/libfooPlugin.so*",
    "header": ["include/foo/plugin.h"],
    "include": ["include", "/opt/dep/include"]
  }
]
```

Read that carefully, because it encodes the one distinction integrators most
often get wrong:

* `header` is an **export obligation**. These are the declarations the
  component is held responsible for; a change to one of them is a change to
  *this* component's surface.
* `include` is **parser context**. It tells the C++ parser where to find what
  the headers `#include`, and it never widens what the component owns. That is
  what lets both components above name the same installed include tree while
  owning disjoint parts of it — and why a change to `libfoo`'s headers does not
  show up as a `libfooPlugin` finding.
* `header_exclude` is how one component hands a header to a sibling. An
  exclusion that matches nothing is an error, not a no-op: a stale exclusion is
  exactly how a component silently re-acquires a header that has since moved.

Three things you no longer write:

* **Picking the real shared object.** `lib/libfoo.so*` matches `libfoo.so`,
  `libfoo.so.1` and `libfoo.so.1.2.3`. Those are one artifact spelled three
  ways, so they are de-duplicated by resolved identity, not treated as
  ambiguity and not banned. Two *genuinely distinct* files are an error; a
  symlink pointing out of `library-root` is refused.
* **ELF validation.** Each artifact must be an ELF shared object (`ET_DYN`), and
  every component must name the same machine — a mixed-architecture set
  produces findings that describe the toolchain rather than the change.
* **Arch/path plumbing in shell.** If your install path varies by architecture,
  interpolate it into `library-root`, not into a hand-rolled `find`.

## 2. Capture once

```yaml
- name: Capture ABI snapshots
  id: capture
  uses: abicheck/abicheck/actions/baseline@<pinned-sha>
  with:
    library-spec: .ci/abicheck-components.json
    library-root: ${{ github.workspace }}/install
    output-dir: ${{ runner.temp }}/candidate
    project-ref: ${{ github.event.pull_request.head.sha || github.sha }}
    profile: linux-x86_64-gcc-release
    depth: headers
    baseline-generation: '1'
    snapshot-compression: zstd
```

This performs no build. It reads what your build already installed. Capture
stays at header depth — no compilation database, no build-evidence collection,
no source replay is required for any of this.

Upload `output-dir` as an artifact. It is a **baseline-set**: one snapshot per
component plus a `manifest.json` recording profile, project ref, generation and
per-component content digests. Because the snapshots are self-contained, every
later job compares them with the original build tree gone, and nothing
re-extracts.

## 3. Name the snapshots in the comparing job

The capture ran in another job; this one downloaded the artifact. Do **not**
parse `manifest.json` yourself. Its `artifact` field is the producing runner's
absolute binary path and means nothing here; the portable entry is `snapshot`,
and the per-component digests are worth checking.

```yaml
- name: Locate the candidate snapshots
  id: candidate
  uses: abicheck/abicheck/actions/resolve-baseline@<pinned-sha>
  with:
    kind: members
    baseline-path: ${{ runner.temp }}/candidate
    channel: candidate
    bundle-members: '["libfoo", "libfooPlugin"]'
    profile: linux-x86_64-gcc-release
    expected-project-ref: ${{ github.event.pull_request.head.sha || github.sha }}
    expected-baseline-generation: '1'
```

`steps.candidate.outputs.snapshot-paths` is a JSON object mapping each
component to its snapshot file. Every check a single-target resolution
performs — content digest, profile, project ref, generation, schema, path
escape — applies to each member, on the candidate side as well as the baseline
side. Validating the baseline alone does not validate the candidate.

`baseline-path` also accepts a `.tar.zst`/`.tar.gz`/`.tgz`/`.tar` archive of
the same, extracted in place.

## 4. Resolve the baselines

One `resolve-baseline` call per channel, each against the set you staged for
it. Staging the right set is the trust decision, and
`actions/verify-baseline-source` is what makes it:

```yaml
# "What did this pull request introduce?" -- the accepted-main channel.
- name: Find the baseline run for this PR's base commit
  id: base
  uses: abicheck/abicheck/actions/verify-baseline-source@<pinned-sha>
  with:
    mode: producer-run
    workflow: .github/workflows/ci.yml
    expect-event: push
    expect-head-sha: ${{ github.event.pull_request.base.sha }}
    expect-head-branch: ${{ github.event.pull_request.base.ref }}
    required-jobs: build,abi-capture
```

Exact base SHA alone is not eligibility, which is why every one of those inputs
exists: a run at that commit can come from any branch, any event, and can have
failed — and a capture step that deliberately runs after a test failure means a
failed run still has an artifact to offer. `required-jobs` names the results the
baseline actually depends on; a required job that *never ran* is reported
separately from one that failed, because an absent capture job silently treated
as passing is how a run with no capture becomes a baseline. If unrelated matrix
legs are allowed to fail, say so explicitly with
`allow-unrelated-job-failures: true` — it requires `required-jobs`, so something
is always still checking that the baseline's own evidence was produced.

`outcome` distinguishes `resolved`, `not_found` (a real lifecycle state — the
first release, a branch with no successful run yet) and `lookup_failed` (an API
error). Keep them apart in your own reporting: collapsing them is how a
transient failure becomes a silent "no baseline, therefore compatible".

For a release channel, `mode: tag` answers the other question:

```yaml
- name: Verify the tag this capture claims
  uses: abicheck/abicheck/actions/verify-baseline-source@<pinned-sha>
  with:
    mode: tag
    tag: ${{ steps.source.outputs.tag }}
    built-sha: ${{ steps.source.outputs.head-sha }}
```

The name is resolved through `git/ref/tags/<name>` explicitly — a generic
revision endpoint accepts any revision name and will happily resolve a
*branch*. Annotated tags are peeled to the commit they point at. And the name is
never required to look like a version: plenty of projects tag `1.5.2` with no
`v`, and a name-shaped guard would exclude every one of their releases.

Then run one `check-target` per (component × channel), passing the resolved
candidate snapshot as `new-library`.

## 5. Declare and aggregate the checks

Every check the event was supposed to produce is named **once**, in one
declaration, whether or not it produced a report:

```yaml
- name: Aggregate
  id: aggregate
  if: always()
  uses: abicheck/abicheck/actions/aggregate@<pinned-sha>
  with:
    reports-dir: ${{ runner.temp }}/reports
    manifest-path: ${{ runner.temp }}/expected-checks.json
    checks: |
      [
        {"id": "libfoo@linux-x86_64-gcc-release#accepted-main@headers",
         "report": "${{ steps.main-foo.outputs.report-path }}"},
        {"id": "libfooPlugin@linux-x86_64-gcc-release#accepted-main@headers",
         "report": "${{ steps.main-plugin.outputs.report-path }}"},
        {"id": "libfoo@linux-x86_64-gcc-release#release-contract@headers~release",
         "report": "${{ steps.rel-foo.outputs.report-path }}"},
        {"id": "libfooPlugin@linux-x86_64-gcc-release#release-contract@headers~release",
         "report": "${{ steps.rel-plugin.outputs.report-path }}"}
      ]
```

An empty `report` means "expected, produced nothing". That check is still
declared, so the run reports it as an unavailable target rather than quietly
describing a shorter set of checks than it actually had. **Declare only the
checks the event asked for** — naming an `accepted-main` comparison on a tag or
push build manufactures a missing check for a question nobody asked.

The `id` is the check's full identity: component, profile, baseline channel,
depth, and any explicit id. It is written into the report filename verbatim.
Do not sanitize the `@`/`#`/`~`/`!` separators — `aggregate` reads the identity
back out of that filename, and a sanitized name makes every report aggregate as
an unavailable target while the run looks like it produced them. A report that
records its own `target_id` is authoritative: a declaration disagreeing with it
is refused rather than silently re-labelled.

This Action keeps two things apart that a hand-rolled `aggregate … || true`
plus a JSON key check cannot:

| | Reported on | Fails the Action? |
|---|---|---|
| A real ABI break | `compatibility-exit` (`0`/`1`/`2`/`4`) | No — you own the gate |
| Incomplete coverage | `coverage` (`complete`/`partial`/`empty`) | No |
| A refused declaration | step log | **Yes** |
| A document describing no real outcome | step log | **Yes** |

`channels` additionally breaks the result down per baseline channel, so "the
release comparison is missing" is distinguishable from "one component is
missing" instead of collapsing into one total.

Two footguns it forecloses for you: the expected-target manifest must live
**outside** `reports-dir` (`aggregate` globs `*.json` there, so a manifest
inside it becomes an extra target), and a previous run's `aggregate.json` in
that directory is refused rather than aggregated as a component result.

## 6. Publish from a trusted job

Analysis runs unprivileged, under the contributor's pull request, including
from forks — with no write credentials and no secrets. Publication is a
separate `workflow_run` job that runs only reviewed, pinned code. See
[Fork PR reporting](fork-pr-reporting.md) for the full boundary;
`actions/verify-source-run` establishes which run and which pull request from
the GitHub API rather than from the artifact, and `actions/report` renders the
aggregate document without re-running anything.

!!! warning "Deployment prerequisite"
    `workflow_run` only ever runs the copy of a workflow file on the default
    branch. Until a maintainer merges it there, nothing in a pull request —
    including the pull request that adds the file — publishes a comment. That
    is by design. Do not reach for `pull_request_target` to work around it: it
    would run contributor-authored build code with write credentials, which is
    the exact thing this split exists to prevent.

## What is still yours

* The component declaration in step 1 — where your build installs things, and
  which component owns which header. That is genuinely project knowledge.
* Your build commands, and any dependency preparation they need.
* Which revision and profile you intend to compare against. abicheck owns
  resolving and validating that selection; choosing it is policy.

## See also

* [Baseline management](baseline-management.md) — channels, generations, and
  the immutability contract for a published release baseline.
* [Aggregate reports](aggregate-reports.md) — the outcome document's own shape.
* [Fork PR reporting](fork-pr-reporting.md) — the publication trust boundary.
* [GitHub Action recipes](github-action-recipes.md) — single-library setups.
