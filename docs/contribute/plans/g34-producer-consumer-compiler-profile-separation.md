---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# G34 — Producer/consumer compiler-profile separation and compiler-matrix hardening

**Origin:** a status-review of the toolchain-profile/compiler-matrix surface
(`abicheck/buildsource/project_targets.py`'s `ProfileCompileSpec`,
`abicheck/buildsource/run_plan.py`, `.github/workflows/check-project.yml`).
Confirms the semantic model already in place (a separate scan per compiler
profile, fail-closed aggregation, no cross-product blind sweep) is sound,
but finds the schema conflates two independent axes into one
`profiles.<id>` block, and finds five concrete gaps in the Actions matrix
that block a genuine "one artifact, several supported client compilers"
scenario today. Two narrower items from this review (compiler-version
enforcement; a real toolchain-identity probe) were already flagged as open
in `AGENTS.md`'s "Known gaps" under the now-reverted GCC-argument-rendering
entry — this plan is their actionable home, generalized to the full
producer/consumer split the same investigation surfaced.
**Type:** Initiative plan (cross-cutting; spans
`abicheck/buildsource/project_targets.py`, `abicheck/buildsource/run_plan.py`,
`abicheck/cli_project.py`, `abicheck/aggregate*.py`/`abicheck/service_scan.py`
aggregate reconciliation, `.github/workflows/check-project.yml`,
`actions/check-target/action.yml`, `action.yml`).
**Effort:** XL, phased over multiple PRs — see per-phase estimate below.
**Risk:** low for additive schema fields (Phase 0); medium for Actions-matrix
scheduling changes (Phase C: a real OS-aware runner selection touches a
widely-used reusable workflow); medium-high for the toolchain-identity probe
(Phase A: shells out to the resolved compiler on every gated run, needs a
caching/skip story so it doesn't regress `dump`/`compare` latency).

## Problem

`profiles.<id>` today names exactly one compiler and is used for two
different things at once:

1. **Producer/artifact identity** — the compiler the library binary was
   actually built with (affects mangling, layout, vtables, calling
   convention, exception/RTTI model, the linked standard-library ABI).
2. **Consumer/client identity** — the compiler a user of the library
   compiles their own code with against the public headers (affects which
   `#ifdef __GNUC__`/`__clang__`/`_MSC_VER` branch, which standard-library
   ABI, which template instantiation, which `sizeof`/packing the client
   actually sees).

For the common case (library and its clients share one toolchain) folding
both into one profile is fine and is exactly what today's
`profiles.<id>.compile` (`ProfileCompileSpec`: `compiler_family`,
`compiler_version`, `target`, `standard`, `stdlib`, `binding`,
`abi_macros`, `args`) already does well. It breaks down for "one binary,
several supported client compilers/dialects" (e.g. a `.so` built once with
GCC 14 but contractually supporting GCC 11/14 and Clang 20 clients under
different C++ standards and standard-library ABIs): expressing that today
needs one synthetic profile per producer×consumer combination
(`linux-gcc14-build-client-gcc11`, `linux-gcc14-build-client-clang20`, ...),
each re-declaring the same candidate binary/`build-output.json` under a
different profile id.

On top of the schema gap, five concrete mechanical gaps in
`.github/workflows/check-project.yml` block a real GCC/Clang/MSVC matrix
from running through the shared reusable workflow at all today (each
confirmed by reading the workflow, not asserted from a design doc):

- **Every check cell runs on `runs-on: ubuntu-latest`** (hardcoded,
  independent of a profile's own `os:`/`arch:` fields) — there is no way to
  route an `os: windows` profile's check cell to a `windows-latest` runner
  through this workflow.
- **`dependency-source` is not per-cell.** `actions/check-target/action.yml`
  and `check-project.yml` still forward only the old boolean
  `install-deps: true|false` (`check-project.yml` ~line 839) into the root
  Action, even though the root `action.yml` itself already supports
  `dependency-source: conda-forge|conda-forge-gcc14|conda-forge-clang20|
  system|none` — so a GCC-profile cell and a Clang-profile cell in the same
  run-plan can't each provision their own matching conda environment
  through this workflow.
- **`ast-frontend`/`sources`/`compile-db`/`build-info`/`sysroot`/`policy`
  stay global workflow inputs.** Only `compile_gcc_path`/`compile_gcc_options`
  get a per-cell override (from `profiles.<id>.compile`, per the P1
  toolchain-profile audit already landed) — a project can't express "GCC
  profile parses via CastXML, DPC++ profile parses via the direct-clang
  `icpx` frontend" in one `check-project.yml` call.
- **`compiler_family`/`compiler_version` are shape-validated but not
  enforced.** `run_plan.py`'s own docstring says so explicitly: they are
  "deliberately not projected into any forwarded field" and a real
  toolchain-identity probe "does not exist yet." A profile can declare
  `compiler_family: gcc`, `compiler_version: ">=14,<15"` and have `binding`
  resolve to a Clang 12 executable with no error — the snapshot honestly
  records what actually ran, but nothing fails the gated check for the
  mismatch itself.
- ~~**No per-finding cross-profile reconciliation.**~~ **Closed by Phase D.**
  `aggregate` (G32/ADR-050, done) already produced
  `affected_profiles`/`incomplete_profiles`/`unanalyzed_profiles` and a
  `verdict_by_profile` map at the *target* level, but did not merge the
  *same logical finding* appearing in two different profiles' reports into
  one entry with its own `affected_profiles` list. That limit was a known
  one carried over from G32, not a new gap this review discovered.
  `AggregateResult.finding_matrix` now does it; see Phase D below.
  (The earlier wording quoted a sentence from `index.md`'s G32 row that is
  no longer there — CodeRabbit review; a dangling quote is worse than no
  citation, so the fact is stated directly instead.)

## Goal & acceptance criteria

### Phase 0 — schema: separate producer and consumer profile axes (S/M)

- [x] `.abicheck.yml` gains `profiles.<id>.consumer_compile` (sibling to
      the existing `profiles.<id>.compile`), carrying the *same*
      `ProfileCompileSpec` shape (`compiler_family`/`compiler_version`/
      `target`/`standard`/`stdlib`/`binding`/`abi_macros`/`args`) —
      `ProfileSpec.consumer_compile` (`project_targets.py`), shape-validated
      identically to `compile:` (unknown-key/type/whitespace-injection
      guards all reused via the same `ProfileCompileSpec.from_dict`).
      **Not yet done:** actually resolving/applying it to the
      **header-AST (L2) extraction step only** — this slice is config-schema
      projection only (see the next two open items).
- [x] A profile with no `consumer_compile:` overlay behaves exactly as
      today (`profile.consumer_compile is None`, omitted from `to_dict()`)
      — additive, not a breaking schema change; existing single-profile
      projects need zero edits. Covered in
      `tests/test_project_targets_consumer_compile.py`.
- [x] A profile *with* `consumer_compile:` gets a second extraction pass
      under the consumer toolchain — **landed with a different design than
      originally scoped here.** This item originally called for merging
      L0/L1 producer facts with L2/L4 consumer facts into *one* snapshot
      (mirroring `dumper_hybrid.merge_snapshots()`). PR #860 instead has
      `check-project.yml` run a wholly separate `dump` of the (unchanged)
      candidate binary under the consumer's frontend/binding/options, and
      feeds that materialized snapshot to `compare` as the entire new side
      — no merge with the producer pass's own L0/L1 facts. That is a real,
      working extraction pass (not config-schema projection only any
      more), just a narrower one: the compared snapshot reflects the
      consumer's header view end to end, not "producer binary facts plus
      consumer header facts" as one object. Revisit the original merge
      design only if this narrower shape proves insufficient in practice.
- [x] `run_plan.py` projects `consumer_compile:` into the generated cell
      the same way `compile:` already does, as its own, independently
      resolved `RunPlanCheck.consumer_compile_gcc_path`/
      `consumer_compile_gcc_options` pair (`_consumer_compile_fields_for_profile`)
      — never falling back to the producer overlay's own resolved values.
- [x] `tests/test_project_targets_consumer_compile.py` (shape parsing,
      round-trip, absence-is-None, unknown-key/not-a-mapping validation
      errors), `test_run_plan.py`'s `TestConsumerCompileOverlayProjection`
      (target checks, bundle checks, independent binding resolution), and
      `tests/test_reusable_workflows_project_evidence.py` (the separate
      candidate dump itself: mode, per-cell frontend/binding/options
      forwarding and their fallback to the workflow-global input, new-side
      header/include replacement semantics) cover the schema/projection and
      extraction slices landed here.
- [ ] **Still open:** the baseline (old) side of a real `baseline-channel`
      comparison. `publish-baseline.yml`/`update-main-baseline.yml` read
      only `build-output.json` (no per-profile compile-context fields) and
      never consult `run-plan.json`, so they cannot apply a
      `consumer_compile:` overlay to the baseline dump the way
      `check-project.yml` now does for the candidate. A `consumer_compile:`
      check compared against a real baseline channel therefore usually
      resolves `NOT_COMPARABLE`/`ProfileMismatchError` (the comparability
      gate refusing a genuine profile-fingerprint mismatch, not a silent
      wrong verdict) rather than actually comparing; `baseline-channel:
      none` (audit-only) is unaffected. Closing this needs `actions/
      baseline` and `abicheck/buildsource/baseline_publish.py`'s
      `derive_baseline_libraries()` to gain the same per-library compile-
      context resolution `check-target` already has, sourced from a real
      per-target run-plan read neither baseline workflow performs today —
      see `run_plan.py`'s own docstring for the same note.

### Phase A — toolchain-identity enforcement (L, risk: medium-high)

- [x] A real probe step (`abicheck/buildsource/toolchain_probe.py`) resolves
      a profile's `compile.binding`/`consumer_compile.binding` (via a
      trusted `BindingsFile`, both overlays checked independently) to its
      actual executable, runs a cheap identity check reusing the existing
      raw-`--version`-capture plumbing (`dumper_toolchain._tool_identity_metadata`/
      `_compiler_family_from_toolchain` — no new subprocess-handling code),
      parses the profile's declared `compiler_version` as a comma-separated
      constraint spec (`==`/`!=`/`>=`/`<=`/`>`/`<`, e.g. `">=14.2,<15"`) and
      compares both `compiler_family` (reconciling the schema spelling
      `"gcc"` with the internal `"gnu"` label) and `compiler_version`
      against the probed executable. MSVC (`compiler_family: msvc`, or a
      `cl`/`cl.exe` binding) is deliberately skipped — `cl.exe` has no
      `--version` flag and the reused probe always runs exactly that flag,
      so this is a documented limitation, not an oversight (see the
      module's docstring).
- [x] Wired into `project validate --toolchain-bindings`
      (`cli_project.py`), alongside the existing `check_profile_bindings_resolve`
      call, so a mismatch surfaces as a validation error (exit 1) the same
      way an unresolved binding already does.
- [ ] **Still open:** a hard-fail *before extraction* on `dump`/`compare`
      themselves, as originally scoped above. Investigated and found not
      wireable as a drive-by extension: neither `service.py`'s `run_dump`/
      `resolve_input` nor the `abi_dump` MCP tool accept a `binding`/profile
      parameter at all today — there is no dump/compare call path that
      resolves a `.abicheck.yml` profile's toolchain binding to begin with,
      so there is nothing for this check to hook before. That gap belongs
      to a separate, already-tracked line of work (see `AGENTS.md`'s "Known
      gaps" entry on depth-contract/CLI-vs-API parity for the same class of
      "no such call path exists yet" finding) — closing it needs a real
      dump/compare-time binding-resolution feature first, not an extension
      of this validation-report check.
- [x] The probe result is cached per resolved executable path + mtime/hash
      — inherited for free from `dumper_toolchain._tool_version_output`'s/
      `_executable_sha256`'s existing `@lru_cache`, since this phase reuses
      that plumbing directly rather than re-implementing its own subprocess
      call. Process-lifetime only (not persisted across separate
      `check-project.yml` matrix cells the way `snapshot_cache.py` persists
      to disk) — good enough for the current single-process `project
      validate` invocation this phase wires into; a cross-process cache
      would only matter once Phase C's matrix scheduling is the caller.
- [x] Unit tests (`tests/test_toolchain_probe.py`) stub the probe
      (`_tool_identity_metadata`) so the fast lane has no real compiler
      dependency; one `integration`-marked test class exercises it against
      a real installed `gcc`.

### Phase B — per-profile AST frontend (M)

- [x] `profiles.<id>.compile.frontend`/`consumer_compile.frontend` (both
      overlays, sharing `ProfileCompileSpec`) accept the same values as the
      global `--ast-frontend` (`auto`/`castxml`/`clang`/`hybrid`,
      shape-validated against `api_types.HEADER_AST_FRONTENDS` — the same
      canonical fact source the CLI flag itself resolves against, not a
      hand-duplicated list), overriding the global default for that
      profile's cell only — same precedence pattern already established for
      `compile_gcc_path`/`compile_gcc_options` (profile overlay wins over
      the global input, per the P1 toolchain-profile audit's own comment in
      `check-project.yml`).
- [x] `run_plan.py`'s `RunPlanCheck` gains a resolved
      `compile_ast_frontend`/`consumer_compile_ast_frontend` pair, threaded
      the same way `compile_gcc_path`/`compile_gcc_options` already are
      (`_compile_ast_frontend_for_profile`/
      `_consumer_compile_ast_frontend_for_profile`), for both target and
      bundle checks.
- [x] `check-project.yml`'s check job forwards the resolved field into the
      cell's real invocation as
      `ast-frontend: ${{ matrix.compile_ast_frontend || inputs.ast-frontend }}`
      — the same per-cell-first precedence `gcc-path`/`gcc-options` already
      use, and the step that makes a per-profile frontend real rather than
      projected: a GCC profile's cell resolves `castxml` while a Clang/DPC++
      profile's cell in the same run resolves `clang`, which one
      workflow-global `--ast-frontend` cannot express. The rest of the chain
      (`actions/check-target` → root `action.yml` → `INPUT_AST_FRONTEND` →
      the CLI flag) already existed for the workflow-level input, so nothing
      downstream needed a second pass-through. A profile with no
      `compile.frontend:` (or a run-plan from an older abicheck, where the
      key is absent) falls back to the global input exactly as before.
      Gated on `kind != 'bundle'` (Codex review): a bundle cell's operand is
      the `bundle-staging` *directory* it stages its members into, and the
      root Action rejects every non-`auto` `ast-frontend` for a
      directory/package operand outright (`action/run.sh`'s
      `_is_release_style_operand` guard), because the per-library fan-out
      never threads an L2 compile context to each pair's header dump — so
      forwarding a profile's `frontend:` there would turn a previously
      working bundle check into a hard operational error. The fallback for
      such a cell is the workflow-global input, not the empty string, so a
      bundle cell behaves exactly as it did before this override existed.
      **Note the same hazard exists, unfixed, for `compile_gcc_path`/
      `compile_gcc_options`:** the guard rejects those for a directory
      operand identically, and `check-project.yml` has forwarded them to
      every cell including bundles since the P1 toolchain-profile audit.
      That is a pre-existing bug, not one this phase introduced, and
      closing it is a behaviour change to already-shipped wiring — it needs
      its own decision (silently drop, gate the same way, or make the
      combination a hard `project validate`/`project plan` error the way an
      unroutable `os:` already is), not a drive-by extension here.
- [x] `consumer_compile_ast_frontend` (and its `consumer_compile_gcc_path`/
      `consumer_compile_gcc_options` siblings) are now forwarded — to the
      separate consumer-context candidate dump Phase 0 landed above, never
      onto the producer pass. `check-project.yml` forwards each with the
      same per-cell-first precedence `compile_ast_frontend` uses, falling
      back to the workflow-global `ast-frontend`/`gcc-path`/`gcc-options`
      input (not the empty string, and not the producer overlay's own
      resolved value) when the profile's `consumer_compile:` overlay
      omits that field.
- [ ] **Still open:** a fixture project with one GCC profile (castxml) and
      one Clang/DPC++ profile (direct-clang `icpx`) in the same
      `.abicheck.yml`, exercising two cells that actually invoke different
      frontends end to end. The wiring above is what such a fixture would
      exercise; the fixture itself needs a real DPC++ toolchain on a runner
      (same G17 dependency as Phase C's own remaining native-MSVC lane).
- [x] `tests/test_project_targets_compile_frontend.py` (shape validation,
      round-trip, both overlays independent) and `test_run_plan.py`'s
      `TestCompileFrontendOverlayProjection` (target checks, bundle checks,
      independent resolution, no-override case) cover the schema/projection
      slice landed here.

### Phase C — Actions-matrix native-OS scheduling + per-cell dependency source (L) — **done**

- [x] `check-project.yml`'s check-matrix job's `runs-on:` is derived from
      the resolved profile's `os:` field (`ubuntu-latest`/`windows-latest`/
      `macos-latest`) instead of the current hardcoded `ubuntu-latest`.
      Derived once, at plan time: `runner_label_for_os` (`project_targets.py`)
      is the mapping, `RunPlanCheck.runs_on` carries it, and the workflow
      reads `matrix.runs_on` — so the widest-blast-radius half of this plan
      is a one-expression change in the reusable workflow rather than
      scheduling logic embedded in YAML. A profile with **no** `os:` (every
      profile written before this phase) resolves to `ubuntu-latest`
      unchanged, which `test_a_profile_without_os_keeps_todays_runner` pins.
      Two decisions worth not re-litigating: `runs_on` is serialized even at
      its default, unlike every other optional field, because a matrix entry
      missing the key resolves `runs-on:` to the empty string and schedules
      *nothing*; and an `os:` naming no schedulable platform is a hard error
      at both `project validate` and `project plan` time rather than a
      fallback to Linux, since a cell scheduled on the wrong platform
      reports success having gated the wrong thing. A GitHub-hosted runner
      label (`ubuntu-24.04`) passes through verbatim — `os:` was a
      free-form, never-consulted string before this phase, so narrowing it
      to platform names only would be a breaking config change dressed up
      as a feature.
- [x] `check-project.yml`/`actions/check-target/action.yml` gain a
      per-cell `dependency-source` input, resolved from the profile the
      same way `compile_gcc_path`/`compile_gcc_options` already are
      (`profiles.<id>.dependency_source` → `RunPlanCheck.dependency_source`
      → `matrix.dependency_source || inputs.dependency-source`), forwarded
      to the root `action.yml`'s existing `dependency-source` input instead
      of only the legacy `install-deps` boolean. Both unset leaves that
      boolean deciding exactly as before — the root Action already owns that
      fallback, so the workflow forwards one expression rather than keeping
      a second copy of the rule. The accepted-value list is mirrored from
      `action.yml` (its own validation case is the fact owner) and
      `TestActionYmlAgreesOnDependencySources` asserts the two agree, plus
      that `check-target` actually forwards the input — a per-cell value
      accepted but never forwarded would be silently inert, which is the
      exact failure this phase is about.
- [x] `docs/reference/check-target.md`, `run-plan-schema.md`,
      `project-targets-schema.md`, and `reusable-workflows.md` document the
      new fields/inputs; `tests/test_project_targets_scheduling.py` covers
      the `os:`-to-`runs-on:` resolution and the `dependency_source` schema
      in isolation (plain Python, no workflow run), and
      `test_run_plan.py`'s `TestSchedulingProjection` covers the projection
      into target and bundle cells.
- [x] **The unset `dependency-source` default is OS-aware.** Making Windows
      cells schedulable for the first time exposed that the existing default
      is a *Linux/macOS* default: an unset `dependency-source` with
      `install-deps: true` resolves to `conda-forge`, and `action.yml` then
      explicitly hard-fails every conda-forge source on Windows (pixi's
      `native-toolchain*` features don't cover win-64), so a Windows cell
      declaring no `dependency_source:` — including this plan's own
      `windows-msvc` example — would have been scheduled straight into an
      `exit 1` before reaching analysis (Codex review, P1). Fixed in the one
      place that already owns the fallback rule and already knows the
      platform: `action.yml`'s own `Resolve dependency-source` step now
      resolves an unset value to `system` on a Windows runner. That is what
      the conda-forge-on-Windows error message already tells users to pick,
      and `install-deps.sh`'s Windows branch warns and continues rather than
      failing, matching the "toolchain is pre-installed on the image" story
      an MSVC lane has anyway. Deliberately *not* fixed by injecting a
      default into the run-plan: that would have made a per-cell value
      silently outrank the workflow-level `dependency-source` input for
      Windows cells only. An **explicit** conda-forge* still fails —
      requesting something unsupported should say so rather than be
      rewritten — and no existing consumer can regress, since the path this
      replaces was an unconditional error. `TestWindowsDependencySourceDefault`
      extracts and *runs* the real resolve script rather than string-matching
      it, so a future edit that keeps the wording but changes the branching
      still fails.
- [x] **The `check` job's own shell steps resolve their Python interpreter.**
      Three of them (`Resolve candidate binary/binaries`, `Synthesize
      pre-check operational-error report`, `Sanitize check-id for artifact
      name`) invoked `python3` directly, which Git Bash on a Windows runner
      does not resolve — the Windows CPython layout ships `python.exe` only.
      Harmless while every cell ran on Linux; a guaranteed failure the moment
      this phase let one land on `windows-latest`, and a compounding one:
      candidate resolution fails, then the envelope-writing fallback fails
      with it, so the cell produces no report at all rather than an
      operational-error one (Codex review). All three now resolve the
      interpreter the way `action/run.sh` already does
      (`PY="$(command -v python3 || command -v python)"`), and
      `test_check_job_shell_steps_resolve_their_python_interpreter` fails on
      any future bare invocation in this job. Two pre-existing tests that
      extracted these steps' embedded Python by splitting on the literal
      `python3 -c` broke on the change and now split on the flag instead, so
      they no longer pin an interpreter name they don't care about.
- [ ] Out of scope for this phase, still open: an actual native
      `windows-latest` CI lane exercising a real MSVC profile end-to-end
      through `check-project.yml` — that needs a real fixture project and
      belongs in G17 (real-world validation corpus) now that the scheduling
      mechanism itself has landed here.

### Phase D — per-finding cross-profile reconciliation (M, depends on G32) — **done**

- [x] `AggregateResult.finding_matrix` (`abicheck/aggregate.py`) extends the
      existing per-profile grouping (G32/ADR-050) with a per-finding one,
      keyed by `aggregate_findings.resolve_report_change_identity` — a new
      read-back adapter that runs the *same* tiered canonical/normalized/
      reduced resolution (ADR-049 Phase 2, the identity model
      `diff_filtering.py`'s cross-detector dedup key already uses) over a
      report's serialized `changes[]` entry instead of a live `Change`. One
      `FindingMatrixEntry` per distinct finding per `base_target`, with its
      own `affected_profiles`/`unaffected_profiles` lists. Because the
      identity model collapses the rich-vs-symbols-only detector pair, one
      event two profiles report under different kinds (`func_removed` where
      DWARF was available, `func_removed_elf_only` where it wasn't) is one
      entry carrying both `kinds`, not two unrelated findings — the case a
      real GCC/Clang/MSVC matrix actually produces. `kinds` unions across
      *every* check a profile ran, not just its first, since one profile can
      run a `binary`-depth and a `headers`-depth check that report the same
      event under the two equivalent kinds.
- [x] Profiles on **different C++ ABIs** no longer falsely clear each other,
      which the mangled-name identity alone could not prevent: one
      declaration is spelled `_ZN3lib3addEii` by an Itanium toolchain and
      `?add@lib@@YAHHH@Z` by MSVC, so a Linux and a Windows profile
      reporting one logical removal produced two profile-specific entries,
      each asserting the *other* profile was clean of it.
      `cross_abi_declaration` recovers the declaration's qualified name from
      either scheme (`diff_cxx_rules.itanium_qualified_name`/
      `msvc_qualified_name` — pure structural parsers, no demangler
      subprocess, so this works identically on every host), and
      `resolve_cross_abi_identity` re-resolves the identity from it, keeping
      the discriminator so a removal and an addition on one declaration are
      not treated as related.
- [x] **That key withholds a clean verdict; it never merges two findings.**
      Neither parser recovers parameter types, so `add(int, int)` and
      `add(double)` reduce to the same key — meaning two profiles matching on
      it may be reporting one shared removal or two unrelated overload
      removals, and *nothing in a report distinguishes those*. Withholding
      needs no proof and is therefore safe; merging asserts a pairing the
      evidence does not establish, so it is not done. A profile holding a
      finding on the same declaration under another mangling is reported
      `undetermined`, and the shared declaration is exposed on the entry so a
      consumer can present the two together without the report claiming they
      are one finding.
- [x] **Withholding applies only where the spellings cannot be compared.**
      Sharing a qualified name is not itself ambiguity: two Itanium manglings
      encode their parameter types, so `_ZN3lib3addEii` vs `_ZN3lib3addEd`
      *proves* two distinct overloads, and reporting either profile as
      merely undetermined threw away real precision on the commonest
      configuration of all — a GCC and a Clang profile, both Itanium. The
      clean verdict is now withheld only when the other profile's spelling
      is in a *different* scheme (Itanium vs MSVC, not comparable without a
      type-encoding translator this module does not have) or normalizes to
      the *same* symbol. That second case is the Mach-O quirk: a macOS
      toolchain prefixes an extra leading underscore, so one entity appears
      as two raw symbols and two primary identities — `comparable_mangled_symbol`
      recognizes them as one. (Fifth Codex review round; the Mach-O sub-case
      was found while implementing it, and is why the rule compares
      normalized symbols rather than just schemes.) (An intermediate revision *did* merge when each
      profile contributed exactly one identity; the third Codex review round
      pointed out that cardinality is not evidence — Linux losing
      `add(int, int)` while Windows loses `add(double)` passes that check and
      would have been published as a single all-profiles finding. Reverted to
      withholding only.)
- [x] **The Mach-O case is a merge, not a withholding.** Withholding was
      still the wrong answer for it: the two spellings normalize to
      byte-identical *complete* Itanium encodings, parameter types included,
      so they are provably one declaration — the exact evidence the
      cross-ABI case lacks. Left split, a Linux and a macOS profile reporting
      one removal produced two `undetermined` entries where one
      `all_profiles` finding is the truth.
      `_merge_equivalent_spellings` re-keys such identities onto one before
      the matrix is built, keyed on `(cross_identity, comparable_symbol)` —
      never on the qualified name alone, so two genuinely different
      overloads spelled by a Linux and a Mach-O toolchain stay two findings
      exactly as two Linux toolchains' would. (Sixth Codex review round; the
      distinction between this merge and the one the third round reverted is
      *whether the whole mangling matches*, not how many identities each
      profile contributed.)
- [x] A finding entry is validated before it counts as *enumerated*: being a
      JSON object is not enough, since one with no `kind` still parses into a
      contentless REDUCED-tier identity and would let a garbage array read as
      an exhaustive finding set. `kind` must be a non-empty string and every
      other identity-essential field must be a string when present — a
      wrong-typed value is rejected rather than coerced, since coercion would
      mint an identity from a spelling no producer emitted. Valid siblings in
      the same array stay usable. (Third Codex review round; the same false
      clean claim as the non-object case, one level down.)
- [x] The rule that validation follows is **accept exactly what the identity
      resolver handles, no less.** `old_value`/`new_value` are annotated
      `str | None` but the annotation is not runtime-enforced, and
      `diff_python.py`'s `python_stable_abi_violation` emissions really do
      pass a list (`new_value=sorted(group)`), which `_change_to_dict`
      preserves verbatim into JSON —
      `finding_identity._stringify_change_value` exists precisely to fold
      that shape. A first cut of the validation above rejected it, which
      dropped a genuine finding *and* marked its report incomplete, demoting
      a demonstrably affected profile to undetermined. List/tuple values are
      now accepted for those two fields and forwarded intact rather than
      filtered to `None`, so they discriminate the identity as they should;
      every other field stays string-only. (Fourth Codex review round —
      a regression the third round's fix introduced.)
- [x] A third list, `undetermined_profiles`, was **added beyond the original
      scope** and is the load-bearing one: a profile whose findings are not
      fully known is neither affected nor unaffected. Without it, this view
      would answer "profile X is clean of this finding" for a profile that
      was never checked — the per-finding form of exactly the "an expected
      target with no report is unknown, never compatible" invariant the rest
      of `aggregate.py` is built on. `ReportFindings` carries the findings
      and a separate `complete` flag so the two facts cannot be conflated,
      and `FindingMatrixEntry.scope` answers `undetermined` ahead of every
      other value whenever any profile is in that state. Six things fall
      short of complete: a missing/unreadable/not-comparable report, a
      report with no finding array, a report with an unparseable or
      non-conformant array element, a `compare-release` report, a `scan
      --against` report (gating buckets only, capped), and a report whose
      array was narrowed for *display* rather than enumerated. Only
      `complete` may *clear* a profile; an incomplete report's findings are
      still read, since seeing a finding proves it is there while not seeing
      one proves nothing.
- [x] All three report shapes participate. A `compare` report carries
      `changes`; a `compare-release` report — what a `kind: bundle` check
      produces, since a bundle comparison routes through the per-library
      release fan-out — has no `changes` at all and instead carries
      `bundle_findings`/`matrix_findings` plus per-library entries that are
      only *counts*; a `scan --against` report itemizes its gating buckets
      under `diff.findings`. All three arrays are parsed, so bundle and
      scan-baseline targets reconcile rather than being written off as
      unknown (sixth Codex review round for the scan shape, which previously
      vanished out of the matrix entirely); the missing per-library detail —
      and, for scan, the deliberately-omitted compatible findings plus the
      20-entry cap — is exactly why such a report can never be `complete`. A bundle
      finding's `consumer_library`/`provider_library` are folded into its
      description using `BundleFinding.to_change`'s own
      `"[consumer ← provider] "` flattening, so two findings differing only
      in which library pair they are about stay two findings. (Both this
      item and the `complete` flag above came from the PR's Codex review;
      the original slice treated every release report as unknown and let a
      partially-unparseable `changes` array read as an exhaustive one.)
- [x] An entry must carry the fields the compare-report schema marks
      *required* on a `changes[]` entry before the array counts as
      enumerated — a `kind` alone is not something a conformant producer
      wrote, so it is no evidence the array is exhaustive. Present-but-`null`
      is accepted: a producer really does emit those keys with `None` for a
      finding carrying no before/after value, and that is emitting the field
      rather than omitting it. (Sixth Codex review round — the type-only
      validation from the third round left the "field absent entirely" hole
      open.)
- [x] **Readability and conformance are two questions, answered separately.**
      A first cut folded both into one predicate over `("symbol",
      "description")`, which was wrong in both directions (seventh/eighth
      Codex review rounds). Too narrow: `old_value`/`new_value` are
      `required` by the schema *and* part of the identity discriminator, so
      an entry omitting them resolved to a different identity while its
      report stayed `complete` — each profile then listing the other as
      unaffected. Too broad: `cli_scan_baseline`'s own findings carry no
      `old_value`/`new_value`/`severity` at all (verified against the
      producer, not assumed), so simply extending the one predicate would
      have dropped every `scan --against` finding the round before had just
      made readable. Split into `_is_usable_finding_entry` (can an identity
      be resolved — governs whether a finding is *kept*, and a kept finding
      can only ever convict) and `_is_conformant_change_entry` (did a
      conformant producer write it — governs whether the array is
      *exhaustive*, the only thing that can clear a profile). The mirrored
      required-field list is checked against
      `compare_report.schema.json`'s own `required` by
      `TestSchemaRequiredFieldsAgree`, since the schema is the fact owner.
      Test fixtures now build `changes[]` entries through one `_change_entry`
      helper that fills the required set — hand-written near-miss fixtures
      are how this validation drifted from producer reality to begin with.
- [x] **Conformance checks the schema's declared *types*, not just key
      presence.** A first cut of the split above tested only that each
      required key existed, so `symbol: null` — schema-invalid, and read as
      an empty spelling that resolves to a different identity than the same
      finding elsewhere — still counted as conformant and left the report
      `complete` (ninth Codex review round). The nullability split is the
      schema's own: `old_value`/`new_value` are declared `["string",
      "null"]` because a finding with no before/after value really is
      emitted that way, while `kind`/`symbol`/`description`/`severity` are
      plain `"string"`. `_CONFORMANT_CHANGE_FIELDS` became a field →
      nullable map and `TestSchemaRequiredFieldsAgree` now pins both halves
      to the schema. `severity` is the one field where the two predicates
      genuinely disagree — required by the schema but not part of the
      identity, so a non-string there is readable yet non-conformant.
- [x] **Structured values are validated element-wise, and so is
      `affected_symbols`.** Checking only the container let
      `old_value: [{"bad": 1}]` through, which `_stringify_change_value`
      folds into the spelling `"{'bad': 1}"` — an identity no producer
      wrote (eleventh Codex review round). `affected_symbols` was worse:
      the adapter `str()`-coerced every element, so a `[123]` became the
      spelling `"123"` and could *collide* with an unrelated profile's
      genuine `"123"` symbol — and `resolve_change_identity` folds that
      whole set into `header_binary_context_mismatch`'s discriminator. It
      is no longer coerced (a non-conformant array reads as absent) and is
      validated in conformance despite being optional, since it is
      identity-bearing when present.
- [x] **Same scheme is proof of distinctness for Itanium only.** An MSVC
      decoration can encode the *target ABI* rather than the declaration:
      ARM64EC inserts a `$$h` tag, so one declaration is spelled
      `?add@lib@@YAHHH@Z` on x64 and `?add@lib@@$$hYAHHH@Z` on ARM64EC —
      verified, both reduce to `lib::add` through `msvc_qualified_name`.
      Two Windows profiles on different targets were therefore reported
      clean of each other's identical removal (eleventh Codex review
      round). MSVC pairs now withhold; Itanium keeps its precision, which
      matters because a GCC/Clang matrix is the commonest configuration
      there is. Withheld rather than normalized away, because `$$h` is the
      decoration this module can *name*, not demonstrably the only one —
      and withholding needs no such proof, which is this module's whole
      asymmetry.
- [x] **A `compare-release` report that errored still contributes its
      findings.** `_format_release_json` emits `bundle_findings`/
      `matrix_findings` for whatever completed, independent of the
      top-level verdict, but `aggregate._load_report_file` returned early
      on `ERROR` and dropped them — losing real evidence from the profile
      most likely to differ (eleventh Codex review round). They are now
      parsed and explicitly marked incomplete at that call site, where the
      reason (the run errored) lives, so they convict their own profile and
      clear no other. The forced blocking exit an `ERROR` report already
      carries is unchanged, with a test pinning it.
- [x] **The structured-value carve-out covers exactly `old_value`/
      `new_value`.** The type check above initially accepted `str | list |
      tuple` for *every* required field, so `severity: []` still read as
      conformant (tenth Codex review round). The carve-out exists for one
      reason — `diff_python.py` really passes a list for those two fields and
      the identity resolver folds it — so it is now scoped to them alone;
      every other required field is a plain string or nothing.
- [x] A well-formed array is not by itself proof that nothing else was
      found: `compare --show-only` narrows `changes` (`reporter.to_json`)
      while the verdict, the gate, and the `summary` block all keep
      describing the whole diff, so a profile that *hid* a finding read as
      one that did not have it. Two signals are checked, because neither
      covers every report mode — `show_only_filter`, emitted by full and
      root-cause mode, and a `summary.total_changes` exceeding the array's
      own length, which is the only signal `--report-mode leaf` leaves. The
      count comparison is the general form of the question, so any future
      display filter that narrows the array while leaving the summary whole
      is caught by it alone. (Seventh Codex review round;
      `TestDisplayFilteredReports` verifies it against the real producer in
      all three report modes rather than against hand-built dicts.)
- [x] A finding present on every profile and one present on only one are
      distinguished by an explicit `scope` discriminator
      (`all_profiles`/`profile_specific`/`partial`/`undetermined`) in both
      the JSON (`finding_matrix`, aggregate schema `1.2`, published in
      `abicheck/schemas/aggregate_report.schema.json`) and the text output's
      `Cross-profile findings:` section. *Note on the acceptance criterion's
      wording:* `aggregate` has `--format json|text` and no Markdown
      renderer, so "JSON/Markdown" landed as JSON + text; adding a Markdown
      format for `aggregate` is separate surface, not part of this phase.
- [x] `tests/test_aggregate_findings.py` (a sibling of `test_aggregate.py`,
      mirroring the source split) covers the merged/split shapes (same
      finding on all profiles, profile-specific, partial), the rich-vs-L0
      kind collapse, every source of `undetermined` including the
      partially-malformed array, the release report, the scan report, and
      the display-filtered one, bundle/matrix/scan findings participating,
      bundle attribution keeping distinct library
      pairs apart, "affected outranks undetermined across one profile's own
      checks", ordering stability, the unknown-future-kind degradation, that
      the gate exit code is unmoved, and schema validation of the new block.

**Not part of this phase, deliberately:** `finding_matrix` is a reporting
view only — it never contributes to `exit_code()`. A cross-profile *gate*
("fail when a finding is profile-specific") would be a new policy axis and
needs its own design, not a drive-by addition to a reconciliation view.

## Design

The producer/consumer split (Phase 0) is additive schema, not a rewrite:
`ProfileCompileSpec` already carries every field a consumer overlay needs
(`project_targets.py:663`); the new block is a second, optional instance of
the same dataclass, resolved through the same `_compose_gcc_options`/
binding-resolution path (`run_plan.py`) but applied only to the L2/L4
extraction step instead of the whole cell. The toolchain-identity probe
(Phase A) is new subprocess-probing logic but slots into the same place
`run_plan.py`'s docstring already names as the missing piece ("checking the
resolved executable against `compiler_family`... requires a real subprocess
probe and is not implemented"). Phase C's `os:`-to-`runs-on:` mapping is
pure data transformation inside `check-project.yml`'s existing matrix
generation step (Python, not new infrastructure) followed by a matrix
`runs-on:` expression keyed off it. Phase D reuses `finding_identity.py`
and `diff_filtering.py`'s existing identity-resolution machinery rather
than inventing a second one.

## Files & surfaces

- `abicheck/buildsource/project_targets.py` — `ProfileCompileSpec`, new
  consumer-overlay dataclass/field, shape validation.
- `abicheck/buildsource/run_plan.py` — per-cell resolution of the consumer
  overlay, the toolchain-identity probe call site, per-cell
  `ast_frontend`/`dependency_source` fields.
- `abicheck/dumper_hybrid.py` — extend `merge_snapshots()` (or add a
  sibling merge path) for producer-toolchain L0/L1 + consumer-toolchain
  L2/L4 merging.
- `abicheck/aggregate*.py` / `abicheck/finding_identity.py` /
  `abicheck/diff_filtering.py` — Phase D's per-finding reconciliation.
- `.github/workflows/check-project.yml`, `actions/check-target/action.yml`,
  `action.yml` — Phase C's scheduling and per-cell `dependency-source`.
- `docs/reference/check-target.md`, `docs/contribute/adr/047-*.md` (or a
  new ADR if the producer/consumer split is judged architecturally
  significant enough at implementation time — this plan doesn't presume
  which).

## Tests

Covered per-phase above. No golden-file changes expected (additive schema
+ new aggregate fields, not a change to existing output shapes).

## Example fixtures

A new `tests/fixtures/run_plan/producer_consumer_matrix/` (sibling to the
existing `tests/fixtures/run_plan/toolchain_matrix/`) with one producer
profile and two consumer overlays (GCC client, Clang client) — mirroring
the existing toolchain-matrix fixture's own disclaimer that it exercises
config/toolchain projection, not real compiler execution; a real
end-to-end GCC-vs-Clang-consumer example (compiled, not just projected)
belongs in G20's example catalog once this plan's schema lands.

## Effort & risk

XL overall, phased as scoped per-phase above (S/M, L, M, L, M) so each
phase is independently mergeable and independently risk-assessed rather
than one large cross-cutting PR. Phase C carries the highest
blast-radius risk (a widely-consumed reusable workflow); Phase A carries
the highest correctness risk (a probe that's wrong in either direction —
false-reject a valid toolchain, or false-accept a mismatched one — directly
gates every project using it).

## Out of scope

- A real native `windows-latest` MSVC end-to-end fixture (belongs in G17
  once Phase C's scheduling mechanism exists).
- A full `artifact_profiles:`/`consumer_profiles:`/`compatibility_matrix:`
  three-block schema redesign as sketched in the originating review — Phase
  0 intentionally ships the smaller "one optional consumer overlay per
  existing profile" version first; a fuller redesign is a candidate
  follow-up only if Phase 0's overlay shape proves insufficient in
  practice.
- Vendor/accelerator frontend correctness (CUDA, OpenACC, non-DPC++ SYCL
  toolchains) beyond what the existing direct-clang backend already
  recognizes as clang-family — no new frontend work is implied by this
  plan.
