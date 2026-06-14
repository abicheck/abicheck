# Architecture deepening plan

> Status: living document. Candidate **C1** is implemented (PR #395); the rest
> are proposals with concrete, sequenced plans. This document is the source of
> truth for the "deepen the architecture" effort and is updated as candidates
> land.

## Why this document exists

`abicheck` is a healthy, mature codebase (≈158 modules, ≈77k LoC, 34 ADRs) with
several genuinely *deep* modules — a lot of behaviour sits behind small, stable
interfaces:

- the self-registering `@registry.detector` pattern (`detector_registry.py`),
- the single-declaration `change_registry.py` (one entry per `ChangeKind`,
  with verdict + impact + addition flag colocated),
- the import-time `ChangeKind` partition assertion in `checker_policy.py`,
- `service.render_output()` as a clean format dispatcher.

The work below extends that same philosophy to the places that did **not** get
it. The organising idea is **module depth**: increase the amount of behaviour
behind an interface while *shrinking* the surface callers must understand. The
test applied to every candidate is the **deletion test** — if the module or
abstraction were removed, would its complexity *concentrate* (a sign the module
was deep and worth having) or *scatter* across its callers (a sign the design
was shallow and the knowledge was effectively inlined everywhere)?

### Vocabulary

| Term | Meaning |
|------|---------|
| **Module** | a unit with an interface and an implementation |
| **Depth** | leverage at the interface: lots of behaviour behind a small surface = deep |
| **Seam** | where an interface lives; a place behaviour can change without editing call sites in place |
| **Adapter** | a concrete implementation satisfying an interface at a seam |
| **Locality** | depth's payoff for maintainers: knowledge concentrated in one place |

### Guard-rails for every candidate

Every change in this plan must keep the existing gates green and is reviewed
against them:

- `ruff check abicheck/ tests/` and `ruff format --check`
- `mypy abicheck/` — baseline **0 errors**, must stay 0
- the fast unit lane (`pytest -m "not integration and not libabigail and not abicc and not slow and not golden"`)
- `python scripts/check_ai_readiness.py` — **0 errors** (warnings allowed)
- where output or parity can change, the relevant `golden` / `abicc` /
  `libabigail` / `integration` markers are run as well

A refactor that changes *visible behaviour* (output text, verdict, exit code,
ABICC parity) is called out explicitly below and is **not** treated as a pure
refactor — it lands behind a golden-output review and, where it encodes a
decision, an ADR.

---

## Candidate catalogue

Each candidate states: the **problem** (with evidence), the **goal** (what depth
we gain), the **approach** (concrete steps), the **edge cases / risks** found
while grilling the design, and its place in the **sequence**.

### C1 — Consolidate mangled-name classification *(implemented — PR #395)*

**Problem.** The Itanium-ABI prefix knowledge that answers *"is this symbol an
RTTI artifact / a function-local RTTI / in an internal namespace?"* was
re-encoded as private tuples in `report_summary.py`, `diff_platform.py` and
`diff_symbols.py`, and the copies had **drifted** (the reporting copy carried
the thunk prefixes `_ZTc/_ZTh/_ZTv`; the `diff_platform` local copy was the bare
`_ZTV/_ZTI/_ZTS` triple). There was no module to delete — the knowledge was
pre-scattered, which is itself the signature of a missing deep module.

**Goal.** One home for the prefix tables and the `symbol_origin` classifier, so
a new compiler convention is added once instead of hunted across the tree.

**Approach (done).** New `abicheck/name_classification.py` owns the tables and
classifiers. Crucially, **semantically distinct concepts are kept as distinct,
clearly-named constants** rather than merged — they are not interchangeable:

| Constant | Meaning |
|----------|---------|
| `ITANIUM_RTTI_PREFIXES` | generic RTTI artifacts (vtable/VTT/typeinfo obj+name/thunks) — origin classification |
| `RTTI_DATA_PREFIXES` | the size-owning data objects `_ZTV/_ZTI/_ZTS` only |
| `LOCAL_RTTI_PREFIXES` | RTTI for function-local (unnameable) types |
| `INTERNAL_NAMESPACE_COMPONENTS` | length-prefixed internal-namespace components |

Behaviour is unchanged (tuples moved verbatim).
`report_summary.classify_symbol_origin` is preserved as a re-export.

**Deliberately out of scope (follow-up).** The stdlib-/runtime-specific RTTI
skip sets in `elf_symbol_filter.py`, `diff_elf_layout.py` and `elf_metadata.py`
were left in place — their memberships genuinely differ and feed `startswith`
filters whose results would change if merged. Unifying them safely needs
per-call behaviour-equivalence checks. Tracked here as a sub-task of **C10**.

---

### C2 — A report view-model behind one seam

**Problem.** There is no `Reporter` interface. Each of five output formats
re-walks `DiffResult.changes` independently: the line
`changes = apply_show_only(list(result.changes), show_only, policy=…)` is
copy-pasted verbatim into `reporter.to_json`, `sarif.to_sarif`,
`junit.to_junit_xml` and `html_report.generate_html_report`. Worse, each format
re-derives its **own** classification of the same changes — `reporter.py` via
`checker_policy` kind-sets, `html_report.py` via `report_classifications.severity`,
`pr_comment.py` via a private `_SEVERITY_BUCKET` dict, `report_summary.py` via a
third origin classifier. Three-plus competing notions of "how severe / what
category is this change" that can disagree across formats.

**Goal.** Build the classified, filtered, summarised view **once**; renderers
become thin, policy-free functions `ReportModel → str/bytes`. A new output
format becomes a ~30-line renderer instead of a re-implementation of the
pipeline tail. SARIF, HTML and the PR comment can no longer disagree about a
change's severity.

**Approach.**
1. Add `report_model.py` with `ReportModel.from_diff_result(result, show_only)`
   — applies `show_only` once, classifies via C1's classifier + the policy
   kind-sets, buckets, and summarises. Provide `to_dict()` / `from_dict()` so it
   is serialisable.
2. Port `reporter.to_json` first (the canonical format) and snapshot its current
   output as a golden baseline.
3. Migrate `sarif`, `junit`, `html`, `pr_comment` one at a time, diffing golden
   output at each step.
4. Delete the per-renderer `apply_show_only` copies and the duplicate
   classifiers.

**Edge cases / risks.**
- `pr_comment.py` consumes a JSON dict, not a `DiffResult` — hence the
  serialisable model: it becomes the single feed for both in-process and
  JSON-driven renderers.
- Some renderers read `result.changes` for *unfiltered* counts; audit each
  before deleting its filter copy.
- **Behaviour change:** wherever renderers currently disagree on severity,
  unifying changes that format's output. This is not a pure refactor — it needs
  a golden-output review and a short ADR recording the canonical classification.
- Pairs naturally with C1 (origins/severity route through the deep classifier).

**Risk:** medium-high (user-visible output). Gate with `-m golden`.

---

### C3 — Binary-format handler registry

**Problem.** `dumper.dump()` dispatches binary format via a hard-coded
`if fmt=="macho" / elif "pe" / elif "elf"` chain; magic-byte knowledge lives
both in `_detect_format()` and again as `is_pe()` / `is_macho()` in the metadata
modules. The three builders `_dump_elf` / `_dump_macho` / `_dump_pe` share ~70
lines of near-identical castxml-invocation + parser + `AbiSnapshot`-construction
boilerplate, and the `--lang → profile` conversion is a helper for ELF but
copy-pasted inline for the other two. Mach-O's leading-underscore symbol strip
is duplicated within `_dump_macho` itself.

**Goal.** Reuse the registry idiom the project already trusts for detectors.
Adding a future binary format becomes a new handler file, not edits to `dump()`.
"Everything about Mach-O" lives in one place.

**Approach.**
1. Define a `BinaryFormatHandler` protocol: `matches(magic) → bool`,
   `parse_binary_metadata(path)`, `attach(snapshot, meta)`,
   `normalize_symbol(name)`, plus an optional post-build hook.
2. Add a shared `_build_snapshot_from_castxml()` for the common body.
3. Extract ELF/PE/Mach-O handlers from the three `_dump_*` functions, keeping
   each format's no-headers symbol-only path.
4. Replace `dump()`'s if/elif with `registry.select(magic)`; unify
   `--lang → profile` on the existing ELF helper.

**Edge cases / risks.**
- ELF alone calls `_populate_elf_visibility` and accepts `debug_format` — the
  optional post-build hook keeps that without polluting the shared body.
- Magic detection must become single-source; keep `is_pe()`/`is_macho()` as thin
  wrappers only if external callers exist.

**Risk:** medium. Requires the `integration` marker (castxml + gcc/g++) to verify.

---

### C4 — Detector auto-discovery (drop the import manifest) *(implemented — PR #395)*

**Problem.** The detector registry's "no manual list" promise is undercut by
side-effect imports in `checker.py` tagged
`# noqa: F401 — triggers detector registration`. A new `diff_*` module that
forgets to get added to that block contributes *zero* detectors, with no error.

**Goal.** Adding a detector module requires no `checker.py` edit; a forgotten
module is impossible to skip silently.

**What was implemented (order-preserving variant).** Investigation surfaced two
facts the original sketch missed:

1. Most of `checker`'s `diff_*` imports are **not** pure side-effects — they also
   pull real symbols (`diff_filtering` helpers, `diff_platform`/`diff_types`
   functions, …), so the block cannot simply be deleted.
2. **Registration order feeds post-processing dedup.** An empirical check
   confirmed importing *all* `diff_*` modules registers exactly the current 49
   detectors (no additions), but reordering them could change dedup outcomes.

So C4 was implemented as a **safety net that preserves order**:
`registry.ensure_loaded()` walks `pkgutil.iter_modules(abicheck.__path__)` for
the `diff_` prefix (sorted) and imports each. It is called at the top of
`compare()`. Because `checker`'s explicit imports already ran at module load
(fixing the canonical order), `ensure_loaded()` is a **no-op for the existing
set** — the modules are cached, so re-import does not re-register. A *new*
`diff_*` module is discovered automatically and appended after the existing
detectors, deterministically — **no `checker` edit required**, and zero
behaviour change today.

Verification gate is a test (`tests/test_detector_discovery.py`) rather than a
readiness check: it asserts every `diff_*` module is imported after
`ensure_loaded()` (the silent-skip footgun), that the call is idempotent and
order-stable, and a soft detector-count floor.

**Risk:** low; behaviour-preserving (verified: registered set + order unchanged).

---

### C5 — Fold synthetic detectors onto the registry *(deferred — not a clean win)*

**Re-grilled and deferred.** Closer inspection showed the sketch
mis-characterised the target. The post-processing step is `DetectCppPatterns`,
which runs **seven** coupled sub-detectors, not three. Several
(`detect_sycl_overload_set_removal`, `detect_cpu_dispatch_isa_dropped`) return
`(findings, suppressed_keys)` tuples and drive **grouped-child suppression**
that mutates `ctx.kept` / `ctx.suppressed` *by reference*; one
(`detect_inline_body_renamed_member`) needs the in-flight `changes` list. They
fundamentally require **post-filter context**, which the registry's
`(old, new) -> list[Change]` contract does not carry. Splitting them onto a
registry phase buys marginal discoverability (`detector_names` listing them) at
the cost of either a second, ctx-aware detector contract or breaking up a
cohesive step. Net negative — left as-is. If discoverability is wanted later, a
cheaper move is to expose a read-only `synthetic_detector_names` list from
`post_processing` for docs/coverage tools, without touching the registry
contract.

**Original problem (for reference).** There are two detection orchestrators. The
synthetic detectors are invisible to `registry.detector_names`, so nothing that
introspects "all detectors" sees them.

**Goal.** One discovery point for "what detectors exist", without losing the
post-filter ordering the synthetic detectors need.

**Approach.**
1. Add a `phase: "primary" | "post_filter"` attribute to `@registry.detector`
   (default `primary`).
2. Register the two pure synthetic detectors as `post_filter`.
3. Replace their direct calls in `AddSyntheticDetectorFindings` with
   `registry.run_phase("post_filter", …)`.

**Edge cases / risks.**
- These run *after* surface scoping + dedup — the phase concept must preserve
  that ordering; a naive move to the primary phase would run them too early.
- `detect_sycl_overload_set_removal` also *suppresses* redundant findings (it
  returns a tuple, not just changes). The phase contract should either allow a
  detector to return suppressions, or this one stays special-cased and
  documented. Default: leave it special, migrate the two pure ones.

**Risk:** low-medium. Do after C4.

---

### C6 — A `Change` factory for consistent findings *(scheduled as its own PR)*

> **Tracking decision:** C6 is the widest-blast-radius candidate (~358 call
> sites) and is intentionally carved out into a **separate, self-contained PR**,
> done *after* the C1–C2 work in #395 merges. This section is the spec for that
> PR — detailed enough to start cold.

**Problem.** `Change(kind=ChangeKind.XXX, description=f"…", old_value=…, new_value=…)`
is hand-rolled across the `diff_*` modules, each call site inventing its own
description wording. Reporters then partly parse those free-text descriptions.
The `impact` text already lives in `change_registry.py` (one entry per
ChangeKind), but the per-finding *description* does not — so phrasing is
inconsistent, untestable, and the description is the only place some detail
(old→new) is encoded for machine consumers.

**Goal.** Uniform, templated descriptions owned next to each kind's
verdict/impact in `change_registry`; detectors pass structured fields, not prose;
machine consumers read fields, not scraped text.

**Inventory / where to start.**
- Enumerate the sites: `rg -n "Change\(" abicheck/diff_*.py abicheck/checker.py`
  (was ~358 at time of writing — re-count first; treat the number as
  approximate). Group by ChangeKind to see which kinds dominate.
- Classify each kind's description into **regular** (a fixed template +
  symbol/old/new substitution, e.g. `func_return_changed`:
  `"Return type changed: {symbol}; old={old}, new={new}"`) vs **bespoke**
  (embeds computed offsets, demangled signatures, vtable slot indices, counts —
  e.g. `type_field_offset_changed`, vtable/RTTI size findings). Expect a long
  tail of regular kinds and a small bespoke minority.

**Approach.**
1. Add an optional `description_template: str | None` field to each
   `change_registry` entry (alongside `impact`). Use `str.format`-style named
   placeholders drawn from a fixed vocabulary: `{symbol} {old} {new} {detail}`.
2. Add `change_registry.make_change(kind, *, symbol, old=None, new=None,
   detail=None, description=None, **change_kwargs) -> Change`: formats from the
   template when present, else requires an explicit `description=`. It stamps
   `kind` and forwards the remaining `Change` fields (`old_value`, `new_value`,
   `caused_by_type`, …). Keep it a thin wrapper over the `Change` dataclass.
3. Migrate the **regular** kinds to `make_change(...)`, deleting their f-strings.
4. Leave **bespoke** kinds calling `make_change(..., description=<computed>)` —
   do **not** force-fit a template; the override path is first-class.
5. Add a registry-completeness test: every ChangeKind that any detector emits
   either has a `description_template` or is on an explicit `BESPOKE_DESCRIPTION`
   allowlist (so a new kind can't silently regress to an ad-hoc f-string).

**Sequencing within the PR.** Land the factory + templates + a handful of
migrated kinds first (small, reviewable), then migrate the rest in a second
commit. Mechanical, so keep commits per-module to ease review.

**Edge cases / risks.**
- ~358 call sites — highest churn-to-benefit ratio; that is exactly why it is a
  standalone PR, not bundled with semantic refactors.
- Descriptions are user-visible **and** some are golden-snapshotted: run the
  `golden` lane; any wording change is intentional and regenerated deliberately.
- Some descriptions are parsed downstream (e.g. PR comment / appcompat). Audit
  for `.description` string parsing before changing wording; prefer adding a
  structured field over changing the prose those consumers read.
- Depends on C2 only loosely: C2's model already centralises *classification*;
  C6 centralises *description text*. They are independent but both reduce
  reporter string-scraping, so do C6 after C2 to land on the model.

**Risk:** mechanical but wide. Standalone PR, golden-gated.

---

### C7 — Push business logic out of the CLI into the service layer *(exit-code unification done; command-body extraction is a sized follow-up)*

**Problem.** `cli.py`'s `compare_cmd` (~200 lines of a file at the size cap) does
result post-processing, GitHub-annotation emission and exit-code mapping inline —
business logic, not presentation. The same leak appears in
`cli_compare_release`, `cli_appcompat` and `cli_stack`. Most contractually
dangerous: the **verdict→exit-code mapping was duplicated inline** in `compare`
(`cli._exit_with_severity_or_verdict`) and `compare-release`
(`cli_compare_release._exit_compare_release`), so the two flows could drift.

**Done (this PR).** The exit-code contract is unified:
- `severity.legacy_exit_code(verdict)` is the single home for the
  non-severity-aware mapping (BREAKING→4, API_BREAK→2, compatible→0), sitting
  next to the existing severity-aware `compute_exit_code`.
- Both `compare` and `compare-release` route their legacy branch through it;
  flow-specific floors (`compare-release`'s operational `ERROR`→4 and
  removed-library→8) are applied on top, documented at the call site.
- `tests/test_exit_code_integrity.py` locks the contract and asserts the two
  flows produce identical codes per verdict, and that the `compat` scheme
  (BREAKING→1, API_BREAK→2, 3–11 errors) is *deliberately distinct* and not
  accidentally unified.

**Remaining (follow-up, can be its own PR).** Extract the rest of the
command-body business logic into `service.py`:
1. A `service.run_compare_to_report(...)` (or similar) returning a structured
   result + the resolved exit code, so post-processing, annotation emission and
   exit decisions live in the library layer.
2. Reduce `compare_cmd` / `compare_release_cmd` / `appcompat_cmd` /
   `stack_check_cmd` to arg-parse → service call → `sys.exit`.
3. Extract shared Click option stacks into `cli_options.py` where they duplicate
   (`--suppress`, `--policy`, severity flags).
This is what finally drops `cli.py` below the size cap.

**Edge cases / risks.**
- Exit-code semantics are contractual (documented in `/CLAUDE.md`); the
  unification preserved the legacy and severity-aware mappings exactly (covered
  by the new integrity tests). The follow-up extraction must keep them.
- GitHub-annotation emission has side effects (writes workflow commands); keep it
  behind the same flag and test with output capture.

**Risk:** exit-code unification — low (behaviour-preserving, tested). Command-body
extraction — medium (wide, exit-code- and annotation-sensitive).

---

### C8 — Make the ABICC-compat CLI a thin adapter

**Problem.** `compat/cli.py` (~1581 lines) is a parallel pipeline: it
re-implements dump, check, suppression-building (`_build_skip_suppression`) and
verdict computation, rather than wrapping the main pipeline. It already reuses
the output modules (`html_report`), so it is only half-wrapped.

**Goal.** ABICC compatibility becomes a translation layer: ABICC flags →
`service.run_compare`, then verdict → ABICC exit codes. One pipeline, two front
ends.

**Approach.**
1. Map ABICC `-skip-*` flags onto the existing `suppression` machinery instead
   of a bespoke builder.
2. Route `dump`/`check` through `service.resolve_input` / `run_dump` /
   `run_compare`.
3. Keep only the ABICC-specific argument parsing and the exit-code translation
   in `compat/cli.py`.

**Edge cases / risks.**
- This is the drop-in ABICC replacement — **parity is contractual**. Land behind
  the `abicc` marker (abi-compliance-checker + gcc/g++) and the golden lane.
- ABICC error exit codes (3–11) must be preserved exactly
  (`_classify_compat_error_exit_code`).

**Risk:** high (parity-sensitive). Sequence after C2/C7 stabilise the shared
layer.

---

### C9 — Relocate confidence computation *(implemented — PR #395)*

**Problem.** `_compute_confidence()` and its three helpers lived in
`diff_filtering.py` but are pure orchestration: they consume `detector_results`
(the registry's output) and the snapshots' available metadata, and are called
from `checker.compare()`. Following the orchestration flow meant a cross-file
hop into a *filtering* module.

**What was implemented.** Moved the four functions
(`_detect_evidence_tiers`, `_determine_evidence_tier`,
`_determine_confidence_level`, and the public `compute_confidence`) into a new
dedicated `abicheck/confidence.py`. A new module (rather than folding into
`checker.py`) avoids a `checker ↔ diff_filtering` import cycle: `confidence.py`
depends only on `checker_policy`, `detectors` and `model`, so it sits at the
bottom of the graph. `checker` and the two test modules now import from
`confidence`; the historical name `_compute_confidence` is kept as an alias.
`diff_filtering.py` is left to actual filtering and shrank by ~190 lines.

**Risk:** low; behaviour-preserving (verified: full fast lane + no new import
cycle).

---

### C10 — Split `model.py`: pure data vs name heuristics

**Problem.** `model.py` (imported by 56 modules) mixes the core data classes
(`AbiSnapshot`, `Function`, `RecordType`, …) with string heuristics —
`is_compiler_internal_type`, `is_non_abi_surface_type`, `canonicalize_type_name`,
cv-qualifier parsing. The *type-name classification* part is conceptually the
same family as C1's symbol-name classification.

**Goal.** `model.py` is the pure data model; all name/type classification lives
with C1's `name_classification` (or a sibling). This also gives the deferred C1
follow-up (unifying the stdlib-/runtime-RTTI skip sets) a natural home.

**Approach.**
1. Move the type-name classification helpers into `name_classification.py`,
   keeping back-compat re-exports from `model.py` to avoid churning 56 importers
   at once.
2. Migrate importers incrementally.
3. Fold the stdlib-/runtime-RTTI skip sets (from `elf_symbol_filter`,
   `diff_elf_layout`, `elf_metadata`) in *with behaviour-equivalence proofs* per
   call site (the deferred C1 sub-task).

**Edge cases / risks.**
- `model.py`'s public surface is part of the Python API (`/CLAUDE.md` calls this
  out) — re-exports must stay until importers migrate.
- Watch for import cycles (`name_classification` must stay dependency-free).

**Risk:** medium; staged via re-exports.

---

## Environment / verifiability constraints

Some candidates change behaviour that can only be safely verified with external
tools or output snapshots. Status in the current dev environment:

| Lane | Tool | Present? | Blocks |
|------|------|----------|--------|
| `integration` | castxml + gcc | castxml **missing** | C3 (dump path) |
| `abicc` | abi-compliance-checker | **missing** | C8 (parity) |
| `libabigail` | abidiff | **missing** | parity cross-checks |
| `golden` | snapshot files | present | C2 (output) |

Implication: **C3 and C8 must not be merged from an environment that cannot run
their lanes** — they are deferred to a context with castxml / ABICC, or to CI
with those lanes green. C2/C7 change user-visible output / exit codes and need
explicit sign-off plus the golden lane before merge.

## Sequencing

Ordered by risk-adjusted payoff. Cheap, isolated wins first; output- and
parity-changing work last.

```
C4  detector auto-discovery        ✅ done (PR #395)
C9  relocate confidence            ✅ done (PR #395)
C1  name classification            ✅ done (PR #395)
C2  report view-model              ✅ inc 1–2 done (model+ADR-036; maps unified; integrity tests)
C7  CLI → service                  ◐ exit-code unified + integrity tests (body-extraction follow-up)
C3  binary-format registry         (parallelisable; needs integration lane)
C10 split model.py                 ◐ stage-1 done (name predicates moved)
C8  ABICC compat adapter           (parity-sensitive)
C5  synthetic detectors → registry ⛔ deferred (entangled; net-negative)
C6  Change factory                 (widest churn; depends on C2)
```

Rationale: C4 and C9 are mechanical and reversible — do them to build
confidence. C1 (done) unblocks C2 and C10. C2 is the largest locality payoff but
carries visible-output risk, so it lands behind golden review before the
churn-heavy C6. C8 is sequenced last among the structural items because ABICC
parity is contractual and benefits from a stabilised shared layer underneath it.

## Tracking

| ID | Title | Status | PR |
|----|-------|--------|----|
| C1 | Name classification module | Done | #395 |
| C2 | Report view-model + canonical severity + cross-channel integrity tests (ADR-036) | Increment 1–2 done | #395 |
| C3 | Binary-format handler registry | Proposed | — |
| C4 | Detector auto-discovery | Done | #395 |
| C5 | Synthetic detectors → registry | Deferred (not a clean win) | — |
| C6 | `Change` factory | Spec'd for own PR | — |
| C7 | CLI → service (exit-code unify + cross-flow integrity tests done; command-body extraction follow-up) | Partial | #395 |
| C8 | ABICC compat adapter | Proposed | — |
| C9 | Relocate confidence computation | Done | #395 |
| C10 | Split `model.py` (stage-1: name predicates) | Stage-1 done | #395 |
