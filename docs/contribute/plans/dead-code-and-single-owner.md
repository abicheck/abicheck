# Dead code and single owners

Status: Stage A done (PR #1448); Stage B item 1 and Stage C done; Stage B
items 2-3 continue in [usecase-path-tracing](usecase-path-tracing.md);
Stage D open.

## Why

Line coverage is high, but much of it came from tests calling functions no
command, workflow or documented API calls. And several building blocks had
more than one implementation, so two paths through the product answered the
same question differently. Both were found by running the real CLI scenarios
(not the test suite) under `coverage run` over the 169 catalog library pairs,
then cross-checking every unexecuted definition for production references to
a fixpoint, and by comparing the competing implementations directly.

## Rules for this work

- **Dead** means no caller in `abicheck/`, `scripts/`, `action/`, `actions/`,
  `.github/` or `pyproject.toml`, and not documented as Python API in
  `docs/use`, `docs/reference` or `docs/learn`. A test-only caller does not
  make code live.
- Documented API (including API an accepted ADR or plan names), test hooks and explicitly planned primitives are kept until
  a decision says otherwise.
- A test that used a removed function as an independent oracle keeps it as
  test code; it is never just deleted.
- Each duplicate is collapsed onto one owner, with a test that states the
  shared contract over a generated input space and fails on the old code.

## Done in PR #1448

Removed (about 10k lines): leftovers of the deleted `merge`, `collect` and
`scan --artifact-set` commands, the appcompat renderers, the stderr
annotation path, and about 40 smaller unreferenced functions.

Fixed while removing:

- Markdown dropped every finding of a flooded non-gating kind.
- `compat` could not read back its own `.dump`.

Single owners:

| Question | Owner | Former divergence |
|---|---|---|
| `-std=` spelling, edition order | `model/language_standard.py` | first vs last flag; `c++98` ranked above `c++20` |
| Internal namespace names | `model/symbol_ownership.py` | export accounting missed `__detail`/`_impl` |
| Integer specifier order | `model/int_spelling.py` | `long unsigned int` vs `unsigned long` across backends |
| New-library exports in appcompat | `model/export_index.py` | private table reader |
| Stored-pack integrity | `pack_io.verify_integrity` via `pack_load` | defined, never called |
| Verdict to legacy exit code | `policy.severity.legacy_exit_code` | `--used-by` kept its own map |

## Stage A — behavioural duplicates (done)

| # | Question | Owner | Bug it closed |
|---|---|---|---|
| 6 | `-std=` on castxml's command line | `extract/castxml_compiler_emulation.castxml_parser_arguments` | MSVC `/std:` was read by castxml as a file name; L2 and L4 parses failed |
| 7 | Is a finding in a frozen namespace | `post_processing.match_frozen_namespace` + `policy/frozen_namespace.py` | demotion looked at the root type only, so a finding escalation would tag was demoted first |
| 8 | Is an entity standard-library | `model/source_graph_query.is_stdlib_owned_name` | `mylib::Wrapper<std::string>` read as stdlib |
| 9 | Override parameter identity | `model/signature_normalization.canonicalize_function_signature_param_type` | vtable keys missed array adjustment and callback cv |

## Stage B — make the measurement reproducible

1. **Done.** `scripts/usecase_paths.py` records which functions the
   automated scenarios and the real-binary catalog flows execute
   ([usecase-path-tracing](usecase-path-tracing.md)), and its `dead`
   subcommand (`scripts/production_references.py`) applies this plan's rule
   to a recording: every unreached function whose production references
   all lie inside other dead functions, to a greatest fixpoint, with
   documented and ADR/plan-named API listed apart and kept as roots. The
   weekly `usecase-paths.yml` run publishes the list, so it is recomputed,
   never hand-made. `tests/test_production_references.py` checks the
   fixpoint against breadth-first reachability over generated call graphs.
2. Windows and macOS recordings, and 3. wider sources (the composite
   Action, PR-comment rendering, the hybrid frontend, `post_manifest`,
   `debian_symbols`, a multi-library release) are the same work as
   usecase-path-tracing's "Widen the sources" and "Windows and macOS"
   steps, tracked there. Until they land, PE, Mach-O and PDB readers and
   those flows read as unreached on Linux for want of a recording, not
   because they are dead.

## Stage C — decisions (done)

Each item was wired into a real path, deleted, moved to `tests/`, or kept
with the ADR or page that names it:

| Item | Decision |
|---|---|
| `product_baseline.py` (~1,900 lines + ~3,800 of tests) | **Deleted.** It reimplemented directory `compare` with diverging semantics (no suppression, no ADR-065 scope record, every missing library a removal) and carried ADR-065 D3's open silent-fallback item. A whole product is compared with directory/package `compare`; `--bundle-facts-out` stores its baseline. ADR-065's D3 item is closed by the deletion. |
| `bundle_side_input.compare_bundle_sides` and its `LiveBundleInput`/`StoredBundleFactsInput`/`resolve_bundle_side` layer | **Deleted.** The CLI has one driver per operand shape instead (`compare_release_against_bundle_facts`, `workflows/bundle_stored_pair_compare.py`). |
| `bundle_multibuild.py`, incl. `coverage_regression_findings` | **Deleted, with `ChangeKind.BUNDLE_VARIANT_COVERAGE_REGRESSED` (410 → 409 kinds).** G38 had made the CLI half's deferral permanent and storage-format-v2 A1.6's `compare/variant_pairing.py` is the pairing that runs, report-only by design; the kind could never be emitted. G38 Phase 3 carries the amendment. A gate on an unmatched required variant belongs to ADR-065's completeness axis, not a `ChangeKind`. |
| `buildsource/archive_graph.defining_members` | **Wired.** `localize_symbol` (`graph explain`, documented Python API) reports `defined_in_archive_members` through it — the "`cache_dispatch.o` in `libinternal_dispatch.a`" answer its reference page promised. Moved to `model/source_graph_query.py`, since `compare` may not import `extract`. |
| `accepted_main_cache_key` / `accepted_main_cache_restore_prefix` | **Wired.** `update-main-baseline.yml`'s "Compute cache key" step calls them instead of restating the key in bash, which had drifted: `baseline-generation: 03` keyed `-g03` while the manifest recorded 3, and `abc` built a key before `actions/baseline` rejected it. `parse_baseline_generation` applies `actions/baseline`'s acceptance rule. |
| `cross_front_end_differences`, `unstatable_selectors` | **Moved to `tests/_cross_front_end.py`.** They compare two resolved configs; no run holds two. |
| `source_smoke.run_source_smoke` | **Moved to `tests/source_smoke.py`.** Its only runtime caller is the examples harness; ADR-060 calls it a fixture oracle. |
| `sycl_context.decode_and_select_frontend_context`, `decode_frontend_contexts`, `select_frontend_context` | **Moved to `tests/_sycl_context_oracle.py`**, the independent non-streaming oracle the production `..._from_path` decoder is checked against. |
| `storage/identity.group_by_entity` | **Deleted** (named nowhere; its tests now state the same contract on `OccurrenceSet`). |
| `impact/use_cases.build_use_case_graph`/`join_use_case_graph` | **Deleted.** No tracked item consumes the joined graph: `compare --use-cases` answers over the plain library graph (`explain_use_case_impact`). Their entrypoint-resolution tests now assert on the shared resolution owner. ADR-057's status carries the amendment; the `USE_CASE_*` edge kinds stay registered as reserved. |
| `post_manifest.diff_manifests` | **Kept:** documented release-script API in `docs/use/post-python.md`. |
| `storage/availability.for_entity`/`missing_families` | **Kept:** the inert ADR-062 Phase 0 primitive (D3) that one-semantic-pipeline Phase 8 wires; deleting `for_entity` would leave `override()` writing data nothing reads. |
| `contract_replay.replay_original_decisions` | **Kept:** ADR-049 D6's "replay original decision" procedure, which ADR-067 builds on. |
| `workflows.input_resolution.load_env_matrix` | **Kept:** ADR-068's documented migration path for the retired `env_matrix_path`. |

The removed Python names are registered in `scripts/retired_surfaces.py`, so
a page that still presents one as live is flagged.

## Stage D — the recomputed list (open)

`python scripts/usecase_paths.py record --source scenarios --source flows
--build-catalog DIR` followed by `usecase_paths.py dead` lists, at the time
of writing, **128** dead functions (no production reference, undocumented),
25 named only in user docs and 75 named only by an ADR or plan. The largest
groups: orphaned `_CastxmlParser` methods in `dumper_castxml.py` (11),
`reporter_markdown.py` section builders kept only by a re-export from
`reporter.py` (9), `dwarf_advanced.py`'s CFI helpers (7),
`buildsource/compiler_record.py` (6) and `pdb_parser.py` accessors (6) --
the last waits for a Windows recording before it counts. Most of the rest
are single accessors and cache-reset hooks.

Work it the same way as Stage C, module by module: wire, delete, move to
`tests/`, or keep with the page that names it. A cache-reset or
`reset_for_testing` hook that exists for test isolation is a test hook and
is kept. Recompute the list rather than editing a copy of it.
