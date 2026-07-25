<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Two more CI-red-at-scale regressions in the ADR-050 comparability gate**,
  found immediately after the previous single-header-scope fix (same commit
  going live in `dumper.py`), both hitting the identical class of bug: a
  rename-prone name leaking into a fingerprint field with nothing left to
  disambiguate once the declared surface collapses to one entry.
  - `profile_fingerprint`'s `include_sequence` field: P3's auto-added owned
    include root (`resolve_inferred_header_roots`) makes a lone `-H v1.h`
    umbrella's own parent directory a project-owned `-I` slot even with no
    explicit `--include` at all. The owned-slot token embedded the
    declared header's own basename via its dir-relative-path component, so
    a legitimate single-header rename (`v1.h` -> `v2.h`,
    `examples/case189`'s and `case191`'s exact CI failure) flipped
    `include_sequence` — and therefore `profile_fingerprint` — even though
    `header_sequence` and scope's `headers` field both already correctly
    collapsed to `"<single-header>"`. Fixed by short-circuiting
    `_slot_token_for_ancestor` to a constant placeholder whenever the whole
    declared surface is one logical header **and** there is at most one
    owned, unlabeled include-dir slot for it — gating on single-header
    alone regressed `test_13c` (two NESTED project-owned roots, e.g. `-I
    work` and `-I work/include`, that both own the exact same header still
    need distinct per-slot tokens to preserve genuine search-order
    sensitivity), so the collapse only applies when there is truly nothing
    left to disambiguate.
  - `scope_fingerprint`'s `public_header_dirs` field: a lone `-H
    old=<dir>`/`-H new=<dir>` umbrella directory (`abicheck scan`'s
    side-aware header option) fed its own basename into
    `public_header_dirs` with the same load-bearing-rename problem as the
    already-fixed `headers` field — `test_perf_binary_scan.py`'s
    `test_headers_depth_matrix_args_stays_l2_only_and_fast` (`old-include`
    vs. `new-include`) is the exact CI reproduction. Fixed the same way:
    a single declared public-header directory collapses to a
    `"<single-header-dir>"` placeholder; two or more still fingerprint by
    real per-directory identity.
  - Root-caused alongside a genuinely separate, pre-existing bug this same
    incident exposed: `cli_scan_baseline.py`'s `_run_baseline_compare` fed
    the old side's `-H old=<dir>` umbrella into `public_headers` as a raw
    **directory** "path" (`bl_public_headers = bl_headers`, unsplit) while
    the candidate side always splits file-vs-dir via
    `_public_provenance_set`. This asymmetry was harmless before the
    comparability gate went live but produced a genuinely different
    declared-surface representation between old and new once it did —
    fixed by applying the same file/dir split to the baseline side.

- **`TestCrossProducerUnmangledIdentityKnownLimitation`'s two castxml/clang
  parity tests** (`tests/test_castxml_clang_parity_gate.py`) now pass
  `diagnostic_comparison=True` to `compare()` — pre-existing failures (since
  the earlier, already-merged `dumper.py` contract-wiring commit, not this
  session's fixes) caused by the comparability gate correctly detecting that
  this test class's `castxml_snap`/`clang_snap` fixtures are, by design,
  extracted under genuinely different compile contexts (two different AST
  frontends). This class exists specifically to document that known
  divergence, so the diagnostic escape hatch (ADR-050 D2's sanctioned
  best-effort-diff mode) is the intended way to reach it.

- **A new build-context carve-out in the ADR-050 comparability gate**
  (`abicheck/comparability.py`'s `check_contracts_comparable`), mirroring
  the existing platform-identity carve-out: a `profile_fingerprint`
  mismatch confined to `language_standard`/`macro_ops` no longer hard-fails
  when both snapshots were actually parsed against real build-system
  evidence (`AbiSnapshot.parsed_with_build_context` on both sides) —
  `examples/case98_cxx_standard_floor_raised`'s `--build-info`/`--sources`
  CI lane is the exact reproduction. A genuinely build-reconciled C++
  standard raise or macro delta between two real build configurations is
  precisely the fact `CXX_STANDARD_FLOOR_RAISED`/
  `ABI_RELEVANT_BUILD_FLAG_CHANGED` (`diff_build_config.py`) exist to
  surface as a `COMPATIBLE_WITH_RISK` finding, not a reason to refuse a
  verdict outright — the gate was, until now, blocking the very
  build-context-drift detection it's the tool's job to perform. Requires
  **both** sides to carry that build-context evidence (a one-sided real
  build is still the "manifest/CLI-flag drift" mistake the gate exists to
  catch), and is scoped to `language_standard`/`macro_ops` alone — a
  co-occurring, unrelated profile mismatch (e.g. a genuinely different
  `compiler_family`) still raises.

