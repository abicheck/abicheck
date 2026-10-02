# Dead code and single owners

Status: Stage A done (PR #1448); Stages B and C open.

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
- Documented API, test hooks and explicitly planned primitives are kept until
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

## Stage B — make the measurement reproducible (open)

1. A `scripts/` entry point that builds the catalog, runs the scenario set
   under coverage and lists unexecuted definitions with their production
   references, so the dead-code list is recomputed rather than hand-made.
2. Run it on the Windows and macOS CI runners. PE, Mach-O and PDB code
   (`pdb_parser`, `pe_metadata`, `macho_metadata`) read 0% on Linux only
   because no such toolchain exists there; it is not evidence of dead code.
3. Widen the scenario set to the composite Action, PR-comment rendering,
   the hybrid frontend, `post_manifest` and `debian_symbols`, which the
   first run never invoked.

## Stage C — decisions needed (open)

Each item is either wired into a real path or deleted; none should stay as
code only tests reach.

1. `product_baseline.py` (~1,900 lines): documented API with "no CLI wiring,
   none planned".
2. Public functions with no CLI: `compare_bundle_sides`,
   `accepted_main_cache_key` (the workflow reimplements it in shell),
   `defining_members`, `unstatable_selectors`,
   `cross_front_end_differences`, `post_manifest.diff_manifests`.
3. Test infrastructure inside the package: `source_smoke.run_source_smoke`,
   `sycl_context.decode_and_select_frontend_context` -> move to `tests/`.
4. Storage-model readers with no production caller
   (`storage/availability.for_entity`/`missing_families`,
   `storage/identity.group_by_entity`,
   `contract_replay.replay_original_decisions`): check against ADR-063 and
   ADR-049 and either wire or delete.
5. `impact/use_cases.build_use_case_graph`/`join_use_case_graph`: tie to a
   tracked item or delete.
