---
doc_type: contributor
level: advanced
lifecycle: active
---

# Bug-class regression testing — closing the escape pattern behind the fix-history audit

**Origin:** An audit of the merged-fix history through `327df7b` (post-#883),
covering the issue-recorded bugs, the six real-world oneAPI defects in
issues #833–#838, and the escape sequences the bug-fix test contract's own
docstring already names (#699→#721, #753→#759, #705→#758). Asked one
question: for a fix whose PR *did* answer the bug-fix test contract in
good faith, why did a sibling defect in the same mechanism still escape?

**ADR:** none dedicated; this plan operationalizes AGENTS.md's own
"Fix the cause, not the instance" principle and extends the existing
bug-fix test contract (`scripts/check_bugfix_test_contract.py`,
`.github/PULL_REQUEST_TEMPLATE.md`) rather than replacing it.

**Type:** Initiative plan (cross-cutting, process + test infrastructure).
Touches the bug-fix contract tooling, `tests/regressions/` (new), and
adds generalized/metamorphic test suites across several existing test
files named per bug class below.

**Effort:** XL (phased, no fixed deadline — per AGENTS.md's
"time estimates are not a factor" principle, phases are ordered by
expected defect-catching value, not by size). **Risk:** additive
throughout — no phase changes production behavior; every phase adds
tests, a small amount of gate tooling, and one documentation change.

## Problem

The bug-fix test contract (`scripts/check_bugfix_test_contract.py`) already
asks a PR to name a "Bug class" and a "General invariant," not just a
reproducer for the one reported input — and it is unusually effective at
this compared to a typical repository's bug-fix discipline. Reading through
the fix history anyway turns up a consistent residual pattern: the
regression test proves the *reported* input is now handled correctly, and
the class is described in prose (the PR body, or a "Known gaps" entry in
AGENTS.md), but nothing in the test suite *exercises* the class — only the
one instance. The next escape is then never a repeat of the fixed input; it
is a sibling case the same mechanism still gets wrong:

* **#699 → #721** — the zstd compressed-snapshot window size was computed
  with the wrong unit. The shipped test asserted the formula was
  self-consistent with itself (a tautology against the bug's own wrong
  formula) and used a toy-scale, highly-compressible fixture whose actual
  required window never approached the size where the bug manifests. Both
  passed identically before and after the regression.
* **#753 → #759** — three `ChangeKind` entries were missing from a
  hand-maintained classification list. Nothing failed anywhere; the
  associated findings simply vanished from `canonical_finding_id`. The
  fix added the three entries; a *fourth* omission would have been caught
  by nothing the fix added, only by the later-introduced exhaustiveness
  gate (`tests/test_canonical_finding_id_completeness.py`).
* **#705 → #758** — a GitHub Actions injection risk was defended by
  asserting the *text* of a workflow/shell file, not by executing it
  against a hostile workspace. The corrective test runs the real shell
  against adversarial input and checks for the absence of a side effect.
* **#833–#839** (this branch's own history, see AGENTS.md's "Known gaps"
  entries) — an AST wrapper-chain traversal bug, a public-surface
  reachability heuristic, and a checkout-path-tainted identity bug each
  needed *several* follow-up review rounds on the *same* function before
  the underlying invariant was fully closed, because each fix's own test
  covered only the shape of AST/path/config the report happened to
  contain.

None of these needed a cleverer reviewer at fix time. They needed the fix's
test to state the invariant as something *executable and adversarially
generated*, not as a fixed-input assertion plus a paragraph of prose. This
plan turns that observation into (1) a small, immediate change to what
"regression test" means in this repository's process, and (2) a phased set
of generalized test suites closing the specific classes the audit found
still open.

## Goal & acceptance criteria

1. The bug-fix test contract's definition of a passing regression test is
   changed from "a test that fails on base and passes on head for the
   reported input" to "an executable, named bug-class invariant, tested
   with generated/adversarial inputs beyond the reported one, that fails
   on base and passes on head." Phase 0 itself is a documentation and
   PR-template change, deliberately not a rewrite of the enforcement
   script's structural half — see the Phase 0 section below for one
   correctness fix later folded into this same PR that *does* touch the
   structural half, and why that is a different kind of change from
   Phase 0's own scope.
2. A durable **regression-class registry** exists (`tests/regressions/`)
   naming each bug class this audit found, its invariant, its test(s),
   and which axes/entry points/backends it is verified across — so a
   future contributor (or agent) can check "does a test already cover
   this class" before writing a fifth narrow reproducer for the same
   mechanism.
3. Each bug class identified below and now registered in
   `tests/regressions/manifest.py` (`BUG_CLASSES`/`all_ids()` is the
   source of truth for the current count and membership — not restated
   here) has at least one generalized test — property-based, metamorphic,
   or exhaustive —
   that is shown to catch a deliberately reintroduced instance of the
   class's own already-fixed bug (not just the literal historical
   input).
4. No phase changes production behavior; every phase is additive
   (new tests, a new registry file, one documentation update per phase).

## Design

### Two changes, not one

The user's request separates into two deliverables, and they are sized very
differently on purpose:

* **Process change (implemented):** stop treating "a test for the
  reported input, plus a prose description of the class" as sufficient.
  The bug-fix contract already *asks* for a bug class and a general
  invariant (`.github/PULL_REQUEST_TEMPLATE.md`'s "Bug class" /
  "General invariant" rows) — what changes is what counts as having
  answered them: the invariant must be backed by a test that generates
  or enumerates inputs beyond the one reported, not only restated as
  prose. See "Phase 0" below for the exact wording change.
* **Implementation plan (this document, phased):** close the specific
  generalized-test gaps the audit found across the whole codebase. This
  is real, multi-phase engineering work and is *not* attempted in one
  PR — each phase below is its own scoped slice with its own tests,
  reviewed and merged independently, the same way every other XL
  initiative plan in this directory is phased.

### Why a registry, not just "more property tests"

The audit's structural finding (see "Coverage gaps found," phase table
below) is not "there aren't enough property tests" — this repository
already has an unusually strong property/mutation-testing base
(`tests/test_detector_properties.py`, the per-PR diff-scoped mutation
gate, `tests/canonical_identity_contract.py`'s exhaustiveness rule). The
gap is that a test's *relationship to a bug class* is not recorded
anywhere queryable: nothing currently answers "which bug classes have a
generalized test, across which axes, with which known residual gaps" —
so a class can be silently under-covered (one property test, one axis,
one backend) while looking, from a coverage-percentage or CI-green view,
completely fine. `tests/regressions/manifest.py` (a plain, importable,
pure-Python data structure — matching every other machine-checked
registry convention in this codebase, e.g. `evidence_tiers.py`,
`platform_capabilities.py`) records, per bug class:

```python
BugClass(
    id="identity.environment_taint",
    invariant=(
        "Canonical identity (finding IDs, type/function identity keys) "
        "does not depend on checkout root, absolute path spelling, "
        "temp-directory location, or unrelated line-number drift."
    ),
    fixed_by=(837, 843, 846, 868),           # issue/PR numbers, for traceability
    seed_tests=("tests/test_castxml_anonymous_type_location.py",),
    public_surfaces=(),                       # () until a seed genuinely invokes one
    axes={},                                  # {} until a seed genuinely covers an axis
    known_gaps=(),                            # each entry names a reference + optional canary
)
```

(This is the shape the implemented `BugClass`/`KnownGap` dataclasses in
`tests/regressions/manifest.py` actually carry — see that module for the
authoritative field list and each field's own docstring, not this
illustrative snippet, if the two ever disagree.)

`tests/test_regressions_manifest.py` (Phase 1) enforces the registry's own
integrity mechanically — every named `seed_tests` path exists and is a
real, pytest-collected `test_*.py` file, every `known_gaps` entry names a
non-empty reference, and, when a `known_gaps` entry sets `canary_test`
(optional — many current entries deliberately leave it `None`, an honest
"tracked, not yet monitored by a canary" rather than a fabricated pointer),
that path resolves the same way — the same "a registry entry is checked,
not just written" discipline `check_ai_readiness.py`'s `changekind-*`
checks already apply to `ChangeKind`. It does **not** replace or duplicate
the bug-fix
contract's PR-time gate; it is the durable, cross-PR index that gate's
per-PR answers accumulate into. A future `fix:` PR's "Bug class" field
should, where the class already exists in the registry, name the existing
`id` rather than inventing new prose — and where it's genuinely new,
add an entry (Phase 0's contract wording makes this explicit).

### Independent oracles, not the production formula restated

Several of the audit's most damaging examples (#721's zstd window-size
tautology, #879's deduplication-key collisions) share one root cause: the
test computed its expected value with the same formula, or the same
production helper, that the implementation uses. A property test that
regenerates the bug it's meant to catch is not a safety net. Every
generalized test added under this plan states, in its own docstring or a
one-line comment, which of the following its *oracle* is — matching the
repository's own existing convention for this
(`docs/contribute/adr/`-referenced fact-conservation tests, and
`tests/test_clang_header_backend_integration.py`'s existing CastXML-
parity-oracle style):

* an independent, structurally different implementation (a
  from-scratch tree walker, a graph-reachability computation, a
  round-trip through the real third-party library);
* a real external tool run directly (a real compiler, `castxml`, the
  actual shell, `abidiff`/`abi-compliance-checker` where already wired);
* an explicit, hand-verified truth table (for a small finite domain);
* a metamorphic relation, verified against the SUT's own output under
  a semantics-preserving transformation, deliberately with no oracle
  needed for the untransformed baseline (round-trip equality, invariance
  under relocation, etc.).

A test with no stated oracle, computing "expected" from the same helper
under test, does not satisfy Phase 0's contract wording change.

### Killing known-bad mutants, not just running Hypothesis N times

Issue #879's own history (a property strategy that never generated tuples,
mappings, or `NaN`, so a real collision escaped two review rounds) is the
concrete argument for a rule already present in spirit in this
repository's own "Primitive-level property tests" AGENTS.md section: for
each generalized suite added under Phases 2–9 below, include at least one
deliberately-reintroduced historical-bug mutant (a hand-written
known-bad implementation, or a `pytest.mark.parametrize`d "does this
generator produce a witness against this specific known-bad shape"
sub-test) that the suite is shown to kill. This is cheap — it is usually
the literal original bug, restored — and it is the only mechanical
defense against a generator that looks thorough but never actually
reaches the input shape a real bug needs.

## Coverage gaps found, by bug class (phased implementation)

Each phase is independently scoped, independently mergeable, and does not
block the others. Ordered by expected value (how many historical/likely
escapes the class explains) rather than by size — per AGENTS.md's
decision-making principles, size/time is not a sequencing criterion here,
expected defect-catching value is.

### Phase 0 — process change (implemented)

* Reword the bug-fix test contract's "General invariant" row
  (`.github/PULL_REQUEST_TEMPLATE.md`) and the enforcement script's
  matching guidance text (`scripts/check_bugfix_test_contract.py`) to
  require the invariant be backed by a *generalized* test (generated/
  adversarial inputs beyond the one reported, or an exhaustive
  enumeration for a small domain) — not a single fixed-input assertion
  plus prose. Points at this plan file and, now that Phase 1 has landed,
  at `tests/regressions/manifest.py` for "does this class already have a
  home." (The "Regression test fails on base" row is left as-is — it
  asks whether the *named* test failed pre-fix, a question orthogonal to
  how many inputs that test covers, so it doesn't need this reword.)
* Add the corresponding normative statement to `AGENTS.md`'s
  "Decision-making principles" section, alongside the existing
  "Fix the cause, not the instance" bullet it already extends.
* Phase 0's own design keeps the structural half (code change ⇒ test
  change) untouched, redefining only the declared half's required
  *content*. **One correctness fix landed in the same PR does change the
  structural half, and is worth distinguishing from Phase 0's own
  scope**: `tests/regressions/manifest.py` (Phase 1, below) is itself a
  `.py` file under `tests/`, and `adds_or_modifies_a_test()`'s pre-
  existing `is_test_path()` check credited *any* non-prose file there as
  test evidence — so a PR that only added a `BugClass` entry, with no
  `seed_tests` path actually touched, passed the structural gate with
  zero executable test evidence (found by review on this PR itself, once
  the registry file existed to expose it). Fixed by requiring a `.py`
  path to look like a file pytest actually collects (`test_*.py`/
  `*_test.py`), not just live under `tests/`. This is a bug fix to an
  existing loophole, not part of Phase 0's declared-half redefinition —
  it would have needed fixing whenever the first non-test `.py` support
  module landed under `tests/`, regardless of this plan. A stricter,
  automatically enforced version of the declared-half question (does the
  named test actually generate more than one input; does it name an
  oracle) is left for a later, optional `check_regressions_manifest.py`
  CI gate once the registry has enough entries to make false positives
  rare (see Phase 1 below) — deliberately
  not built yet, so this phase ships without new CI surface.

### Phase 1 — regression-class registry + manifest gate (implemented)

* `tests/regressions/manifest.py` — the `BugClass`/`KnownGap` registry
  described above, seeded with the classes this plan names below (see
  `BUG_CLASSES`/`all_ids()` for the current, authoritative count and
  membership — not a number restated here), each pointing at the real
  generalized/property test(s) that already exist
  for it (see the "Coverage gaps found" sections below for what each
  class's tests still need to grow into) and, where applicable, a
  `known_gaps` entry recording a residual this registry does not yet
  claim to close.
* `tests/test_regressions_manifest.py` — integrity checks: every
  registered id is unique and dotted, every class states a non-empty
  invariant and traces to at least one real fix, every `seed_tests`/
  `known_gaps.canary_test` path resolves to a real, `test_`-prefixed
  file under `tests/` (so a stale or typo'd path fails the suite instead
  of silently reading as verified coverage), and the lookup helpers
  (`get`/`all_ids`) round-trip against the registry.
* `scripts/check_regressions_manifest.py` (optional CI wiring, e.g. a
  soft nudge when a `fix:` PR's "Bug class" answer matches a registered
  id but that class's own test set didn't change) — **not built in this
  phase**, deliberately: with only a handful of entries (see
  `BUG_CLASSES`/`all_ids()` for the current count) the false-positive
  risk of an automated nudge isn't worth it yet; revisit once the
  registry has grown from later phases' work.

### Phase 2 — AST wrapper-chain traversal invariant

Generalizes the fix behind #839 (enum value on an intermediate
`ConstantExpr` node, not the outer/leaf node the parser checked).

* **Invariant:** extracting a semantic value from a clang/castxml AST
  subtree gives the same answer regardless of which semantics-preserving
  wrapper nodes (implicit casts, parens, constant-folding wrappers) sit
  between the declaration and its value.
* **Oracle:** an independent, from-scratch tree-walking reference
  implementation, not the production helper under test.
* **Generator:** arbitrary single-child wrapper chains, value at root/
  middle/leaf, several equivalent value encodings, malformed/missing
  children, extra irrelevant metadata — reusing and generalizing the
  wrapper-chain generator already added for #839
  (`tests/test_dumper_clang_enum_value_properties.py`, the real seed test
  named in `tests/regressions/manifest.py`'s
  `extraction.ast_wrapper_chain_traversal` entry) so every "unwrap until X"
  helper in `dumper_clang.py`/`dumper_castxml.py` (not just the enum-value
  path) is checked against the same generator, per that file's own
  docstring intent.
* **Negative control:** a malformed tree must produce a typed
  incomplete-analysis result, never a fabricated or silently-dropped
  value.

### Phase 3 — public-surface reachability invariant

Generalizes #235 → #834 → #835 → #843's sequence (public-surface
classification by path/name heuristic rather than include-graph
reachability + export evidence).

* **Invariant:** a declaration's public/private classification is a
  function of reachability from an explicit public root through the
  real include graph, language visibility, and export-table evidence —
  never directory containment or name-shape alone.
* **Oracle:** an independently computed graph-reachability answer over a
  generated include DAG model (roots, `-I`/`-isystem`/`/I`/
  `/external:I`/`-idirafter`, cycles, symlinks, generated headers,
  toolchain headers, duplicate basenames).
* **Metamorphic properties:** relocating the checkout, adding an unused
  include directory, and normalizing `.`/`..` must not change any
  classification.
* **Cross-backend:** the same generated scenario run through both
  castxml and clang (skip cleanly where a backend is unavailable in the
  current environment, matching this repo's existing `integration`
  marker discipline).
* **Known residuals to encode as tracked canaries, not silent gaps:**
  the direct-clang path-normalization gap and the nested/anonymous-
  namespace record gaps AGENTS.md's "Known gaps" section already
  documents from #843 — each needs an `xfail`-with-issue-link canary in
  this suite per Phase 0's registry rule, not a silent absence.

### Phase 4 — path/checkout identity-taint metamorphic suite

Generalizes #837 → #843 → #846 → #868 (absolute paths, checkout root,
and closure/anonymous-type discriminators leaking into ABI identity).

* **Invariant:** canonical identity (finding IDs, type/function identity
  keys, node IDs) is a function of semantic scope and source identity —
  never of checkout root, absolute path spelling, temp-directory
  location, path separator, or unrelated line/column drift from an
  edit elsewhere in the file.
* **Metamorphic transformations:** relocate checkout root, symlink root,
  change path separator, insert/remove blank lines and comments,
  reorder unrelated declarations, change compilation-database root,
  change archive member order.
* **Property:** `canonical_snapshot(base) == canonical_snapshot(transformed)`
  and `verdict(base, transformed) == NO_CHANGE`, for every transformation.
* **Negative control (do not over-merge):** two distinct local entities
  with similar source shapes (two lambdas in the same header, two
  same-named nested records in different namespaces) must remain
  distinct identities under every transformation above — this is the
  control that would have caught the direction of the reverted
  name-shape-heuristic attempts recorded in AGENTS.md's "using-
  declaration" known-gap entry, and should reuse that entry's own
  counterexamples as fixed regression cases within the generated suite.
* **L5 residual:** AGENTS.md already documents that the L5 source
  graph's own node identities are not renumbered alongside the flat
  snapshot's closure markers — encode as a tracked canary per Phase 0.

### Phase 5 — deduplication/matching-key soundness properties

Generalizes #753→#759 (registry) and #879 (key collisions with
starved property generators).

* **Invariant (four properties, each independently tested):** for every
  key used in matching, grouping, or deduplication — totality (every
  producer-valid value yields a key), determinism, injectivity for
  *semantically distinct* findings (not "no two unequal values ever
  collide" — a batch/library-level finding samples an arbitrary
  affected export as its "spokesperson", so two `Change` instances
  differing only in which export was sampled describe the same logical
  event and must collide by design; see
  `test_finding_identity_properties.py`'s own
  `TestBatchShapedChangeIgnoresTheSample`), and order-invariance for
  unordered inputs.
* **Generator:** recursive values including tuples/lists/sets/dicts in
  varying insertion order, `NaN`/signed-zero/infinities, structurally
  equal copies, and objects with identical `repr()` but different
  identity — precisely the shapes #879's own post-mortem names as
  missing from the first attempt.
* **Mutant-killing requirement (Phase design principle, above):**
  the suite must be shown to reject at minimum: `repr(value)` as a key,
  untagged list→tuple coercion, endpoint-only normalization, and
  order-sensitive dict encoding — four deliberately-bad
  implementations, each standing in for a real historical near-miss.
* **Explicit must-merge / must-not-merge pairs**, not only generated
  ones: the existing merge primitives this repository's own
  "Primitive-level property tests" section already documents
  (`_paired_stable_indices`, constant/type identity fallbacks) get
  their pairs promoted into this suite's fixed corpus rather than
  living only in each primitive's own scattered test file.

### Phase 6 — configuration-propagation matrix

Generalizes #860 and #883 (an option accepted at one public entry point
silently not reaching a sibling path — `consumer_compile`, `policy_file`
on bundle-level findings, a safety budget applied to archive input but
not equivalent plain JSON).

* **Invariant:** an accepted configuration value either reaches every
  relevant consumer with identical semantics, or is rejected at the
  public boundary — no third state.
* **Mechanism:** a sentinel value per configurable concern (policy/
  policy-file, frontend/compiler, include roots, evidence-pack/target
  attribution, safety budgets, suppression/filtering, consumer compile
  context, per-library override, output/report options), threaded
  through every public entry point this repository documents
  (Python API, CLI, composite Action, reusable workflow, single-binary
  compare, project/multi-library `compare`, live snapshot, stored
  plain/compressed/archive snapshot, bundle) — asserting the *exact*
  expected consumer set receives it, not merely that some mock was
  called.
* **State distinctions:** omitted / explicit `None` / explicit empty /
  default / explicit-equal-to-default must be tested as five distinct
  cases, since #860/#883's own root causes conflated some of these.
* **Mutation check:** deliberately removing one forwarding edge in a
  copy of the matrix harness must make the suite fail and name the
  missing path — proving the harness would have caught #860/#883
  before merge, not only after.

### Phase 7 — storage/third-party dependency contract tests

Generalizes #699→#721 (zstd window-size unit bug hidden by a
self-referential formula test and a toy-scale fixture) and #883's
archive-vs-plain-JSON budget asymmetry.

* **Invariant:** `read(write(x)) == x` at realistic production scale for
  every supported algorithm (not just the one with a known incident);
  archive and plain-JSON inputs receive equivalent safety-limit
  treatment; a resource-limit failure is attributed to the correct typed
  cause, never surfaced as a generic parse/format error.
* **Oracle:** computed independently of the production formula — either
  by interrogating the real dependency directly, or via a hand-derived
  reference calculation checked into the test as a comment showing its
  derivation.
* **Scale requirement:** at least one test per algorithm at a byte scale
  realistic enough to trigger the boundary condition being defended —
  this repository's own "Third-party-boundary tests" AGENTS.md section
  already states this rule for compression; this phase is the
  generalization to every other storage/third-party boundary
  (`snapshot_cache.py`, archive/bundle readers, the JSON safety-budget
  code #883 touched).

### Phase 8 — shell/workflow injection execution tests

Generalizes #705→#758 (text-assertion instead of execution) and #836's
word-splitting bug.

* **Invariant:** every scalar input to a shell script or composite-Action
  step arrives as exactly one argument with exactly the same bytes, and
  untrusted data cannot create additional commands, `$GITHUB_OUTPUT`
  records, paths, or side effects.
* **Mechanism:** execute the real script/extracted workflow block (not a
  text/YAML assertion) against a parametrized adversarial-input corpus —
  spaces/tabs, leading `-`, quotes, glob metacharacters, `$()`/backticks/
  `;`/redirects, CR/LF, Unicode, empty string, a value shaped like
  multiple CLI flags, path traversal — capturing real argv, environment,
  `$GITHUB_OUTPUT`, filesystem effects, and exit status.
* Reuse and extend the execution harness #758 already introduced rather
  than building a second one; this phase's job is widening its input
  corpus and its target script inventory, not inventing new tooling.

### Phase 9 — registry-completeness and silent-degradation invariants

Generalizes #753→#759 (hand-maintained list omission) and the shared shape
of #838/#834/#860/#883 (missing/rejected/partial evidence producing a
clean, silently-wrong result).

* **Registry completeness:** every declared `ChangeKind`/evidence-kind/
  provider is accounted for by every *total* downstream consumer
  (serialization, identity resolution, reporting, severity/policy,
  suppression, deduplication, SARIF/JSON/Markdown rendering, aggregate
  reporting) — bidirectional (`every kind has a mapping` and `every
  mapping key names an existing kind`), with a mutation check (deleting
  one registry entry must fail the suite). `checker_policy.py`'s existing
  import-time completeness assertion is the right *pattern*; this phase
  widens it to every other kind-keyed registry in the codebase, not only
  `ChangeKind`'s own verdict-bucket partition.
* **No silent clean verdict on incomplete evidence:** for every pipeline
  stage, inject missing/malformed/rejected/partial evidence and assert —
  independently, from a small explicit truth table, not all derived from
  one production helper — the resulting analysis status, report content,
  compatibility verdict, gate decision, process exit code, and aggregate
  result. This generalizes the existing fact-conservation suite
  (`tests/test_fact_conservation_properties.py`'s
  `TestFunctionRemovalFactConservation`/`TestVariableRemovalFactConservation`)
  from its current selected-detector-family scope to cover every `ChangeKind`
  family and every evidence-completeness failure mode named in AGENTS.md's
  "Known gaps" section (#838's rejected `-H` combination, #834's
  transitively-included-header misclassification, #860/#883's dropped
  configuration).

## Files & surfaces

* `AGENTS.md` — new "Decision-making principles" bullet (Phase 0).
* `.github/PULL_REQUEST_TEMPLATE.md`, `scripts/check_bugfix_test_contract.py`
  — reworded declared-half guidance text (Phase 0); the latter also
  gained one structural-gate correctness fix
  (`_is_collected_python_test_module()`, see the Phase 0 section above)
  once Phase 1's own `tests/regressions/manifest.py` file exposed a
  pre-existing loophole — not part of Phase 0's own design, but folded
  into this PR rather than deferred, since it's a small, self-contained
  fix directly caused by this PR's own new file.
* `tests/regressions/manifest.py`, `tests/test_regressions_manifest.py`
  (Phase 1, implemented). `scripts/check_regressions_manifest.py` — not
  built (see Phase 1's own note on why).
* One new or extended test module per bug class (Phases 2–9), named in
  each phase section above; extends rather than replaces the existing
  property/oracle test files the audit found already doing this well
  (`tests/test_detector_properties.py`,
  `tests/canonical_identity_contract.py`,
  `tests/test_clang_header_backend_integration.py`'s CastXML-parity-
  oracle tests, the #758 shell-execution harness).

## Tests

Each phase *is* a test-infrastructure change; see the phase descriptions
above for what each adds. Phase 1's `tests/test_regressions_manifest.py`
additionally verifies the registry's own integrity, mirroring
`tests/test_canonical_finding_id_completeness.py`'s exhaustiveness-gate
pattern for `ChangeKind`.

## Effort & risk

XL overall, phased into nine independently mergeable slices plus the
immediate Phase 0 process change. Every phase is additive — no production
code path changes, no schema version bump, no CLI/API surface change.
Risk is concentrated in Phase 6 (the configuration-propagation matrix,
which needs a real sentinel threaded through every public entry point and
so touches the most call sites) and Phase 3 (the reachability oracle,
which needs a generated-include-DAG model expressive enough to reproduce
the real counterexamples #834/#835/#843 found without becoming its own
maintenance burden) — both should get their own design review before
implementation starts, per this repository's normal process for an XL
initiative.

## Out of scope

* A broader rewrite of `scripts/check_bugfix_test_contract.py`'s structural
  half (code-change ⇒ test-change detection) — that mechanism is sound;
  this plan changes what counts as satisfying the *declared* half. (One
  narrow, self-contained correctness fix to the structural half's file-
  recognition logic did land in this PR — see the Phase 0 section above
  — but closing a loophole this PR's own new file exposed is different
  from redesigning the mechanism, which stays out of scope.)
* A trend-reporting database for property/mutation results over time —
  already listed as a deferred, separately-scoped item in AGENTS.md's
  "Known gaps" section; this plan does not change that.
* Migrating any of this plan's classes' existing narrow regression tests
  out of the test files that already carry them — Phase 2–9 work
  extends and generalizes those files in place, per each phase's own
  "reuse... rather than building a second one" note, rather than
  relocating passing tests for their own sake.
