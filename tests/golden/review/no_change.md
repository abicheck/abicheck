## ABI review — `libfoo.so` 1.0 → 2.0

**Verdict:** ✅ `NO_CHANGE` — no ABI/API change — safe to merge
**Policy:** `strict_abi` · **Gate:** not configured

> ⚠️ No binary metadata available; verdict is based on header analysis only

**Evidence:** recorded snapshot facts; no debug-derived layout verification, no source replay.

| Category | Count |
|---|---|
| ❌ Breaking (ABI) | 0 |
| ⚠️ API breaks (source) | 0 |
| ⚠️ Risk findings | 0 |
| ✅ Public additions | 0 |
| 🔒 Filtered (internal/private) | 0 |

**Disposition audit:**

| Disposition | Count |
|---|---|
| Detected (raw) | 0 |
| Effective (gating) | 0 |
| … non gating | 0 |
| … suppressed | 0 |
| … out of contract | 0 |
| … unresolved relevance | 0 |
| … deduplicated | 0 |

**Not evaluated:** 20 detector(s) — `elf_layout`, `fingerprint_renames`, `kabi`, `long_double`, `elf`, `pe`, … and 14 more

**Details:** export the same completed comparison with `-o markdown=abi-report.md -o json=abi-report.json`.
