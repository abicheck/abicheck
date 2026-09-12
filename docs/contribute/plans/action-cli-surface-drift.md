# Action-vs-CLI surface drift: audit and prevention

**Status:** Audit complete (2026-09-12). Phases 1 and 3a landed; ADR-070
drafted (Proposed). Phases 2, 3b, 4 and 5 proposed, not started.

**Problem.** The composite GitHub Action (`action/`, `actions/check-target/`)
is a hand-maintained adapter over the abicheck CLI. It encodes, in shell, a
large number of assumptions about what the CLI accepts. Nothing compares the
two surfaces, so those assumptions rot silently: a CLI restriction is lifted
and the Action keeps rejecting, or a CLI option is retired and the Action's
tokenizer keeps reserving its name. `action/run.sh` documents this risk about
itself (`_extra_args_is_value_option`, `action/run.sh:1626-1634`: "a
hand-maintained snapshot, not derived at run time ... it can go stale") and
the audit below confirms the prediction came true in three independent places.

**Where derivation is actually possible — corrected.** An earlier revision of
this plan asserted, on the strength of `_extra_args_is_value_option`'s own
comment, that "the Action cannot introspect the CLI at run time" and that
anything derived must therefore be derived at authoring/CI time. **That is
false for the tokenizers, and the comment asserting it is itself a stale
justification** (Codex review on PR #1234, verified against `action.yml`):

| Shell | Runs | Live `abicheck`? |
|---|---|---|
| `action/validate-inputs.sh` | before Python setup, by design ("fail fast") | **No** |
| `action/run.sh` | `action.yml`'s step 4, after step 3's `pip install` (`action.yml:1240-1246`) | **Yes** |
| `actions/check-target/action.yml`'s assurance-overlay step | line ~666, after the `pip install` at `:507-509` | **Yes** |

`run.sh` already resolves an interpreter and checks `abicheck` importability
(`action/run.sh:2095`), so it not only *could* introspect — it already proves
the import works. Only `validate-inputs.sh` is genuinely pre-install, and the
CLI facts it needs are the format choice sets, not the option tables.

This correction materially narrows B1/Phase 3 below: the option tables do not
need a generated artifact at all, because the surface that uses them can ask
the installed CLI directly.

---

## Part A — Audit

Classification: **AGREES** (the Action's claim matches the CLI today),
**DRIFTED** (the claim is false today), **STALE-COMMENT** (the decision is
defensible but its stated justification names something that no longer
exists), **UNGUARDED** (the CLI restricts something the Action forwards
anyway).

### A1. Release-operand guards in `run.sh` / `validate-inputs.sh`

| # | Action site | Claim | CLI ground truth | Verdict |
|---|---|---|---|---|
| 1 | `action/run.sh:2813`, duplicated at `action/validate-inputs.sh:281` | `lang`/`ast-frontend`/`gcc-path`/`gcc-prefix`/`gcc-options`/`sysroot`/`nostdinc` unsupported for a directory/package compare, "the per-library fan-out never threads the L2 compile context" | `abicheck/cli_resolve.py`, comment above `_reject_compile_context_for_set_inputs` (~:759) and the function body: the **both-sides** compile context *is* threaded through the release fan-out. Only a *sided* `--ast-frontend old=/new=` is rejected (`sided_frontend_explicit`). | **DRIFTED** |
| 2 | `action/run.sh:2898` | `--depth headers` "is still rejected by the CLI here", dropped with a `::notice::` | `abicheck/cli_compare_options._resolve_depth_for_set_inputs` (~:214) forwards **every** rung and its own docstring documents both former rejections as no longer true ("It rejects nothing: every rung of the public ladder is forwarded"). | **DRIFTED** |
| 3 | `action/run.sh:2882` (`--depth build/source` half) | `--depth build`/`source` unsupported for a directory/package operand | Same function: `--depth build`/`source` are forwarded now. | **DRIFTED** |
| 4 | `action/run.sh:2882` (`--sources`/`--build-info` half) | inline `--sources`/`--build-info` unsupported for a directory/package operand | `cli_resolve._EVIDENCE_SET_INPUT_FLAGS` + `_reject_evidence_flags_for_set_inputs` still raise a `UsageError` for `--sources`/`--build-info`/`--dump-manifest`. | **AGREES** |
| 5 | `action/run.sh:2858-2862` | `--config` is *not* covered by the set-input evidence rejection | `_EVIDENCE_SET_INPUT_FLAGS` lists only `sources`/`build_info`/`dump_manifest`. | **AGREES** |
| 6 | `actions/check-target/validate-inputs.sh:141` | `requested-depth: build/source` unsupported for `kind: bundle`, because the fan-out collects no inline build/source evidence | True of `--sources`/`--build-info` (#4), but the comment also leans on "the root Action's run.sh now fails loud", i.e. on drifted guard #3. | **AGREES** (substance) / **STALE-COMMENT** (secondary justification) |
| 7 | `actions/check-target/validate-inputs.sh:155` | `requested-depth: headers` unsupported for `kind: bundle` | Justified by the **Action's own** baseline staging (one project-wide `header:` input, no per-baseline-version header snapshot), not by what the CLI accepts. Survives the CLI fix unchanged. | **AGREES** |

Guard #1 and #2/#3 are the two known drifts; they are fixed by a separate
task. The audit's own contribution here is that #1 exists in **two** copies
and `tests/test_action_validate_inputs.py` runs both against the same
fixtures — so the existing cross-copy test confirms two wrong copies agree.

### A2. `_extra_args_is_value_option` / `_ct_extra_args_is_value_option`

Two byte-identical hand-maintained `case` lists:
`action/run.sh:1635-1656` and `actions/check-target/action.yml:901-920`.
Ground truth introspected from `abicheck.cli.main.commands[...]`.

**Twelve entries name options that are value-taking on no live command** —
every one a flag this repo retired (see `scripts/retired_surfaces.py`):

```
--ast-frontend  --compiler  --compiler-option  --compiler-prefix
--debug-format  --debuginfod-url  --frontend-context  --lang
--manifest  --max-findings  --pdb-path  --public-header-dir
```

**One entry is command-misscoped:** `--sysroot` is no longer a `compare`
option; it survives only on `deps tree`.

**Four real value-taking options are missing**, and they matter because
`_effective_format()` is computed at `action/run.sh:3199`, *after* the mode
dispatch — so the tokenizer runs for **every** mode, not just `compare`:

| Missing | Command |
|---|---|
| `--compression`, `--provenance` | `dump` |
| `--old-root`, `--new-root` | `deps compare` |

`_extra_args_expand_short_clusters` (`action/run.sh:1680`, and its
check-target twin) claims `-j` is one of "four value-taking" short options on
`compare`. `compare` has no `-j` at all (`jobs` was retired, ADR-068 D5).

**What the surplus entries actually cost — corrected.** An earlier revision
of this section claimed `--lang --config x.yml` let a real conflicting
`--config` "through unnoticed". Codex (PR #1234, P2) correctly pushed back:
all twelve surplus names are retired on every command the Action invokes, so
Click rejects the invocation with a usage error regardless of how the shell
tokenized it. The token misclassification is real and demonstrable —

```
                             pre-fix tokens                    --config seen?
--lang --config x.yml        --lang=`--config x.yml`            NO
--pdb-path --config x.yml    --pdb-path=`--config x.yml`        NO
                             corrected
--lang --config x.yml        --lang=(empty), --config=x.yml     YES
```

— and `run.sh:3175`'s synthesized-config conflict guard really is skipped in
the pre-fix column. But the run then dies on the unknown option anyway, so
the cost is a **degraded diagnostic** (a raw Click usage error instead of the
Action's own specific guidance about which `--config` won), not a false-
negative gate. No surplus entry yields a live false-negative path, because
none of them is accepted by any command the Action invokes.

**The missing entries are the direction with real, shipped harm**, and it is
a *false rejection* rather than a false pass. That is the documented #1222
repro (`tests/test_reusable_workflows_assurance_overlay_extra_args_config.py`'s
`TestAssuranceOverlayRecognizesUsedByManifestAsValueOption`): `extra-args:
'--used-by-manifest --config'` with a consumer manifest literally named
`--config` is argv the real CLI accepts, but the tokenizer read it as a bare
trailing valueless `--config` and the overlay step's guard rejected it
outright — `analysis-assurance-complete: true` broke a check that would
otherwise have succeeded. The four options missing here
(`--compression`, `--provenance`, `--old-root`, `--new-root`) are the same
shape on `dump`/`deps compare`, reachable whenever such an option's own value
resembles a flag the scanners read.

So the two directions are not symmetric, and neither is "safe": a surplus
entry degrades an error message, a missing entry can fail a valid check.

**Why the existing guard missed all of this.**
`tests/test_extra_args_is_value_option_completeness.py` asserts only
`compare_value_options - list == {}` — one direction — and introspects
`compare` alone. A stale *extra* entry and a missing *non-compare* option are
both invisible to it by construction. Its own docstring claims the lists'
staleness "can only under-recognize" and is therefore safe; that is true of a
missing entry and false of a surplus one (a surplus entry makes the tokenizer
consume the following token as a value). **Verdict: DRIFTED, and the guard's
scope is itself a drift surface.**

### A3. Action inputs vs. the CLI flag / config key they map onto

83 inputs on `action/action.yml`, 57 on `actions/check-target/action.yml`.
Inputs whose CLI target was retired are handled correctly — the mapping moved
rather than the input:

| Input | Target | Verdict |
|---|---|---|
| `policy-file` | `--policy` (`run.sh:3020`) — `--policy-file` retired | **AGREES** |
| `compile-db` | `--build-info` (`run.sh:2656`, `:2902`) — `--compile-db` retired | **AGREES** |
| `public-header-dir` | `-H` (`run.sh:2638`, `:2768`) — `--public-header-dir` retired | **AGREES** (input name is now misleading, not wrong) |
| `required-symbols` | `--required-symbol @FILE` (`run.sh:3074`) — `--required-symbols` retired | **AGREES** |
| `snapshot-compression` | `--compression`, choices `auto/none/gzip/zstd` (`validate-inputs.sh:158`) | **AGREES** |
| `dso-only`, `include-private-dso`, `fail-on-removed-library` | synthesized `--config` overlay (`run.sh:1516-1518`) — CLI flags retired | **AGREES** |
| `against`, `estimate`, `audit`, `mode: scan`, `new-library-set`, `risk-rules`, `crosscheck`, `build-target`, `require-complete-analysis`, `bundle-system-providers` | hard-rejected in `validate-inputs.sh` and again in `run.sh` | **AGREES** |
| `jobs` | warns, ignored (`validate-inputs.sh:486`) | **AGREES** |
| `allow-build-query` | declared, documented "deprecated and ignored", read nowhere | **AGREES** (deliberate) |

Format allow-lists re-verified against live `click.Choice` sets:
`compare` → `json markdown sarif html junit review oneline`
(`validate-inputs.sh:251`) **AGREES**; `deps tree`/`deps compare` →
`json markdown html` (`validate-inputs.sh:172`) **AGREES**.

### A4. Audit-only (`compare --no-baseline`) guards vs. `_UNSUPPORTED_OPTIONS`

`abicheck/frontends/cli/commands/no_baseline_rulings._UNSUPPORTED_OPTIONS`
rejects **27** options. `run.sh:2711-2741` guards nine of them
(`since`, `changed-path`, `budget`, `follow-deps`,
`used-by`/`used-by-manifest`/`required-symbol`/`required-symbols`,
`old-header`/`old-include`, `old-version`) plus the directory-operand shape.

Tracing each remaining rejected option that has an Action input:
`--search-path`/`--ld-library-path` are forwarded only under
`follow-deps: true` (`run.sh:3059-3060`), already rejected for this shape;
`--debug-info`/`--devel-pkg` only for a release-style operand
(`run.sh:3083-3086`), also already rejected. **No unguarded Action input
reaches a rejected option** — so this is **AGREES today, by two layers of
coincidence rather than by construction**: the guard list is a nine-entry
snapshot of a 27-entry table, and nothing checks that the remaining 18 stay
unreachable. A future input, or a future change to the `follow-deps` gating,
turns this into a silent CLI usage error with no Action-level diagnosis.
Record as **UNGUARDED (latent)**.

The other 18 reach the Action only through `extra-args`, where the CLI's own
`UsageError` is the correct and only answer — `run.sh`'s `_is_cli_error()`
plus the exit-64 dispatch already surfaces it.

### A5. Exit codes the Action interprets

| Code | Action | CLI ground truth | Verdict |
|---|---|---|---|
| `0`/`2`/`4` | verdict dispatch | severity-aware fold via `severity.compute_exit_code` | **AGREES** |
| `1` | four-way split (severity / coverage / assurance / scope) | `contract_coverage_exit.py` (`max`-folded 0→1), `policy/scope_completeness.py` | **AGREES** |
| `5` | `BUDGET_OVERFLOW` | `abicheck/cli_compare_fold.py:833` `sys.exit(5)` | **AGREES** |
| `7` | `EVIDENCE_CONTRACT_ERROR` | `policy/exit_decision_precedence.py:220` `EXIT_EVIDENCE_CONTRACT_ERROR = 7` | **AGREES** |
| `8` | removed library | checked ahead of the coverage fallback | **AGREES** |
| `16` | `NOT_COMPARABLE` | `frontends/cli/runtime.py:206` `_EXIT_NOT_COMPARABLE = 16` | **AGREES** |
| `64` | usage error | `frontends/cli/runtime.py:195` | **AGREES** |

Values all agree. **Four justification comments cite deleted modules**:
`action/run.sh:3567` (`scan_engine`), `:3961-3962`
(`scan_engine._EvidenceContractError`, "`_EXIT_EVIDENCE_CONTRACT_ERROR = 7`
in `cli_scan.py`"), `:5014` (`abicheck/cli_scan.py`), `:5026` (`cli_scan`).
Neither `abicheck/cli_scan.py` nor `abicheck/scan_engine.py` exists; exit 7
is now an engine-level constant in `policy/`. **STALE-COMMENT ×4.** A reader
checking one of these against its cited source finds nothing and cannot tell
whether the code or the comment is wrong.

**A sixth stale justification, and the one that misled this plan's own first
revision:** `_extra_args_is_value_option`'s comment
(`action/run.sh:1626-1634`) explains that the list is not derived at run time
because "the Action has no live `abicheck --help-all` to introspect before it
even knows which dependency-source install produced a `python`/`abicheck` on
PATH". `action.yml` installs abicheck at step 3 and runs `run.sh` at step 4,
and `run.sh:2095` already verifies the import — so the comment's stated reason
for the whole hand-maintained-snapshot design does not hold. A false
justification is worse than a missing one: it was load-bearing enough that
this plan's first revision adopted it without checking and designed a
generated artifact around it. **STALE-COMMENT (×6 total.)**

Separately, `docs/reference/exit-codes.md:547` still documents exit `6` =
`NOT_COMPARABLE` for `scan --against`, a command ADR-068 retired. Docs
drift, not Action drift, but it is the reference the Action's own dispatch
should be checkable against.

### A6. CLI surface with no Action channel

Value-taking `compare` options reachable only through the `extra-args`
tokenizer, with no first-class input: `--abi3`, `--bundle-facts-out`,
`--bundle-facts-library-manifest`, `--contract`, `--dump-manifest`,
`--instantiation-manifest`, `--max-findings-per-library`, `--output-dir`,
`--pack`, `--post-manifest`, `--probe-matrix`, `--select`,
`--select-required`, `--use-cases`, `--variant`, `--view`. Flags:
`--diagnostic-comparison`, `--include-system-declarations`,
`--no-baseline`, `--scope-public-headers`/`--no-`.

This is not itself a defect — `extra-args` is the documented escape hatch —
but it is the reason the tokenizer's correctness is load-bearing: every one
of these options is *only* ever seen by the Action as a token the
hand-maintained table must classify. ADR-049's contract-coverage axis, which
`run.sh` gates on unconditionally, is reachable only via
`extra-args: --contract ...`.

### A7. Summary

| Verdict | Count |
|---|---|
| DRIFTED | 3 guards (two known, one new) + both option tables + one short-cluster comment |
| STALE-COMMENT | 6 (4 exit-code, 1 check-target, 1 the tokenizer's own no-live-CLI premise) |
| UNGUARDED (latent) | 1 (no-baseline guard list vs. 27-entry CLI table) |
| AGREES | 7 release/format guards, 10 input mappings, all 7 exit codes |

Every DRIFTED item shares one mechanism: **a CLI fact was copied into shell
and nothing re-derives it.** Every STALE-COMMENT item shares a second:
**a justification names a CLI symbol and nothing checks the symbol exists.**

---

## Part B — Prevention mechanisms, assessed

### B1. Derive the tables from the installed CLI (not from a snapshot)

**Revised after the premise correction above.** Because `run.sh` and
check-target's overlay step both run *after* `pip install`, the right answer
for the option tables is not a committed artifact at all — it is to ask the
installed CLI. One `python -c` introspection call emitting the value-taking
option set for the command about to be invoked, consumed by the existing
tokenizer, replaces both hand-maintained `case` lists with a derivation that
is correct by construction for *whatever abicheck version the workflow
actually installed* — including a version newer or older than the Action's
own checkout, which no committed snapshot can ever be right about.

- **Catches:** all of A2, permanently, and strictly better than a snapshot:
  it cannot drift, and it is version-correct rather than merely
  repo-correct. Removes the duplication between the two lists outright.
- **Misses:** A1 and A4 entirely — a guard's *reasoning* is not an option
  table. Also misses `validate-inputs.sh`'s format choice sets, which are
  genuinely pre-install; those are the only remaining case for a generated
  artifact, and they are a three-line `click.Choice` set, not a table.
- **Cost:** low. `run.sh` already resolves `_PY_BIN` and already verifies
  `abicheck` imports (`action/run.sh:2095`), so the machinery exists. Needs a
  fallback for the documented case where the interpreter cannot import
  abicheck — and the honest fallback is the one `run.sh` already uses there:
  fail loudly rather than guess, or degrade to "treat every unknown token as
  opaque", which is the safe direction for the *missing*-entry failure mode.
- **Home:** `action/run.sh` + `actions/check-target/action.yml`; a unit test
  asserting the derived set equals live introspection. No new gate, no
  generator, no committed artifact.

A committed `--check` generator (the `scripts/gen_changekind_stub.py` pattern)
remains the right shape *only* for `validate-inputs.sh`'s pre-install needs.
Using it for the tokenizers would institutionalize a snapshot where a live
query is available — which is how this class of drift started.

### B2. Cross-surface equivalence check

An executable analogue of
`compatibility_evaluation_frontend.cross_front_end_differences()`: equivalent
Action inputs and CLI invocation must resolve to the same request.

- **Catches:** A1 and A3 in principle — this is the only candidate that
  addresses guard *semantics* rather than option *names*, because a guard
  that rejects what the CLI accepts is exactly a resolution difference.
- **Misses:** nothing in class, but it is the hardest to make real: the
  Action's "resolved request" does not exist as an object. `run.sh` resolves
  to an `argv` array, not a `CompareRequest`. Building the comparison means
  either teaching `run.sh` to emit its resolved argv for inspection (cheap,
  and `--dry-run` nearly does this already) or giving the Action a typed
  resolver (expensive, and duplicates what Part C eliminates).
- **Cost:** high if done as a resolver; **low if narrowed to "run.sh's
  assembled argv must be accepted by the real Click parser"**, which is the
  90% version: parse the emitted argv with `compare.make_context()` and
  assert no `UsageError`. That form would have caught nothing in A1 (the
  Action refuses to *emit* the argv), which is the key limitation.
- **Home:** a unit test under `tests/`, not a gate of its own.

### B3. Matrix CI job running the Action's shell against a real CLI

Drive `action/run.sh` over a matrix of input combinations with a real
installed `abicheck` and assert each guard decision matches what the CLI
accepts.

- **Catches:** A1 and A4 — directly, and with no modelling. A cell that sets
  `lang: c` with a directory operand fails at the Action's `::error::` while
  the equivalent CLI invocation succeeds; that asymmetry *is* the finding.
- **Misses:** A2 (extra-args tokenizing is not exercised by input
  combinations) and the stale comments.
- **Cost:** highest. The guard space is combinatorial, and the cells that
  matter need real binaries and a real toolchain — this is `test-action.yml`
  territory, already the Action's most expensive lane. Practical only as a
  small curated set of *guard-justification* cells, one per CLI-justified
  guard, not a matrix.
- **Home:** `.github/workflows/test-action.yml`.

### B4. Structural gate pinning each guard to the CLI symbol it mirrors

An `ai-readiness`-style check requiring every CLI-justified guard to carry a
machine-readable reference (`# cli-mirror: abicheck/cli_resolve.py::_reject_compile_context_for_set_inputs`)
and failing when the symbol disappears.

- **Catches:** all 5 STALE-COMMENT findings, immediately and cheaply. Would
  have caught the `cli_scan.py` references the day `cli_scan.py` was deleted.
- **Misses:** A1 and A2's substance. The symbol
  `_reject_compile_context_for_set_inputs` still exists — it just rejects
  less than it used to. Symbol *existence* is a much weaker invariant than
  symbol *behaviour*, and the two known drifts are both behaviour changes
  inside a surviving symbol. This is the mechanism most likely to be mistaken
  for protection it does not provide.
- **Cost:** low. One checker in the ai-readiness family, plus annotating
  ~20 guards.
- **Home:** `scripts/check_ai_readiness.py`.

### B5. Delete the mirrored semantics (the structural answer)

Stop encoding CLI semantics in shell. Reduce the shell preflight to
validating **Action inputs only** — input grammar, required combinations,
retired Action inputs, and the Action's own staging limitations (A1 #7 is a
genuine example of one) — and let the installed CLI own flag acceptance,
config resolution and precedence. `run.sh` already has the machinery to
surface a CLI refusal well (`_is_cli_error()`, the exit-64 arm).

- **Catches:** A1, A3 and A4 by **removal** — a guard that does not exist
  cannot drift. This is the only candidate that shrinks the surface instead
  of instrumenting it.
- **Misses:** A2. The `extra-args` tokenizer survives any amount of guard
  deletion: it must classify tokens to decide what `run.sh` itself may inject
  (`--write json=`, `-o`), which is the Action's own concern, not the CLI's.
  B1 is its complement, not its competitor. (It is *not*, as an earlier
  revision claimed, "irreducibly pre-install" — it runs post-install and can
  ask the CLI; see the premise correction at the top.)
- **Cost:** moderate, and mostly *behavioural* rather than mechanical — it
  trades a fast, specific, pre-install Action error for a slower, more
  generic CLI error after install. That is a real regression for the cases
  `validate-inputs.sh` was built for ("fail fast: an unsupported input
  combination used to surface only after a multi-minute toolchain install",
  `action/AGENTS.md`). Mitigation: keep the fail-fast guards whose rule is
  the Action's own, drop the ones that only restate the CLI's.
- **Home:** `action/run.sh`, `action/validate-inputs.sh`,
  `actions/check-target/validate-inputs.sh`; no new gate.

### B6. Assessment

No single mechanism covers the audit. The three failure classes are distinct
and want different answers:

| Class | Answer |
|---|---|
| A CLI *fact* copied into shell (A2) | **B1** — derive it from the installed CLI |
| A CLI *restriction* re-implemented in shell (A1, A3, A4) | **B5** — delete it |
| A CLI *symbol* cited in a comment (A5) | **B4** — pin it |

B2 and B3 are both strictly more expensive than the combination above while
covering a subset of it. B3's one irreplaceable property — it tests the real
shell against a real CLI — is worth keeping as a handful of curated cells,
not as the primary mechanism.

---

## Part C — Recommendation

**B5 + B1, in that order of importance, with B4 as a cheap third.**

The ordering matters: B5 shrinks the surface, and only then is it clear how
little is left to derive. Delete the mirrored semantics, then derive the
tokenizer tables from the installed CLI (3a) and generate only the handful of
facts that are genuinely needed before install (3b) — if those earn a
generator at all.

### Does this need an ADR?

**Yes, for the boundary rule (Phase 2-3); no, for Phase 1.**

ADR-047 owns the GitHub Actions integration model and ADR-037 D10.1 already
establishes the analogous rule one layer down — a front end must route
through the Tier-2 service rather than calling a Tier-1 core entry point, and
`scripts/check_ai_readiness.py`'s `cli-contract` check enforces it with a
line-pinned allowlist. What Phase 2-3 proposes is the same rule one layer
further out, and it changes observable Action behaviour (an input combination
that fails in 5 seconds today would fail in 4 minutes, or succeed). That is
an architectural decision with a migration, which AGENTS.md's authority rule
puts in ADR territory.

**Drafted: [ADR-070 — The Action Layer Does Not Encode CLI
Semantics](../adr/070-action-layer-does-not-encode-cli-semantics.md)**
(Proposed, not implemented). Its four rules are what Phases 2 and 3 execute:
D1 input grammar only, D2 the CLI owns flag acceptance and configuration
resolution, D3 derive a needed CLI fact from the *installed* CLI rather than
transcribing it, D4 a justification cites a symbol that exists. It also
states the accepted cost in full — deleting a restriction mirror moves some
failures after the toolchain install and replaces a tailored Action message
with the CLI's — and records why the three rejected alternatives are the
plan's Phases 1/4/5 rather than its answer.

(An earlier revision of this paragraph said "a draft sketch is in Phase 5
below". There was no such sketch; the pointer was dangling. Noted rather than
quietly deleted, since it is the same defect class this document audits.)

Phase 1 needs no ADR: it strengthens an existing test's invariant and
corrects data that test should already have been protecting.

### Phase 1 — Make the existing guard bidirectional and multi-command *(landed)*

The smallest slice that proves the mechanism, and the only one landed here.

- `tests/test_extra_args_is_value_option_completeness.py`: ground truth
  becomes the **union** of value-taking options over every command the Action
  actually invokes (`compare`, `dump`, `deps tree`, `deps compare`) —
  correct because `_effective_format()` runs after the mode dispatch — and
  the assertion becomes **bidirectional**: a surplus entry fails too.
- Both lists corrected against that ground truth.
- `_extra_args_expand_short_clusters`' `-j` claim corrected in both copies.

**What it must prove, and did:** that the tightened test fails on the lists
as they stood and passes once corrected — i.e. that the invariant is
executable, not prose. Recorded outcome:

* Tightening first, before touching either list, failed 4 assertions naming
  all 17 discrepancies (12 surplus × 2 files, 4 missing × 2 files).
* Re-introducing a single stale entry (`--lang`) fails the surplus assertion;
  re-introducing the `j` cluster terminal fails the terminal assertion. Both
  verified by mutation, not assumed.
* `tests/test_action_run_sh_helpers.py::test_every_known_value_char_expands`
  was a **second** pin of the same `-j` claim — it asserted the expander
  produced `-v -j`. It now derives its char set from the same introspection
  and gained a companion asserting `-vj` is *not* a cluster. A behavioural
  test written to confirm a stale snapshot is itself part of this bug class.
* `cli_surface.copied_option_table_went_stale` registered in
  `tests/regressions/manifest_tool_surface.py`, with the direction/command/
  spelling axes and the "Phase 3 would remove the duplication rather than
  test it" gap recorded.

A1's guards belong to the **already-registered**
`cli_surface.capability_guard_diverged_from_pipeline` class, whose own first
known gap records that no sweep was run beyond `compare`'s set-input guards.
This audit partially discharges that gap — from the Action side — and the new
class is deliberately scoped to option *tables* so the two do not overlap.

Deliberately scoped: it touches none of the A1 guards the separate task owns.

### Phase 2 — Delete the mirrored restrictions (B5)

Depends on the separate task's fix of A1 #1/#2/#3 landing first, so the two
efforts do not edit the same guards.

- Classify every `::error::`/`_fail` in `action/validate-inputs.sh`,
  `action/run.sh` and `actions/check-target/validate-inputs.sh` as
  *Action-input rule* (keep) or *CLI-restriction mirror* (delete).
- Delete the mirrors; rely on `_is_cli_error()` + the exit-64 arm. Keep the
  fail-fast guards whose rule is the Action's own (A1 #7, the
  `upload-sarif`↔`format` coupling, required-input combinations, retired
  Action inputs).
- Replace the A4 nine-entry no-baseline snapshot with nothing: the CLI's own
  `_UNSUPPORTED_OPTIONS` error is the answer, and it cannot go stale.
- Write the ADR alongside.

### Phase 3a — Derive the tokenizer tables from the installed CLI *(landed)*

Both hand-maintained `case` lists are **deleted**. `action/run.sh`'s
`_cli_value_options_init` and `actions/check-target/action.yml`'s
`_ct_cli_value_options_init` query the installed abicheck once per run and
cache the answer; `_extra_args_is_value_option` is a pure-bash substring test
over it.

- **Scoped per command, not a union.** `run.sh` asks about whichever command
  `MODE` selects (`_cli_command_path`). A union would reintroduce the
  surplus-entry failure mode — `--compression` is real on `dump` and absent
  from `compare`, so a union makes it swallow the next real flag under
  `mode: compare`. check-target is fixed to `compare`, since that is the only
  mode it forwards `extra-args` to.
- **Derived once, at top level**, after the interpreter preflight establishes
  `$_PY_BIN_HAS_ABICHECK` and `$MODE` is resolved, because every
  `$(_extra_args_options)` is a command substitution: a subshell inherits the
  cache but cannot populate it. The lazy call inside
  `_extra_args_is_value_option` remains for direct invocation.
- **Same isolation as every other abicheck-importing call** — `cd
  "$_PY_SAFE_DIR"` with a cleared `PYTHONPATH`, since this imports a real
  abicheck submodule and the checkout is untrusted on `pull_request`.
- **The cluster-terminal set is derived too.** `_extra_args_expand_short_
  clusters` asks `_extra_args_is_value_option "-$_last"` instead of listing
  `H | I | o`, which is what had carried `j`.
- **Fails closed, does not fall back** (ADR-070 D3, corrected after review).
  The first implementation treated an undeterminable table as "nothing is
  value-taking", on the reasoning that under-recognition is safe. Codex's
  counterexample disproved it: `extra-args: --version --dry-run` is argv the
  CLI accepts as `--version`'s own value (`dry_run=False`), so an opaque
  tokenizer invents a `--dry-run`, the Action skips its `--write json=`/`-o`
  injection as it must for a real dry run, and a full comparison then runs
  with the requested output never written. Silent, and an *over*-detection —
  the direction argument was wrong too. So an undetermined table is fatal,
  **scoped to a non-empty `extra-args`**: with nothing to tokenize there is no
  decision to get wrong, which keeps a runner whose `python3` cannot import
  abicheck working for every invocation that does not use the escape hatch. A
  static list stays forbidden there.
  `_CLI_VALUE_OPTIONS_DERIVED` exists to keep "no answer" distinguishable from
  "no option takes a value"; conflating them was the original defect.

**What the tests had to become.** The Phase 1 test parsed the `case` bodies,
so it could not survive their deletion — and replacing it mattered more than
patching it. `tests/test_extra_args_is_value_option_completeness.py` now
*executes* the shell functions and compares against live Click introspection:
per-mode set equality, the command-scoping property stated separately, a
roll-call of the twelve retired names, end-to-end tokenizer behaviour for both
drift directions, the cluster terminals, and the fail-closed path (new surface the lists never
had, including that the error message names the counterexample — otherwise the
next maintainer to hit it "fixes" it by reinstating the fallback). Two
mutations were run to confirm the invariants bite: changing `dump`'s scoping to
`compare` fails 3 tests; adding a baked fallback list fails 2.

`tests/test_action_run_sh_helpers.py`'s harness needed extending. Its
`_helpers_region()` stops at run.sh's `# Build the abicheck command` marker —
*before* `$_PY_BIN`/`$_PY_SAFE_DIR`/`$_PY_BIN_HAS_ABICHECK` exist — so without
a prelude supplying them every tokenizer test would silently have exercised
the *fallback* path while appearing to test the real one. That is the
"test takes a shortcut into the dependency" anti-pattern root `AGENTS.md`
warns about, so `_cli_introspection_prelude()` was added and wired into all
four script-assembly sites (one of which, `_run_predicate`, was missed on the
first pass and caught by four failing tests).

### Phase 3b — A generated artifact for the genuinely pre-install facts

Unchanged and still open, deliberately small: `validate-inputs.sh`'s format
choice sets (`compare`'s seven, `deps`' three, `--compression`'s four) are the
only CLI facts needed before install. They already AGREE (A3), so this is
drift *prevention*, not a fix — a `scripts/gen_action_cli_surface.py --check`
emitting a flat `action/cli-surface.txt`, wired as a `Step` in
`scripts/verify.py`'s catalog. **Defensible to defer:** three `click.Choice`
sets that have never drifted may not earn a generator and a gate.

### Phase 4 — Pin guard justifications to CLI symbols (B4)

- A `cli-mirror` annotation convention for every surviving guard that names a
  CLI symbol, plus an `ai-readiness` check resolving each reference.
- Fix the 5 STALE-COMMENT sites as the first consumers.
- Document explicitly, in the check's own docstring, that symbol existence is
  **not** behavioural agreement — B4's limitation is the thing most likely to
  be forgotten, and a gate that is trusted for more than it proves is worse
  than no gate.

### Phase 5 — Curated behavioural cells (B3, optional)

One `test-action.yml` cell per surviving CLI-justified guard, asserting the
Action's decision and the CLI's decision agree. Only worth it after Phase 2
shrinks the guard set to something enumerable.

This is also the phase that would earn
`cli_surface.copied_option_table_went_stale` a real `public_surfaces` entry.
That field is `()` today and must stay so (Codex review, PR #1234): its
contract reserves `github-action` for "a real execution of a
workflow/composite-action step", and Phase 1's seed tests read the shells'
source and call individually sourced helper functions instead. The class of
defect that gap leaves uncovered is specifically the one only the real
runner shows — `INPUT_*` quoting, and `CMD+=($INPUT_EXTRA_ARGS)`'s word-split
under a live IFS — so one `extra-args` cell whose value resembles a flag
would buy more than its cost even before the rest of Phase 5.

---

## Regression-test contract

Bug class: **"a CLI fact copied into the Action layer goes stale with no
failing check."** Registered as `cli_surface.copied_option_table_went_stale`
in `tests/regressions/manifest_tool_surface.py` (landed with Phase 1, not
deferred to Phase 2 as an earlier revision of this line said). Phase 1's
bidirectional union assertion is its executable invariant for the
option-table half, derived from live Click introspection rather than any
hand-listed expectation.

Two things that entry deliberately does *not* claim, both worth preserving
when it is next edited:

- `public_surfaces` is `()`. Its contract reserves `github-action` for a
  real workflow/composite-action execution, and Phase 1's seed tests read
  shell source and call individually sourced helpers instead. Phase 5 is
  what earns it back.
- Its remediation points at **Phase 3a's live CLI query**, not at a
  committed generated artifact. The artifact was this plan's own first
  (wrong) answer, written under the premise the top of this document now
  refutes; a future audit reading the registry must not be sent back to it.

The guard-reasoning half of the audit (A1, A4) belongs to the pre-existing
`cli_surface.capability_guard_diverged_from_pipeline` class, not to this
one — see that entry's own first known gap, which this audit partially
discharges from the Action side.
