# Handover: oneDAL r2 follow-ups, remaining scope

**For:** the next agent working in `abicheck/abicheck`
**Branch:** `claude/vibrant-hamilton-s24sap`
**Last change:** 2026-10-24

This file and `handover/patches/` are handover material, not product code. Delete the `handover/` directory before opening a PR.

## 1. What is already done (merged to `main`)

| PR | Content |
|---|---|
| abicheck/abicheck#1783 | Fixes H1–H8 from the oneDAL #3693 handover. |
| abicheck/abicheck#1789 | Follow-up work, listed below. |

Changes in #1783:

- **H1:** extractor-generation stamp on snapshots, plus spelling migration (`AutoType` becomes `auto`).
- **H2a/H2b:** the template-instance-absence false positive (at headers depth, and with overloads).
- **H3:** readable `rule_id`.
- **H4:** qualified type index.
- **H5:** SYCL undeclared-export census.
- **H7:** `size_t` typedef retention.
- **H8:** per-depth detector input contract.

Changes in #1789:

- Frozen generation corpus (`tests/fixtures/generations/`) and the `generation-corpus` readiness gate.
- Spelling-drift demotion, and the `--require-same-generation` flag (exit 64 on mismatch).
- `--depth binary` collapses depth-excluded detectors into one summary line.
- Report schema 6.6: `rule_identity` and `stale_rule_ids`.
- `tests/field_shapes/` corpus and the `model_warning_hygiene` gate.
- Fix in `scripts/check_mutation_score.py` for binary diff markers that read `/dev/null`.

Maintainer decisions are already recorded in those PRs:

- Keep the generation drift notice.
- No `SCHEMA_VERSION` bump.
- `--require-same-generation` stays a CLI flag.
- Depth-excluded detectors become one summary line, and that line is not an evidence gap.

## 2. Branch state

- `claude/vibrant-hamilton-s24sap` was rebuilt from `main` at 4a9bf6709. The commit `chore: restart branch from main after #1789 squash-merge` is a `-s ours` merge, so the old history is kept and the tree equals main.
- One finished, pushed item sits on top of that: **G5, field_shapes dedup** (commit 170e0a15a, merged as dbec0bba1).
  - Older repro tests now load their sources through `tests/_field_shapes.py`.
  - New guard tests in `tests/test_field_shapes.py` prevent embedded copies.
  - Result: 215 → 218 passing; no PR is open for it yet.
- **`main` moved since the branch was rebuilt:**
  - #1791 bumped `EXTRACTOR_GENERATION` to 15.
  - Nobody froze gen 14, so `python scripts/check_ai_readiness.py` reports a `generation-corpus` error on `main` itself.
  - When you merge `main`, freeze gen 14 with `python scripts/freeze_generation_corpus.py --ref a486625db^`. That is the commit before #1791.
  - Then check that the `SPELLING_MIGRATIONS` row for 15 has an audited reason (#1791: "model castxml vector types through the clang AST"). Audit whether it changes a stored spelling. If it does, add a rewrite row and test it in `tests/test_generation_corpus.py`.

## 3. Unfinished items (agents were stopped by the user)

Each item has a WIP patch in `handover/patches/`. All four were made against `origin/main` at **904915283**.

To apply one:

```bash
git checkout -b work/gN origin/main
git apply --3way handover/patches/<name>.patch
```

Expect conflicts with each other, since g2 and g3 both touch storage and generation files. None of the patches were validated end to end. Treat them as a head start, not as done work.

### G1: `--depth binary` projection must match a binary-only dump

- **Patch:** `g1-depth-binary-parity.patch` (5 files).
- **Problem:** a header-built snapshot projected to `--depth binary` (`policy/depth_projection.py`) gives a different set of findings than dumping the same library without `-H`.
  - Example (struct gains a field): projection gives `struct_size_changed`; the binary-only dump gives `type_size_changed` + `type_field_added_compatible`. The verdict is the same.
  - Also: catalog case06/21 give `func_removed` vs `func_visibility_changed`/`func_static_changed`; case36/40 give `struct_field_removed` vs `type_field_removed`.
- **Root cause found:** the projection clears `declarations.types/enums`. The compare then falls back to the raw-layout path (`struct_*` kinds). A binary-only dump instead keeps DWARF records in `types/enums`, so the type detectors fire.
- **WIP fix:**
  - `debug_layout_declarations()` in `abicheck/compare/debug_layout_view.py` rebuilds layout-only records and enums from the debug layout.
  - The projection uses it. The agent's last step was "wire it into the projection"; check that this is complete.
  - New bug-class id: `evidence.depth_projection_diverges_from_shallow_dump`.
- **WIP tests:**
  - New `tests/test_depth_projection_matches_shallow_dump.py`.
  - Edits to `tests/test_depth_projection.py` and `tests/test_depth_metamorphic_examples.py`.
- **Remaining:**
  1. Finish the wiring.
  2. Handle the case06/21 function-kind difference (not addressed yet).
  3. Write an integration metamorphic test over the catalog: `projected(headers→binary) == binary-only dump`, compared by kind and symbol. Any remaining difference goes in an allowlist with a reason.
  4. Unit tests.
  5. BugClass registration in `tests/regressions/` (and in `families.py`, see the gotchas).
  6. Changelog fragment.
  7. Update `docs/contribute/known-gaps.md` if a residual difference remains.
  8. If the projection changes findings on the golden or example cases, update the expectations deliberately.

### G2: no "Duplicate mangled symbols skipped (first-wins)" warning on benign duplicates

- **Patch:** `g2-duplicate-symbol-warnings.patch` (12 files). The agent was near the end, waiting on a test run.
- **Problem:** dumping catalog case80 (pimpl) and case126 (sycl device impl) warns about:
  - glibc C++ const/non-const overloads (`wcschr`, `strchr`, `memchr`…) that share one mangled symbol;
  - `__abicheck_ctor__std::...` placeholder keys for implicit `std::` template constructors.
  
  The maintainer decided these benign duplicates must not warn. Real conflicting duplicates must still warn.
- **WIP fix:**
  - New `abicheck/extract/headers/castxml/duplicate_declarations.py`, wired from `castxml/functions.py`, which merges or classifies the duplicates at the source.
  - Removes the entries from the `tests/_model_warning_hygiene.py` allowlist.
  - Bumps `EXTRACTOR_GENERATION` (versioning.py), adds a migration row, re-records fingerprints, and adds `tests/fixtures/generations/gen15/`.
- **Conflict warning:** `main` already took generation 15 (#1791). Renumber this bump to **16**:
  1. Freeze gen 14 and gen 15 properly.
  2. Re-run `scripts/record_generation_fingerprints.py --axis extractor --bump`.
  3. Refreeze with `scripts/freeze_generation_corpus.py`.
- **Remaining:**
  1. Rebase onto current main and fix the generation numbering.
  2. Run `tests/test_castxml_duplicate_declarations.py`, the hygiene tests, the generation tests, and an integration dump of case80/case126 with zero first-wins warnings.
  3. Add a negative control: a conflicting duplicate from library headers still warns.

### G3: count exports hidden by any `#ifdef`, not only SYCL

- **Patch:** `g3-ifdef-guard-census.patch` (32 files, the largest). Stopped mid-implementation, while still writing files.
- **Goal (maintainer-approved):**
  - Find the header regions the preprocessor excluded under the parse's defines.
  - Attribute undeclared exports to the guarding macro.
  - Report one coverage line per macro: "N exported symbols declared only under #ifdef FOO (not defined; pass -D FOO)".
  - Add a structured JSON field (guard → count + examples) with an additive report-schema MINOR bump.
  - Keep the existing `sycl::` rule in `model/undeclared_export_census.py` as a fallback.
  - Only emit when something is attributed.
- **WIP approach:**
  - Captures preprocessed text and guard info through the castxml backend (`extract/headers/castxml/preprocessed_text.py`, `ast_config.py`, `compile_provenance.py`, `manifest.py`, `tu_fragment.py`).
  - Stores it on the snapshot (`model/snapshot.py`, storage codec, legacy and sparse sections, `snapshot_schema_versions.py`).
  - Feeds `confidence.py` and the report (`report/json_sections.py`, schemas).
  - **Re-check:** it touches `snapshot_schema_versions.py`. The maintainer ruled no `SCHEMA_VERSION` bump for informational fields earlier. Confirm whether this new persisted fact really needs one. If it changes what a dump records, it also needs an `EXTRACTOR_GENERATION` bump, a migration row, and a corpus freeze.
- **Remaining:**
  1. Complete the implementation.
  2. Unit tests for the guard scanner: nested `#if`/`#elif`/`#else`, `#ifndef` include guards ignored, `defined(A)&&defined(B)`.
  3. Integration test, Linux-only for the `nm -D` oracle: a lib exporting functions under `#ifdef FOO` and `#ifdef SYCL_LANGUAGE_VERSION`. Check the counts per guard. With `-D FOO` the count drops to 0, and removing a FOO function reports a removal.
  4. New corpus shape `tests/field_shapes/ifdef_generic_guard/`: only `v1/v2` `api.h`/`api.cpp` plus `ground_truth.json`, per the G5 guard.
  5. Docs (`docs/use/dump-compare-flags.md`), changelog, ADR surface registry if a report field is added.
- **Consider:** if this is too big, split it. First the in-memory guard scanner from the `-H` files, with no new persisted fact; then persistence.

### G4: deterministic mutation test-selection generator

- **Patch:** `g4-mutation-selection-determinism.patch` (3 files). The code is done; the regeneration was not run yet.
- **Problem:** locally, `scripts/gen_mutation_test_selection.py` dropped `tests/test_type_indirection.py`, but CI's "mutmut stats selection is complete" check flagged it. Reach depended on xdist scheduling.
- **Root cause found:** memoized `only_mutate` functions (e.g. `canonicalize_type_name`) were already cached by an earlier test in the same worker, so they were not executed again.
- **Fix:**
  - `scripts/mutation_reach_trace.py` calls `abicheck.model.execution_cache.clear_all_caches()` before every test.
  - Both modes print a `method_description()`.
  - `tests/test_mutation_test_selection.py` adds an order × workers test (memoized function, `("a","b")`/`("b","a")`, `-p no:randomly` / `-n 2`).
- **Remaining:**
  1. Verify that `clear_all_caches` exists and covers every process-lifetime cache.
  2. Regenerate `tests/mutation_test_selection.txt` with the fixed generator (full suite, about 25 min).
  3. Make sure the CI check and the generator share the code path.
  4. Run the `tests/test_mutation_*.py` tests and `check_ai_readiness.py` (scripts line caps and inventory).

## 4. Still for the maintainer (not code)

- Re-run the original oneDAL case on current `main` to confirm `public_surface_shrank` is gone. It never reproduced locally.
- Re-dump the oneDAL baselines. Otherwise every compare shows the generation-drift notice; the current generation is 15.

## 5. Repo gotchas learned in this work

- **Validation:**
  - Run `python scripts/verify.py --profile pr` before a PR. It takes about 30–40 min, so run it in the background and keep the tree still while it runs.
  - Write the PR body with `.claude/skills/pr/` and check it with `BUGFIX_CONTRACT_BODY_FILE=<file> python scripts/check_bugfix_test_contract.py`.
  - Each contract answer must be on one line.
- **Bug-class registry:**
  - A new `BugClass` needs a `fixed_by` PR number.
  - It must be assigned in `tests/regressions/families.py`. A harnessed family requires the family harness test in `seed_tests`, and `OTHER` has a budget of 25.
- **Changing an extractor:**
  1. Bump `EXTRACTOR_GENERATION`.
  2. Add a `SPELLING_MIGRATIONS` row with an audited reason.
  3. Re-record fingerprints with `scripts/record_generation_fingerprints.py`.
  4. Freeze the retired generation with `scripts/freeze_generation_corpus.py --ref <last commit of that generation>`.
- **CI quirks:**
  - **Codecov:** `codecov/patch` may post a low number before the unit shards upload. Wait for the combined `unit` upload before acting on it.
  - **Mutation selection:** a new test that reaches `only_mutate` code must be added to `tests/mutation_test_selection.txt`. Regenerate it with `scripts/gen_mutation_test_selection.py`.
  - **New CLI flags:** a new flag needs a flag-budget ruling (`frontends/cli/options/rulings.py`), no-baseline wiring, F2 route-parity rows, and the ADR surface registry. `docs/contribute/generated/adr-surface-coverage.md` is generated by `python scripts/check_adr_surfaces.py --write-report`.
- **Cross-platform tests:** macOS and Windows integration lanes run the new tests.
  - Use `read_text(encoding="utf-8")`.
  - Skip ELF-only oracles (`nm -D`, DWARF in the binary) off Linux.
  - Give Mach-O dylibs a fixed install name (`-Wl,-install_name,@rpath/...`).
  - Don't hard-code platform-specific detector names.
- **Git:**
  - `git push --force-with-lease` is blocked by the permission classifier. To restart this branch after a squash-merge, use `git merge -s ours origin/<branch>` and then do a normal push.
  - Commits must be by `Claude <noreply@anthropic.com>` and signed. `%G?` prints `N` locally only because no allowedSignersFile is set; the gpgsig header is present.
