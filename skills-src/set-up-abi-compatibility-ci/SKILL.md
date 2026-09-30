---
name: set-up-abi-compatibility-ci
description: Set up automated ABI compatibility checking for a C/C++ shared library in a GitHub repository's GitHub Actions CI. Use when asked to add, enable, integrate, or configure ABI checking or a breaking-change gate in GitHub Actions, to onboard a native library project onto continuous compatibility checking, to make pull requests fail when they break the library's ABI, or to repair an existing ABI-checking workflow that never catches anything, always fails, or compares against the wrong baseline. Inspects the repository's build, headers, libraries, and release process, chooses a baseline strategy, and writes a working, pinned, least-privilege workflow with a staged rollout.
license: Apache-2.0
metadata:
  abicheck-version-range: ">=0.6.0,<0.7.0"
  layer: A
  source: skills-src/set-up-abi-compatibility-ci/SKILL.md
---

# Setting up compatibility checking in GitHub Actions

Goal: every pull request is checked against what the library's users already
have, with no one remembering to run anything. A workflow that parses the
wrong headers, compares against nothing real, or can never fail is worse than
none. The deliverable is working workflow files plus
[the setup report](#what-you-hand-back).

Three [safety invariants](../shared/safety-invariants.md) govern this job
(the rest concern reviews, not setup): never re-point a baseline to make a
check green (7); never add or widen a suppression to quiet the first run (6);
the request authorizes workflow files, not edits to the build — ask before
touching `CMakeLists.txt` and the like (8).

Scope: C/C++ **shared** libraries, Linux ELF, GCC/Clang, GitHub-hosted
`ubuntu-*` runners, the `abicheck/abicheck` Action. Anything else (macOS/
Windows, static- or header-only, self-hosted) may work — say it is outside
the evaluated scope.

## Step 0 — Preflight

1. It is a GitHub repository (`github.com` remote or `.github/`); other CI
   systems are out of scope.
2. If `abicheck` is installed, check `abicheck --version` against this
   skill's declared version range (`metadata.abicheck-version-range`). A
   missing local install is fine — the Action installs its own; don't
   install it yourself.
3. `grep -ril 'abicheck\|abi-compliance\|abidiff' .github/` — an existing
   check means **repair it in place**, not add a second one; read
   [repairing an existing check](references/pitfalls.md#repairing-an-existing-check).

## Step 1 — Inventory (read the repo; ask only what it cannot tell you)

- **Build commands** — reuse the existing CI workflow's steps verbatim.
- **Shared targets and their output paths** — one check per shipped library
  (matrix); a static-by-default CMake project needs `-DBUILD_SHARED_LIBS=ON`
  in the check build.
- **Public headers** — the installed directory (`install(DIRECTORY include/`,
  `PUBLIC_HEADER`, `include_HEADERS`), never `src/` or the repo root.
- **C or C++** — the Action defaults to `lang: c++`; C needs `lang: c`.
- **Releases** (tags, GitHub Releases) — decides the baseline (step 2).
- **Supported platforms / minimum glibc** ("RHEL 8", "Ubuntu 20.04",
  manylinux) — must be declared (step 3), or floor raises pass green.
- **API shape** — inline functions, templates, macros, default arguments in
  public headers, or "broke users without changing a symbol" → `source` depth.
- **Toolchain** — cross-compilation, Bazel/Autotools, clang-only, several
  profiles → [depth, toolchain, floors](references/depth-toolchain-and-floors.md).
- **Fork contributors** — their PRs get no comment from this job.

## Step 2 — Baseline

| Situation | Strategy | Read |
|---|---|---|
| Tagged/GitHub Releases, compatibility promised with what shipped | **Release snapshot**: release workflow dumps `<lib>.abicheck.json`, attaches it; PRs use `abi-baseline: latest-release` | [template A](references/template-release-baseline.md) |
| No releases, or a zero-infrastructure start | **Merge-base build**: PR job also builds the base commit; each side with its own headers | [template B](references/template-merge-base.md) |
| Snapshot already committed / wanted | **Committed snapshot** + refresh rule | [template C](references/template-committed-snapshot.md) |

Read only the template you chose. Recommend a strategy and say why; the user
decides. Both sides must be built identically
([comparability](../shared/baseline-and-comparability.md)).

## Step 3 — Gate decisions (defaults in parentheses)

1. **Rollout** (advisory first: `fail-on-breaking: 'false'`, PR comment on;
   state when it flips to blocking).
2. **Binary break** blocks; **API-only break** (`fail-on-api-break`) blocks
   for SDKs whose users recompile, else warns.
3. **Intentional breaks** — a label relaxes `fail-on-breaking` for that PR;
   the comparison still runs.
4. **Depth** (`headers`). `source` needs clang, a compile DB and source
   evidence on the baseline side too — see
   [depth](references/depth-toolchain-and-floors.md#2-evidence-depth-headers-l2-vs-buildsource-l3l5).
5. **Runtime floors** — declare stated floors in `.abicheck.yml`
   (`deployment.runtime_floors`); undeclared, a glibc raise is only a
   warning. See [floors](references/depth-toolchain-and-floors.md#1-runtime-and-dependency-floors-glibc-libstdc).
6. **Policy** (`strict_abi`); see
   [policies](../shared/policies-and-suppressions.md) only if the contract is
   an SDK or plugin ABI.

## Step 4 — Write the workflow (every rule below is required)

- `abicheck/abicheck@v0.6.0` — exact tag. In a job holding `contents: write`
  or `security-events: write`, pin **every** action to a commit SHA with the
  tag in a comment, resolved only with:
  `git ls-remote https://github.com/<owner>/<repo> 'refs/tags/<tag>' 'refs/tags/<tag>^{}' | sort -k2 | tail -1 | cut -f1`
  (an annotated tag's plain line is the tag object, which `uses:` cannot run;
  never type a SHA from memory).
- `permissions:` per job: `contents: read`; `pull-requests: write` for the
  comment; `contents: write` only in the release-upload job.
- `pull_request`, never `pull_request_target`; offer
  [fork-PR reporting](../../docs/use/fork-pr-reporting.md) if forks
  contribute.
- abicheck runs **through the Action** on both sides (`mode: dump` for
  snapshots). No `pip install abicheck` + `apt-get install castxml`: distro
  CastXML is below the supported range and is refused.
- Both sides built the same way, with debug info (`RelWithDebInfo`/`-g`),
  pinned runner image (`ubuntu-24.04`).
- Exact library path (no globs), public header directory, correct `lang`.
- Snapshot names end in `.abicheck.json`, one per library.
- `concurrency:` cancelling superseded runs; no top-level `paths:` filter on
  a check that will become required.
- Its own file (`abi-check.yml`, plus `abi-baseline.yml` for strategy A).

Add an `.abicheck.yml` (via `build-config`) only for a real decision
(floors, severity), never to restate defaults.

## Step 5 — Validate

1. YAML parses (`actionlint` if present); every Action input exists in the
   [inputs reference](../../docs/reference/github-action-inputs.md).
2. The library and header paths exist after your build commands (build
   locally if cheap, else trace the build files).
3. If abicheck is installed, run the equivalent `abicheck compare old.so
   new.so --header old=old/include --header new=new/include` once.
4. Say the workflow has not run on GitHub yet, and what its first run shows.

## Termination criteria

Done when every shipped shared library is checked (or the gap is stated),
the baseline and its bootstrap/refresh are automated or written as required
actions, the gate state and its flip condition are explicit, step 5 ran, and
the report is given. Ask instead of guessing when the repository cannot tell
you which library is public, where its headers are, or how it is built.

## What you hand back

Short; the files are the product:

```
Files:        …
Libraries:    libfoo (build/libfoo.so, include/foo/, C)
Baseline:     release snapshot | merge-base | committed — why; bootstrap/refresh
Depth/floors: headers|source; floors declared: GLIBC x.y | none stated
Gate:         advisory until … | blocking; API break: blocks|warns; label: …
Validated:    what ran; what could not
Not covered:  fork comments, platforms, depth limits
Next:         e.g. make it a required check after N clean runs
```
