## ABI review — `libfoo.so` 1.0 → 2.0

**Verdict:** ✅ `COMPATIBLE` — backward-compatible — safe to merge
**Policy:** `strict_abi` · **Gate:** not configured

> ⚠️ No binary metadata available; verdict is based on header analysis only

**Evidence:** recorded snapshot facts; no debug-derived layout verification, no source replay.

**Review:** 0 gating finding(s) in 0 gating group(s); 1 retained group(s) total

1. **helper** — added
   New public function: helper
   Action: Review the detailed finding and its evidence.
   Findings: func_added

| Category | Count |
|---|---|
| ❌ Breaking (ABI) | 0 |
| ⚠️ API breaks (source) | 0 |
| ⚠️ Risk findings | 0 |
| ✅ Public additions | 1 |
| 🔒 Filtered (internal/private) | 0 |

**Disposition audit:**

| Disposition | Count |
|---|---|
| Detected (raw) | 1 |
| Effective (gating) | 0 |
| … non gating | 1 |
| … suppressed | 0 |
| … out of contract | 0 |
| … unresolved relevance | 0 |
| … deduplicated | 0 |

**Not evaluated:** 20 detector(s) — `elf_layout`, `fingerprint_renames`, `kabi`, `long_double`, `elf`, `pe`, … and 14 more

**Details:** export the same completed comparison with `-o markdown=abi-report.md -o json=abi-report.json`.
