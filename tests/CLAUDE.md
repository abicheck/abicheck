# CLAUDE.md — `tests/`

~5400 unit tests across ~180 files. Most are fast and stdlib-only.

## Test markers

| Marker | What it needs | When to run |
|--------|---------------|-------------|
| *(default)* | Python only | always — `pytest -m "not integration and not libabigail and not abicc and not slow and not golden"` |
| `integration` | castxml + gcc/g++ | DWARF/ELF parsing changes |
| `libabigail` | abidiff + gcc/g++ | parity vs libabigail |
| `abicc` | `abi-compliance-checker` + gcc/g++ | parity vs ABICC |
| `msvc` | MSVC `cl.exe` (Windows) | MSVC+PDB end-to-end (`windows-msvc` CI lane) |
| `slow` | varies | hypothesis / property-based / perf — covered in CI on Linux/3.13 |
| `golden` | golden files in `tests/golden/` | output-format snapshots |

The default fast command excludes all external-tool markers. Use it. It
finishes in ~45 seconds.

## Test-quality guards (don't just chase coverage)

- **Detector oracle/metamorphic tests** — three files sharing one mutation
  catalogue (`_detector_mutations.py`, a non-`test_` helper):
  - `test_detector_oracle.py` (fast, deterministic) — applies each *known* ABI
    edit and asserts the exact `ChangeKind`, verdict severity, and that
    unrelated context stays unflagged. **In mutmut's scope** (not `slow`), so
    mutation testing measures these oracles.
  - `test_detector_properties.py` (`slow`) — wraps the same mutations in a
    Hypothesis-randomized context, plus structural properties (idempotence,
    determinism, emitted-kind partition) and grounding on committed real
    snapshots in `fixtures/`.
  - `test_detector_properties_integration.py` (`integration`) — same invariants
    on snapshots dumped from **real compiled binaries** (gcc + castxml).
  Independent-random snapshot pairs almost never share symbols, so they only
  exercise add/remove; the controlled-mutation design is what reaches the
  *modification* detectors.
- `test_fp_rate_gate.py` — mirrors `scripts/check_fp_rate.py`; per-case FP/FN
  checks under public-surface scoping (baselines 0/0).
- `test_mutation_score_gate.py` / `test_mutation_results.py` — the mutation
  gate's drift logic and its parsing/attribution primitives. The latter
  includes one *real* end-to-end `mutmut` run (marked `slow`), because the
  previous fixtures encoded a key format mutmut never emits and so passed
  against a parser that misread real output. `test_mutation_run_scoping.py`
  covers `--scope-run-to-diff` (restricting `mutmut run`'s own test-execution
  phase to the `only_mutate` module(s) a PR's diff actually touches, and
  refusing to scope at all whenever any `tests/` path is also touched or the
  measurement comes from a saved `--results-file`) — split into its own file
  so `test_mutation_score_gate.py` didn't grow further past the file-size
  soft limit. `test_mutation_per_module_scoping.py` covers the sibling gap in
  the *per-module baseline* gate (`check_per_module`): a scoped run never
  test-executes a mutant outside `scope_modules`, so comparing its
  (necessarily incomplete) survivor counts against the full baseline
  unconditionally let an out-of-scope module always read "still within
  baseline" — even one it never re-tested — silently missing a regression
  coupling could introduce (`only_mutate` modules import each other). Split
  out once `test_mutation_run_scoping.py` itself grew past the architecture
  gate's 1200-line test-file cap.
- `test_canonical_finding_id_completeness.py` — every `ChangeKind` must be
  classified for canonical identity, so an omission cannot be silent the way
  the #753 -> #759 escape was. Pins both directions: a declared type-bearing
  kind must be backend-spelling-stable, *and* distinct transitions must not
  collide (stability alone is satisfiable by hashing everything to a
  constant).
- **Silent-skip guard** (`conftest.py`): export `ABICHECK_MIN_EXECUTED=<n>` and
  the session fails unless ≥ n tests actually ran — used by the marker lanes in
  CI so a missing tool can't pass with 0 tests. Every `test_*` should assert
  something (the `test-assertion-density` AI-readiness check flags those that
  don't); pure smoke tests are allowed but should be deliberate.

## Conventions

- Use `assert` freely — no need for unittest-style methods.
- Prefer `pytest.mark.parametrize` over manual loops.
- Fixtures live in `conftest.py` and `tests/fixtures/`.
- Golden outputs live in `tests/golden/`; if you must regenerate, do so
  in a deliberate commit and document why.
- Mark tests that shell out (`gcc`, `castxml`, etc.) with the matching
  marker so default runs stay fast.

## Helpers

- `check_validate_results.py`, `summarize_validate_results.py` — used by
  `test_abi_examples.py` to validate example case ground truth.
- `conftest.py` — shared fixtures, including temp-dir helpers and
  binary-skip markers.
- `_strict_process.py` — `StrictProcessRunner`, the scripted stand-in for
  external-process invocation. **Use this instead of a hand-rolled
  `def fake_run(cmd, **kw)` that branches on a substring and falls through to a
  catch-all return.** A catch-all answers any command, so it cannot detect an
  extra call, a missing call, two calls in the wrong order, a dropped
  argument, or a fallback path reusing the previous command's fixture. Script
  the calls with `.expect(...)`, then `.assert_exhausted()`; failures print the
  full transcript of what actually ran. See `test_strict_process.py` for the
  contract and `test_bazel_root_targets.py` for a migrated call site.
- `canonical_identity_contract.py` — the exhaustive per-`ChangeKind` identity
  classification enforced by `test_canonical_finding_id_completeness.py`; a new
  `ChangeKind` fails CI until it is placed in a bucket.
- `_canonical_lane.py` — `is_canonical_lane()`/`canonical_python()`: whether
  the current interpreter is Linux + `repo_facts.json`'s `canonical_python`,
  for a module-level `pytestmark` that skips a platform/interpreter-
  independent test module on every other unit-test matrix leg (see
  `test_ai_readiness.py`'s own `pytestmark`). Deliberately degrades to a
  fixed fallback on any malformed `repo_facts.json` rather than raising,
  since it runs at *collection* time on every lane. Direct tests in
  `test_canonical_lane.py`, split out so the (already large) consumer module
  doesn't grow past the file-size cap just to host them.
- `_workflow_exec.py` — executes a workflow's `run:` steps for real, in a
  throwaway workspace with a real `$GITHUB_OUTPUT` and a sentinel tree *outside*
  it. Use it whenever a security property of a workflow step matters: asserting
  the step's *text* (which is what `test_reusable_workflows.py` does, and what
  it should keep doing as a cheap guard) proves nothing about behaviour under a
  hostile value — that is exactly how #705 shipped and #758 had to follow.
  `StepResult.output_lines` exposes the raw records, so an *injected extra*
  `$GITHUB_OUTPUT` line is visible and not just a wrong value. See
  `test_reusable_workflow_execution.py`.
## What NOT to do

- Don't change the marker scheme — CI gates depend on it.
- Don't read or regenerate `tests/golden/*` unless the output format
  intentionally changed.
- Don't add network-dependent tests.

## Scenario matrix for product invariants

Root `AGENTS.md`'s "Product decisions and change routing" section names
product rules that only hold if tests exercise the *combinations*, not one
happy path each. When adding or changing behavior in that space, cover the
relevant axes of this matrix (fixed examples plus a property-style
statement of the invariant, per the root file's bug-class guidance):

| Axis | States to exercise |
|---|---|
| Scope | one artifact · one-member package · multi-library package · selected variant against a multi-variant baseline · declared matrix with a missing cell |
| Evidence per side | binary only · + headers · + DWARF · asymmetric (one side richer) · requested-but-failed · stored rich snapshot under a shallow request |
| Policy state | none · suppression (per-finding and broad) · reclassification · scope exclusion · acknowledgment · relaxed versioning strictness |
| Consumers | none supplied · unaffected consumer · affected consumer · unavailable advisory/required consumer |

Invariants worth stating as properties over that matrix:

- **Raw-change conservation:** policy, view, and report-format changes
  never alter the observed change set or its evidence statuses; totals
  reconcile across scalar, bundle, aggregate, and compact reports.
- **Cardinality invariance:** a one-member package and the scalar path
  yield the same applicable findings; adding an unrelated baseline variant
  cannot change a selected comparison; input order cannot change pairing.
- **Front-end and format parity:** CLI, typed API, and Action resolve
  equivalent input to the same decision; JSON, Markdown, HTML, SARIF, and
  JUnit agree on every semantic field they carry.
- **No manufactured findings:** swapping *unavailable* evidence for *empty*
  evidence must fail a test; an unmatched member without inventory evidence
  is never a removal; a run with zero comparisons never asserts success.

Use real compiled fixtures (the `integration` marker, or committed
`fixtures/` snapshots dumped from real binaries) for anything whose truth
depends on compiler or binary facts, and the controlled-mutation catalogue
above for breadth. This file stays the scoped test-instruction owner;
don't add a parallel `tests/AGENTS.md`.
