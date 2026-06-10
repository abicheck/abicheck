# Real-World Scan Coverage — 2026-06 issue catalog

**Date:** 2026-06-10
**Source:** `reports/abicheck-realworld-cron/issues.md` (211 issue records,
2026-06-06 → 2026-06-09), produced by scanning real conda-forge / upstream
shared libraries.

**Purpose:** Map every theme in that catalog onto (a) an existing regression
**test** and (b) a tracked **use case** in
[`usecase-registry.yaml`](../docs/development/usecase-registry.yaml), so we can state precisely what
is already guarded, what is a deliberate product/policy control, and what is a
genuine gap. This is the audit companion to the
[Use-Case Coverage Evaluation](../docs/development/usecase-coverage-evaluation.md).

---

## Headline

- **All five issues the catalog classifies as a *confirmed abicheck bug* are
  fixed and carry an explicit, named regression test.** None are open.
- **The recurring false-positive / policy categories each map to a real test
  module** — the catalog mostly re-confirms existing guards on new products.
- **The large remaining categories are product/package ABI breaks (correct
  `BREAKING` verdicts) and validation-harness/campaign infra**, not abicheck
  defects.
- **One genuine, previously-untracked gap surfaced 21 times:** header-scoped
  scans abort in host system headers before any comparison. It is now tracked as
  **G16 / `UC-TC-header-scope-robustness`**.

---

## Confirmed abicheck bugs → fix + regression test (all closed)

| Catalog issue | Product | Fix | Regression test |
|---|---|---|---|
| `…-1439-01` SONAME-only change reported COMPATIBLE | libevent_pthreads .so.6→.so.7 | PR #309 — `soname_changed` → `COMPATIBLE_WITH_RISK` | `tests/test_diff_platform_deep.py::…test_soname_changed`, `tests/test_report_classifications_unit.py`, `tests/test_object_size_policy.py` |
| `…-1746-01` / `…-1?` PROJ `pj_release` const string size → BREAKING | PROJ `libproj.so.25` | PR #310 — `symbol_size_changed_const_object` (header-aware demotion) | `tests/test_object_size_policy.py::test_public_const_unbounded_string_*` |
| `…-0111-01` FlatBuffers RTTI flagged as leaked libstdc++ | flatbuffers | PR #314 — symbol-origin RTTI attribution | `tests/test_symbol_origin.py` (`_ZTIN11flatbuffers13FileNameSaverE`) |
| `…-0908-01` zstd likely-rename under-counted as risk | zstd `libzstd` | PR #315 — `func_likely_renamed` stays breaking | `tests/test_binary_fingerprint.py::TestFingerprintRenameDetector` |
| `…-0940-01` yaml-cpp `_ZGVZ…` guard var → libmvec risk | yaml-cpp | PR #316 — guard-variable origin fix | `tests/test_symbol_origin.py::test_cxx_guard_variable_ZGVZ_is_native` |

Validation-harness fix that also landed: PR #321 normalizes version-suffixed
shared-library basenames (`libcapnp-1.4.0.so` → `libcapnp`) —
`tests/test_validation_run_matrix.py`.

## Recurring categories → test mapping

The catalog's own "test creation note" is the natural grouping. Counts are
issue records carrying that note.

| Category (count) | What it asserts | Existing coverage |
|---|---|---|
| Validation-harness: package/DSO selection, split-output, logical-name (46) | Pick the DSO-bearing split package; pair version-suffixed/SONAME names | `tests/test_validation_run_matrix.py`; `validation/scripts/run_matrix.py`. *Mostly campaign infra, not abicheck core.* Residual harness work tracked in §Gaps. |
| Strict binary: removed dynamic exports stay breaking (35) | Removed exported symbol under unchanged SONAME ⇒ `BREAKING` | `tests/test_real_world_false_positives.py`, `tests/test_elf_symbol_filters.py`, examples `case01/case12`; `UC-WF-compare` |
| DWARF surface: public ABI vs private/internal churn (31) | Demote internal/unreachable type churn; keep reachable public breaks | `tests/test_internal_churn_demotion.py`, `tests/test_libuv_private_type_churn.py`, `tests/test_real_world_false_positives.py`; scenario `SC-PUBLIC-SURFACE-SCOPE` |
| Runtime-risk: new symbol-version/dependency floor (21) | New `GLIBC_2.x` / dep floor ⇒ `COMPATIBLE_WITH_RISK`, not break | `tests/test_sprint2_elf.py`, `tests/test_symbol_origin.py`; deeper floor check is **G10 (planned)** |
| Header-scoped / toolchain (21) | Scoped scan succeeds **or** emits an actionable hint | **Gap — only generic castxml errors tested** (`tests/test_castxml_errors.py`). Now **G16 (planned)** |
| ELF symbol-filter: leaked stdlib/template churn (19) | Filter/risk-gate compiler/runtime/STL symbols consistently | `tests/test_elf_symbol_filters.py` (PR #313) |
| Triage: preserve as real-world control (16) | Keep expected product breaks as controls | `tests/test_real_world_false_positives.py`, examples catalog |
| Exported object: public data break vs internal/metadata risk (8 + 1) | Public OBJECT size change ⇒ break; internal-looking ⇒ risk; const string demoted | `tests/test_object_size_policy.py` |
| Symbol-origin: project RTTI/guard-var not a dep leak (6 + 2) | Project RTTI/typeinfo & `_ZGV*` guard vars stay project-owned | `tests/test_symbol_origin.py` (PRs #314, #316) |
| ELF policy: SONAME change is risk unless exports removed (5) | SONAME change alone ⇒ risk | `tests/test_object_size_policy.py`, `tests/test_diff_platform_deep.py` (PR #309) |

### Named products that are *correct product/package ABI breaks* (not defects)

oniguruma `OnigUnicodeFolds1` and the four `libunistring_*` exported-OBJECT size
changes under an unchanged SONAME are **real product breaks**, and abicheck
correctly reports `BREAKING` where `abidiff` is silent — the ELF object-size
mechanism is guarded generically by
`tests/test_object_size_policy.py::test_public_data_symbol_size_change_is_still_breaking`.
Likewise the removed-export-under-stable-SONAME cases (zstd, krb5, nettle, gmp,
gflags, libmamba, benchmark, RE2, protobuf/absl …) are correct `BREAKING`
verdicts and serve as product-policy controls, not abicheck bugs. These need a
*manifest/policy* decision in the campaign, not a code change.

---

## Use-case tracking outcome

The catalog re-confirms gaps that are **already tracked** as planned use cases,
and surfaces **one new** one. Nothing else in the 211 records needs a new
use-case entry — they are either covered capabilities, product controls, or
campaign-harness infra (intentionally outside the abicheck capability registry).

| Theme in catalog | Registry status |
|---|---|
| Header-scoped scan aborts in host system headers (21×) | **NEW → G16 `UC-TC-header-scope-robustness` (partial — diagnostics + `castxml --version` floor probe shipped)** |
| New `GLIBC_2.x` floor as deployment risk (zfp, yaml-cpp, x264 …) | already **G10 `UC-TC-glibc-floor` (planned)** — reinforced |
| Abseil `lts_*` / inline-namespace churn (protobuf, RE2) | already **G15 `UC-CHANGE-inline-ns-version` (planned)** — reinforced |
| auditwheel/version-suffixed DSO pairing (openblas, dav1d) | partly PR #321; vendored-hash topology is **G9 (planned)** |
| Public source-API vs private export disambiguation | covered by `SC-PUBLIC-SURFACE-SCOPE` + `--headers`; depends on **G16** to run on stock hosts |

The new entry is the only catalog theme that was both **recurring** and
**untracked**. See **[G16 plan](../docs/development/plans/g16-header-scope-toolchain-robustness.md)**
for the detailed problem statement and acceptance criteria.

---

## Recommended follow-ups (not blocking)

1. **G16 diagnostics — shipped.** The message-only classifier + `castxml
   --version` floor probe turn 21 dead-end campaign runs into one-line,
   user-fixable errors. A `-D_FloatN` auto-shim was prototyped and rejected (it
   rewrites glibc's own `typedef float _Float32;`); the durable cure is a
   newer-Clang castxml or the libclang extractor (G4). Remaining: a real-host
   integration check.
2. **Validation manifest hygiene** (campaign-side, not abicheck core): record
   the known split-package aliases (`libsqlite`, `liblzma`, `libexpat`,
   `libre2-11`, `libwebp-base`, `libbrotli*`) and prefer SONAME/normalized
   fallback pairing for version-suffixed real DSOs (openblas/dav1d). Several
   "manifest expectation is wrong" records (zstd, protobuf, libxml2) are
   *validation-corpus* fixes, not abicheck verdict bugs.
3. **No redundant tests added**: the product-break and object-size topologies in
   the catalog are already guarded by the modules above; new named duplicates
   would add maintenance cost without new coverage. Provenance is captured here
   instead.
