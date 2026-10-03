---
doc_type: contributor
level: advanced
lifecycle: active
---

# Use-case path tracing: importance, relevance drift and path changes

**Tool:** `scripts/usecase_paths.py` (data: `scripts/usecase_flows.yaml`),
CI: `.github/workflows/usecase-paths.yml`. Report-only today.

## Why

Line coverage says whether *a test* reached a line. It does not say whether
any real use of abicheck does, how many use cases depend on a function, or
whether a change moved a use case onto different code. A first, hand-run
measurement (the real CLI over the catalog under coverage) found about 10k
lines that no command or documented API reached, and several building
blocks with two implementations answering one question differently. This
plan turns that one-off measurement into a repeatable process with three
goals:

1. **Importance.** Code on the paths of known use cases matters more and
   should be tested and reviewed harder than code no use case runs.
2. **Relevance drift.** Code that stops being reached is a signal to look,
   not a deletion list.
3. **Path changes.** A change that routes a use case through different
   code is visible even when every test passes.

## How it works

`record` runs each use-case source under coverage, one coverage context
per run, and maps every executed line to the function whose **body** ran.
The `def` line, decorators and default arguments run at import and are
never counted: counting them made every function of every imported module
read as reached (`tests/test_usecase_paths.py` checks attribution against
generated code that logs its own calls). A one-line `def f(): body` shares
its `def` line and is left unattributed -- 33 of about 9,300 functions,
almost all protocol stubs.

Two sources, each run tagged with a `docs/contribute/usecase-registry.yaml`
id:

| Source | What runs | Cost | Reaches |
|---|---|---|---|
| `scenarios` | the 29 automated scenarios of `tests/scenarios/*.yaml` | ~15 s, no compiler | policy, reporting, snapshot storage |
| `flows` | 11 real CLI command lines x 12 catalog cases (`usecase_flows.yaml`) | ~4 min plus the catalog build | also binary readers, both header frontends, `compat`, `deps` |

`rank` places each function in a tier by distinct use cases reaching it:
`shared` (3 or more), `use-case` (1-2), `unreached`. `--unit-coverage`
joins a unit-suite coverage file and lists use-case code whose own unit
coverage is low.

`diff BASE HEAD` (with `--git-base`) reports, in this order:

- the functions the change edits, most relied-on first -- where review and
  test effort should go (goal 1);
- functions some use case reached on base and none reaches on head, split
  into still-defined (look here) and removed/renamed (goal 2);
- runs whose path changed, grouped by the change they share (goal 3);
- newly reached functions, and runs that failed.

`dead RECORDING` classifies every unreached function by production
reference (`scripts/production_references.py`): **dead** when every
reference to its name in `abicheck/`, `scripts/`, `action/`, `actions/`,
`.github/` or `pyproject.toml` lies inside another dead function (a
greatest fixpoint), with documented API and ADR/plan-named functions listed
apart and treated as roots. It is the recomputed form of the hand-made list
[dead-code-and-single-owner](dead-code-and-single-owner.md) started from.

`record --root` measures another checkout with this tool, so CI records
the PR base and head with one recorder.

## First measurements

On `main` at the time of writing, scenarios plus flows (153 runs):

| Tier | Functions |
|---|---|
| shared | 2,418 |
| use-case | 1,096 |
| unreached | 5,772 |

Recording `main~5` against `main` showed exactly the functions those five
commits introduced entering scenario paths (six distinct changes over 29
runs), and listed the 96 functions they edited with 25 in the shared tier
(`checker.compare`, the snapshot encoder, the fact codec among them).

The flows source also caught a live defect on `main`: `compat` could not
read back a dump it wrote under a `.dump` name (fixed in PR #1448).

## Status and next steps

Landed: the recorder, the four reports, the PR and weekly workflow, and
the attribution, diff and dead-code fixpoint tests.

Open, in order:

1. **Widen the sources.** Flows for the composite Action, PR-comment
   rendering, `post_manifest`, `debian_symbols`, the hybrid frontend and a
   multi-library release directory, which no recorded run reaches yet.
2. **Windows and macOS.** PE, Mach-O and PDB readers read as unreached on
   Linux only because no such toolchain runs there; record the flows on
   those runners and merge the recordings before trusting their tier.
3. **Make tiers act.** Once the ranking is stable over a few weeks: a
   shared-tier function edited without a test change gets a PR notice, and
   the mutation lane prioritises shared-tier modules. Both stay advisory
   until the false-positive rate is known.
4. **Order, not only membership.** A path is a set of functions today. If
   set membership proves too coarse (same functions, different order or
   branch), record branch arcs as well.
5. **Gate.** `diff --fail-on dropped|changed|failed` exists; turning any of
   them on is a decision for after the report has run on real PRs.
