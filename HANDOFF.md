# Handoff: moving PVXS's abicheck integration onto supported owners

**Audience:** the agent maintaining the abicheck integration in
`napetrov/pvxs` / `epics-base/pvxs`.
**Purpose:** a review artifact. Nothing here is a runtime format, and nothing
downstream should read this file.

This handoff is deliberately explicit about what it does **not** deliver — see
[§6](#6-not-delivered) — because the measure of this work is downstream
responsibility removed, and claiming a removal that is not yet safe would be
worse than leaving it listed.

---

## 1. Revisions reviewed and tested

| What | Revision |
|---|---|
| abicheck base (`main` at implementation time) | `dc8c2dd43cf7e9ed4f4b4cda439cd03f87a63205` |
| abicheck branch | `claude/clever-meitner-2ja05m` |
| abicheck PR | **Not yet opened at the time of writing — see the note below.** |
| PVXS consumer reviewed | `napetrov/pvxs` PR #2, head `65d3f8d1416b2a916188500c952628029d631713` (fetched fresh; unchanged from the previous review's anchor) |

**Status: unmerged.** There is no merge SHA and none is predicted here. Pin
the abicheck Actions to the merge commit once a maintainer merges the PR; the
`@<pinned-sha>` placeholders below are literal placeholders, not a value to
guess. Do not begin the PVXS deletions in §3 before that.

Upstream work already on `main` that this builds on rather than duplicates:
report-only publication and the aggregate-shaped PR comment (ADR-073, #1311,
#1307), and `publish-baseline.yml`'s immutability contract.

---

## 2. What to use

Every path below is `abicheck/abicheck/actions/<name>@<pinned-sha>`.

### 2.1 Declare the components (replaces `.ci-local/abicheck-inputs.sh`)

Add one document to PVXS. This is the *only* irreducibly project-specific
artifact in the whole integration.

```json title=".ci-local/abicheck-components.json"
[
  {
    "name": "libpvxs",
    "artifact": "lib/${EPICS_HOST_ARCH}/libpvxs.so*",
    "header": ["include/pvxs/*.h"],
    "header_exclude": ["include/pvxs/iochooks.h"],
    "include": ["include", "${EPICS_BASE}/include",
                "${EPICS_BASE}/include/os/Linux",
                "${EPICS_BASE}/include/compiler/gcc"]
  },
  {
    "name": "libpvxsIoc",
    "artifact": "lib/${EPICS_HOST_ARCH}/libpvxsIoc.so*",
    "header": ["include/pvxs/iochooks.h"],
    "include": ["include", "${EPICS_BASE}/include",
                "${EPICS_BASE}/include/os/Linux",
                "${EPICS_BASE}/include/compiler/gcc"]
  }
]
```

`${...}` is **not** expanded by abicheck. Render the two values (`EPICS_BASE`,
`EPICS_HOST_ARCH`) with `envsubst` in the capture step, or template the file —
resolving the EPICS prefix and host arch is PVXS's own knowledge, and abicheck
deliberately hard-codes neither.

This preserves the ownership split exactly as `abicheck-inputs.sh` states it:
`libpvxs` owns every installed public header except `iochooks.h`; `libpvxsIoc`
owns only `iochooks.h`, with the core and EPICS trees as **include context**,
which never widens what it owns.

Then:

```yaml
- uses: abicheck/abicheck/actions/baseline@<pinned-sha>
  with:
    library-spec: .ci-local/abicheck-components.json
    library-root: ${{ inputs.top || github.workspace }}
    output-dir: ${{ runner.temp }}/abicheck-candidate
    project-ref: ${{ inputs.project-ref }}
    profile: ${{ inputs.profile }}
    depth: headers
    build-config: .ci-local/abicheck.yml
    baseline-generation: '1'
    generator-action-ref: ${{ inputs.abicheck-ref }}
    snapshot-compression: zstd
```

New inputs: `library-spec`, `library-root`, `library-spec-require-elf`
(default `true`). New outputs: `resolved-libraries` (the concrete array that
was dumped) and `machine` (the agreed ELF machine, e.g. `EM_X86_64`).
`libraries` is now optional; giving both it and `library-spec` is a usage
error.

**What resolution does, so you can delete the shell that did it:**

| `abicheck-inputs.sh` did | Now |
|---|---|
| `find -maxdepth 1 -type f -name 'lib*.so.*'`, require exactly one | `artifact` glob; matches are de-duplicated by **resolved identity**, so the `libpvxs.so` / `.so.1` / `.so.1.2.3` alias chain is one artifact, not three. Two genuinely distinct files are an error. |
| Exclude symlinks so they aren't analysed twice | Not needed, and *better*: a pattern naming only `libpvxs.so` (the unversioned alias a consumer links against) still resolves. Only a link resolving outside `library-root` is refused. |
| `head -c 4 | grep ELF`, `readelf -h | grep 'Type: DYN'` | Built in; a non-ELF or non-`ET_DYN` artifact is refused by name. |
| Compare `Machine:` between the two libraries | Built in (`require_same_machine`), and it works on an `e_machine` value abicheck has no name for — agreement does not depend on recognising the architecture. |
| `find ... ! -name iochooks.h` | `header` globs minus `header_exclude`. An exclusion matching **nothing** is an error, not a silent no-op — that is how a component quietly re-acquires a header that moved to a sibling. |
| Assert each include root exists | Built in; a missing include root is refused rather than dropped, because it changes how every header parses. |
| Build the `libraries` JSON in inline Python | Gone. |

One behavioural improvement worth knowing: a resolved path containing a
space, tab or control character is now **refused by name**. The `libraries`
contract serializes `header`/`include` space-separated and word-splits them,
so such a path silently became two wrong paths before. PVXS is unaffected
(no whitespace in its install paths) but a future one might not be.

**Still yours:** discovering `EPICS_BASE` from `configure/RELEASE.local`,
resolving `EPICS_HOST_ARCH`, and the `linux-*` host-arch guard. Those are
build-system facts, not ABI facts.

### 2.2 Name the snapshots (replaces `.ci-local/abicheck-snapshots.sh`)

```yaml
- name: Locate candidate snapshots
  id: snapshots
  uses: abicheck/abicheck/actions/resolve-baseline@<pinned-sha>
  with:
    kind: members
    baseline-path: ${{ env.CANDIDATE_DIR }}
    channel: candidate
    bundle-members: '["libpvxs", "libpvxsIoc"]'
    profile: ${{ env.ABICHECK_PROFILE }}
    expected-project-ref: ${{ github.sha }}
    expected-baseline-generation: '1'
```

Outputs: `snapshot-paths` (JSON object, component → snapshot path) and
`members` (per-member `{target, outcome, snapshot, message}` rows). Consume as
`fromJSON(steps.snapshots.outputs.snapshot-paths).libpvxs`.

Note the **case change from your script**: `libpvxsIoc` keeps its real name.
`abicheck-snapshots.sh` lower-cased it to `libpvxsioc` because a
`GITHUB_OUTPUT` key had to; a JSON object does not.

This is the same per-target resolver `kind: target` uses, so each member gets
the full check set — normalized **content digest**, profile, `project_ref`,
`baseline_generation`, schema, and path-escape — on the **candidate** side, not
only the baseline side. Your script explicitly skipped digest verification to
avoid a second divergent verifier; that reasoning was right, and this closes
the gap by reusing the canonical one instead. It also still reads `snapshot`
(portable) rather than `artifact` (the producing runner's absolute binary
path), which a test pins by name.

Verified caveat: the digest is over abicheck's **normalized** content view, so
an edit that survives normalization (an unknown extra key in the storage
envelope) does not change it, while any real surface edit does. Both were
exercised.

### 2.3 Collect, aggregate, validate (replaces `abicheck-collect.sh`, `abicheck-validate-aggregate.sh`, and the inline aggregate step)

```yaml
- name: Aggregate
  id: aggregate
  if: always()
  uses: abicheck/abicheck/actions/aggregate@<pinned-sha>
  with:
    reports-dir: ${{ github.workspace }}/abicheck-reports
    manifest-path: ${{ github.workspace }}/abicheck-expected-checks.json
    checks: ${{ steps.declare.outputs.checks }}
```

Build `checks` once, event-aware, in a small step of your own (this stays
yours — *which* checks an event asks for is your policy):

```bash
python3 - <<'PY' >> "$GITHUB_OUTPUT"
import json, os
p = os.environ["ABICHECK_PROFILE"]
checks = []
if os.environ["EVENT"] == "pull_request":
    checks += [
      {"id": f"libpvxs@{p}#accepted-main@headers",    "report": os.environ["MAIN_CORE"]},
      {"id": f"libpvxsIoc@{p}#accepted-main@headers", "report": os.environ["MAIN_IOC"]},
    ]
checks += [
  {"id": f"libpvxs@{p}#release-contract@headers~release",    "report": os.environ["REL_CORE"]},
  {"id": f"libpvxsIoc@{p}#release-contract@headers~release", "report": os.environ["REL_IOC"]},
]
print("checks=" + json.dumps(checks))
PY
```

Outputs: `status`, `coverage`, `compatibility-exit`, `expected`, `analyzed`,
`unavailable`, `channels`, `aggregate-path`.

**`aggregate ... || true` and the hand-written JSON checks both go away.** The
Action separates the axes for you:

* `compatibility-exit` carries `abicheck aggregate`'s own `0`/`1`/`2`/`4` and
  **never fails the step** — your shadow gate stays advisory by reading it.
* A refused declaration, a usage error (`64`), an exit code that is not one of
  the compatibility codes, or a document that does not describe a real outcome
  **always fails the step**. Operational loss can no longer reach the publisher
  disguised as an empty finding set.

Preserved from your scripts, now enforced upstream and tested: the
expected-target manifest may not live inside `reports-dir` (refused, not just
documented); a previous run's `aggregate.json` there is refused rather than
aggregated as an extra target; check ids are written **verbatim** with their
`@`/`#`/`~`/`!` separators intact.

Two things you did not have:

* A report recording its own `target_id` is **authoritative**. A declaration
  naming it differently is refused instead of silently re-labelling one
  check's result as another's — and a *renamed file* keeps that identity.
* A malformed report is `unusable`, tracked separately from `missing`. Both
  stay declared expected; the distinction between "never ran" and "ran and
  produced garbage" survives, and one component's failure never costs the
  others their diagnostics.
* `channels` splits the roll-up per baseline channel, so "the release
  comparison is missing" is distinguishable from "one component is missing".
* The manifest is written atomically — a half-written one declares a *shorter*
  expected set than the run had.

### 2.4 Baseline eligibility (replaces the inline shell in `abicheck-baseline.yml` and the two staging steps in `ci-scripts-build.yml`)

```yaml
# accepted-main: which run may supply this PR's base baseline?
- id: base
  uses: abicheck/abicheck/actions/verify-baseline-source@<pinned-sha>
  with:
    mode: producer-run
    workflow: .github/workflows/ci-scripts-build.yml
    expect-event: push
    expect-head-sha: ${{ github.event.pull_request.base.sha }}
    expect-head-branch: ${{ github.event.pull_request.base.ref }}

# release-contract: is this name really a tag, and does it name what we built?
- id: tag
  uses: abicheck/abicheck/actions/verify-baseline-source@<pinned-sha>
  with:
    mode: tag
    tag: ${{ github.event.workflow_run.head_branch }}
    built-sha: ${{ github.event.workflow_run.head_sha }}
```

Outputs: `outcome`, `eligible`, and (`producer-run`) `run-id`, `run-attempt`,
`head-sha`, `considered`, `rejected`; (`tag`) `commit-sha`, `annotated`.

Everything your shell established is preserved, with your reasoning intact:

* **Tags are resolved through `git/ref/tags/<name>` explicitly.** Your comment
  was right that the commits endpoint resolves a branch; that is now the
  implementation's own documented rule, with a test named for it.
* **Annotated tags are peeled**, and treating the tag-object sha as the commit
  is refused rather than silently "working".
* **No `v` prefix is assumed.** `1.5.2` is a first-class tag name.
* **The capture must belong to the tagged commit** (`built-sha`).
* **Exact head SHA alone is not eligibility** — repository, workflow, event,
  branch and conclusion are all checked, and each rejection is printed, so "no
  eligible baseline" can say what it *did* see.
* **`not_found` vs `lookup_failed` are distinct outcomes.** Your script kept
  `state=lookup-failed` / `not-found` / `artifact-unavailable` apart by hand;
  that distinction is now part of the contract.

New capability you should adopt: `required-jobs` names the build/test/capture
results a baseline depends on. A required job that **never ran** is reported
separately from one that failed. And `allow-unrelated-job-failures: true` is
the *explicit* policy for "a flaky platform leg may fail" — without it, a
failed run is never silently accepted, and it requires `required-jobs`, so
something always still checks that the baseline's own evidence exists.

This Action does **not** download the artifact or the release asset. Keep your
`actions/download-artifact` / `gh release download` steps, gated on
`steps.base.outputs.eligible` / `steps.tag.outputs.eligible`.

### 2.5 Publication — already supported, already in use

`abicheck-report.yml` is essentially finished: it is `verify-source-run` +
`report`, both upstream and pinned, and the security boundary it documents
(analysis unprivileged, publication trusted, recipient resolved from the API
never the artifact, head vs tested-merge SHA kept apart, producer-run ordering
rather than publisher-run ordering) is what those Actions implement. **No
change required.** Keep the default-branch deployment note; do not move to
`pull_request_target`.

---

## 3. Deletion map

| PVXS file | Upstream owner | Status |
|---|---|---|
| `.ci-local/abicheck-inputs.sh` (126 lines) | `actions/baseline` `library-spec` → `abicheck.frontends.action.library_selection` | **Delete**, after moving EPICS discovery + arch guard into the capture Action's own step (~20 lines stay) |
| `.ci-local/abicheck-snapshots.sh` (69) | `actions/resolve-baseline` `kind: members` → `abicheck.buildsource.baseline_set.resolve_target` | **Delete** |
| `.ci-local/abicheck-collect.sh` (63) | `actions/aggregate` → `abicheck.workflows.aggregate.collection` | **Delete** |
| `.ci-local/abicheck-validate-aggregate.sh` (65) | same | **Delete** |
| `.github/actions/abicheck-capture/action.yml` (74) | `actions/baseline` | **Shrink** to the EPICS/arch resolution + one `uses:` |
| `abicheck-baseline.yml` tag/eligibility shell (~45) | `actions/verify-baseline-source` | **Delete** |
| `ci-scripts-build.yml` baseline-staging shell (~60) | same | **Delete** the eligibility logic; keep the downloads |
| `ci-scripts-build.yml` collect + aggregate + validate steps (~40) | `actions/aggregate` | **Delete**, replaced by the declaration step + one `uses:` |
| `.github/actions/abicheck-publish-baseline/action.yml` (101) | — | **Keep for now.** See §6. |
| `abicheck-report.yml` (139) | already upstream | **Keep as is** |
| `.ci-local/abicheck.yml` | — | **Keep.** The C++17 extraction-context deviation is a real, disclosed PVXS decision; abicheck should not own it. |

**Measured** on a disposable checkout of PVXS PR #2 with the migration applied
(`git diff --stat` against `65d3f8d`):

```
 .ci-local/abicheck-collect.sh               |  63 ---------
 .ci-local/abicheck-components.json          |  15 ++
 .ci-local/abicheck-inputs.sh                | 126 -----------------
 .ci-local/abicheck-snapshots.sh             |  69 ----------
 .ci-local/abicheck-validate-aggregate.sh    |  65 ---------
 .github/actions/abicheck-capture/action.yml |  67 ++++++---
 .github/workflows/abicheck-baseline.yml     |  86 +++++-------
 .github/workflows/ci-scripts-build.yml      | 206 ++++++++++++----------------
 8 files changed, 188 insertions(+), 509 deletions(-)
```

**509 lines removed, 321 net** — and, more to the point, *all* hand-rolled ELF
inspection, manifest parsing, report-identity handling and aggregate-document
validation is gone. What is added back is declaration (the component JSON) and
`uses:` blocks.

That checkout was built only to measure and review this; **nothing was
committed or pushed to any PVXS repository**, as required. The full diff is
reproducible by applying §2's changes to `65d3f8d`; every hunk it contains is
quoted or described in §2 above.

### Irreducibly project-specific, and why

* **EPICS_BASE discovery** (`configure/RELEASE.local`) and **`EPICS_HOST_ARCH`
  resolution** — build-system facts. abicheck deliberately hard-codes no
  `pvxs`, no EPICS cache path, no profile name.
* **The component declaration** — which library owns which header is a product
  decision only PVXS can state. abicheck owns *resolving and validating* it.
* **Which checks an event asks for** — that `accepted-main` exists only on a
  pull request is PVXS's policy. The Action enforces that whatever you declare
  is reported completely; it does not decide what to declare.
* **Which revision/profile to compare against**, and the advisory gate itself.

---

## 4. Compatibility, trust and deployment

**Report/schema compatibility.** No schema changed. No report field was added
or removed. No exit code changed. `actions/aggregate` runs the same
`abicheck aggregate` you run today and produces the same document; the
publisher reads it unchanged. `resolve-baseline`'s new outputs are additive,
and `kind: target`/`kind: bundle` behaviour is bit-for-bit unchanged (a test
pins that the new outputs are present-but-empty on those paths).

**Baseline generations.** Nothing here invalidates an existing baseline-set.
`baseline_generation` is still caller-assigned; keep `'1'`. Your captures
before and after this change are mutually comparable.

**One behavioural change to be aware of:** with `library-spec`, a stale
`header_exclude` (matching nothing) is now a hard failure. If `iochooks.h` is
ever renamed or moved, the capture fails loudly instead of silently handing
its declarations back to `libpvxs`. That is the intended behaviour, and it is
the one way this could newly fail a build that previously passed.

**Trust and permissions — unchanged.** Analysis keeps `contents: read`, no
secrets, no write scope. `verify-baseline-source` needs `actions: read`
(and `contents: read` for `mode: tag`) — exactly what your staging steps need
today. Publication keeps `actions: read` + `pull-requests: write` in the
`workflow_run` job. Nothing here asks for a repository-wide permission change,
and nothing runs contributor-authored code with elevated permissions.

**Deployment prerequisites.** Pin every Action to the merge SHA. `workflow_run`
still only runs the default-branch copy of a workflow file — that constraint is
unchanged and is documented in
[`docs/use/multi-component-ci.md`](docs/use/multi-component-ci.md), the new
page covering this whole shape.

---

## 5. Tests actually executed

All on `claude/clever-meitner-2ja05m`, Linux, Python 3.13.

| Suite | Result |
|---|---|
| `tests/test_action_library_selection.py` (new, 30 tests) | pass, 0.4s |
| `tests/test_aggregate_collection.py` (new, 46) | pass, 0.5s |
| `tests/test_action_baseline_source.py` (new, 44 — incl. 5 driving `run.sh` against a scripted `gh`) | pass, 1.5s |
| `tests/test_action_aggregate.py` (new, 8) | pass, 1.7s |
| `tests/test_action_resolve_baseline.py` (+10 new, 51 total) | pass, 13.8s |
| `mypy abicheck/` | clean, 847 files (baseline 0 held) |
| `ruff check` / `ruff format --check` | clean |
| `scripts/check_ai_readiness.py` | no new errors |

**Non-vacuity.** The selection tests were mutation-checked rather than
assumed: disabling machine agreement, the artifact-escape check, the `ET_DYN`
check, the ambiguity check, and header exclusion (both the stale-pattern guard
and the loop itself) each killed between 1 and 16 tests. Resolved-identity
de-duplication kills 16 when removed.

**External-consumer acceptance**, from a disposable checkout outside the
abicheck tree, against two real compiled two-component installations with real
SONAME symlink chains:

```
capture old                  2.22s   two declared components, L2 only, no library build
capture new                  2.31s
resolve candidate members    0.32s   after relocation, with the build trees deleted
resolve baseline members     0.32s
compare libdemo              ~0.9s   exit 4  -> func_removed (real removal)
                                             + func_added (real compatible addition)
compare libdemohooks         ~0.9s   exit 0  -> NO_CHANGE (ownership held: the
                                             sibling's change did not leak)
collect                      0.19s
aggregate                    0.58s   compatibility-exit=4, REPORTED not raised
validate                     0.19s   status=fail coverage=partial
                                     channels={"accepted-main":{0 analyzed,2 unavailable},
                                               "release-contract":{2 analyzed,0 unavailable}}
```

**Independence** is proven in that run: step 2 deletes the build trees the
snapshots came from, and every comparison afterwards succeeds with no
re-extraction.

Negative controls executed against the real implementations: missing member,
duplicate member, wrong profile, wrong `project_ref`, wrong generation,
tampered snapshot content (`ambiguous`, digest mismatch), snapshot path
escaping the set, manifest inside the reports directory, stale
`aggregate.json` in the reports directory, malformed report, duplicate check
id, conflicting declared identity, aggregate document describing nothing,
declared-count mismatch, branch-as-tag, annotated tag unpeeled, tag/commit
mismatch, lookup-failed vs not-found, ineligible producer (failed / wrong
branch / wrong repo / wrong event / wrong workflow / incomplete), required job
failed, required job never ran, and required jobs unverifiable.

### Unsupported / untested here — read this before relying on it

* **No GitHub Actions runner was available.** Each Action's `action.yml`
  → `run.sh` wiring is covered by input-forwarding gates and by invoking
  `run.sh` as a real subprocess, and every *decision* is unit-tested with no
  credentials. The composite steps themselves have not executed on a runner.
  **Run each one once in a PVXS branch before deleting the shell it
  replaces.**
* **No real GitHub API call was made.** `verify-baseline-source`'s decisions
  are tested against hand-built API documents (the technique `run_selection.py`
  already uses), and its `run.sh` is additionally driven as a real subprocess
  against a **scripted `gh`** covering an annotated tag, a lightweight tag, a
  404 and a transport failure — so the `gh api` call shape, the peeling of an
  annotated tag, and the 404-vs-error discrimination are all exercised. What
  remains untested is only a *live* API: real response shapes and real error
  text. That is the highest-risk remaining surface, and it is why acceptance
  check 5 in §7 exists.

  One real bug was found and fixed this way: the first version read `gh`'s
  status with `$(gh ... ; echo $?)`, and command substitution inherits
  `set -e`, so a 404 aborted the step instead of reporting `not_a_tag`.
* **Linux/ELF only.** `library-spec` has no PE/Mach-O lane; pass
  `library-spec-require-elf: false` and select artifacts yourself there.
* **No PVXS build was run.** The acceptance fixture is a synthetic
  two-component C++ library that reproduces PVXS's *shape* (shared header
  tree, disjoint ownership, SONAME alias chains) — not PVXS itself.
* `/dev/null` was accidentally clobbered mid-session by a bad scratch command
  and restored; one fast-suite run overlapped that window and was re-run
  clean. Noted so a stray failing log is not mistaken for a real signal.

---

## 6. Not delivered

**`abicheck-publish-baseline/action.yml` still has no upstream replacement.**
Keep it.

`.github/workflows/publish-baseline.yml` upstream already implements the
immutability contract you want, in more depth than your action does —
identity-verified idempotent republish, fail-closed on a conflicting asset,
plus `project_ref`/`fact_set`/`baseline_generation`/schema/symlink checks. But
it is a `workflow_call` workflow that **captures** from `build-output.json`
artifacts; it cannot publish an already-captured baseline-set.

The right fix is to extend it with a pre-captured-set input so its ~400 lines
of upload logic are reused rather than copied. Writing a second Action here
would have created exactly the competing implementation this task forbids, and
I could not exercise that workflow in this environment, so I did not attempt
it blind. It is the top follow-up.

Two smaller gaps in your action that a future upstream owner should absorb,
because they are generic: validating that a set's `manifest.json` declares the
profile it is being published as, and that it covers the expected member set.
Neither is PVXS-specific.

## 7. Acceptance checks for PVXS after merge

Run in this order; each is meant to fail loudly if the upstream behaviour is
not what this document claims.

1. **Pin** every abicheck Action to the merge SHA.
2. **Capture parity.** Add the component declaration and switch the capture
   Action to `library-spec`, keeping `abicheck-inputs.sh` in place. Assert
   `resolved-libraries` names the same two artifacts and the same header sets
   the old script emitted, and that `machine` is `EM_X86_64`. Only then delete
   the script.
3. **Member resolution.** Replace `abicheck-snapshots.sh` with
   `kind: members`. Confirm both snapshots resolve **and** — this is new —
   that a deliberately wrong `expected-project-ref` yields
   `outcome: wrong_project_ref` on the candidate side. Update the
   `libpvxsioc` → `libpvxsIoc` key.
4. **Aggregation.** Switch to `actions/aggregate`. On a PR with a real
   surface change, confirm `compatibility-exit` is non-zero while the step
   **succeeds**, and that `channels` reports both channels. Then force one
   check to produce nothing and confirm `coverage: partial` with the check
   still listed.
5. **Baseline eligibility.** Switch both staging paths. Confirm a tag build
   publishes, a branch-named run does not, and that a PR whose base has no
   successful run reports `not_found` rather than an error or a clean pass.
6. **Publication unchanged.** Confirm the sticky comment still updates, still
   shows the tested merge SHA rather than the PR head, and that a re-run does
   not duplicate it.
7. **Only then** delete the four `.ci-local/abicheck-*.sh` scripts and shrink
   the capture Action.
