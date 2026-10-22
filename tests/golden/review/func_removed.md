## ABI review — `libfoo.so` 1.0 → 2.0

**Verdict:** ❌ `BREAKING` — binary (ABI) break — blocks merge under a strict gate
**Policy:** `strict_abi` · **Gate:** FAIL (exit 4)

> ⚠️ No binary metadata available; verdict is based on header analysis only

**Evidence:** recorded snapshot facts; no debug-derived layout verification, no source replay.

**Review:** 1 gating finding(s) in 1 gating group(s); 1 retained group(s) total

1. **helper** — removed
   Public function removed: helper
   Action: Review the detailed finding and its evidence.
   Findings: func_removed

| Category | Count |
|---|---|
| ❌ Breaking (ABI) | 1 |
| ⚠️ API breaks (source) | 0 |
| ⚠️ Risk findings | 0 |
| ✅ Public additions | 0 |
| 🔒 Filtered (internal/private) | 0 |

**Count populations:** 1 detected; 1 retained (1 gating, 0 non-gating); 1 review group(s).

**Public surface:** 0 additions, 1 removals, 0 modifications. Runtime/dependency: 0 finding(s).

**Header/binary consistency:** 0 introduced, 0 resolved, 0 persistent, 0 not evaluated.

**Disposition audit:**

| Disposition | Count |
|---|---|
| Detected (raw) | 1 |
| Effective (gating) | 1 |
| … non gating | 0 |
| … suppressed | 0 |
| … out of contract | 0 |
| … unresolved relevance | 0 |
| … deduplicated | 0 |

**Not evaluated:** 20 detector(s) — `elf_layout`, `fingerprint_renames`, `kabi`, `long_double`, `elf`, `pe`, … and 14 more

**Details:** export the same completed comparison with `-o markdown=abi-report.md -o json=abi-report.json`.
