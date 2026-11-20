# Tool comparison receipts (abicheck · libabigail abidiff · ABICC)

Produced by `python -m abiforge.run.competitors OUT --select {pilot|all}` at
`b0b8793989c150a5b05f48e4e999a7df4219365a` + this branch; Linux x86-64,
gcc 13.3.0, abidiff 2.4.0, abi-dumper 1.2, abi-compliance-checker 2.3,
Python 3.13.18. Absolute build paths are rewritten to `OUT/`.

| Directory | Selection | Denominator |
|---|---|---|
| `pilot/` | deterministic pilot (rule below), 17 cases × 2 configs = 34 pairs × 4 modes = 136 runs | per row: 17 pairs per (config, mode) |
| `sweep/` | every operator × seed of the registry + one identity control per seed: 408 cases → 816 pairs, 84 infeasible, 731 built × 4 modes | per row: 366 (`dwarf+headers`) / 365 (`stripped+headers`) pairs |
| `e16_reduced/` | E16 record-edit fragment, 88 class representatives × gcc/clang; competitor summary of the `b0b879398` script vs the shared-adapter script on the same libraries | identical apart from timings |

Files: `pairs.jsonl[.gz]` (one line per pair: library/header SHA-256, DWARF
section counts, expected decision and details with their sources, each mode's
argv, exit status, seconds, raw report path, decision, outcomes, verified
inputs), `pairs.csv` (one row per pair × mode), `summary.json`, `table.md`,
`divergences.md[.gz]` (pair id, expectation, report excerpt for every
non-correct run), `figure.svg`, `pilot/raw_reports.tar.gz` (every raw report).

**Pilot rule.** For each expected class in the order ABI break, source-only
break, compatible, risk, unchanged: the first 3 operators in registry order on
seed `c_core`, then `c_elf`, whose truth is config-independent (no rules,
rebuild, build-flag carrier, asymmetric evidence, libstdc++ need, host arch or
licence); plus `glibc_floor_raised` (deployment-target floor) and an identity
rebuild of each seed. IDs: struct_append_field, struct_remove_trailing_field,
struct_swap_fields; typedef_retarget, object_became_const,
header_constant_changed; enum_append_member, func_add, param_rename_header_only;
soname_changed, soname_dropped, symbolic_binding; comment_only,
reorder_declarations; glibc_floor_raised; control_identity (c_core, c_elf).

**Caveat on the sweep.** The operators' expectations were written alongside
abicheck's development (they are abicheck's regression corpus), so the sweep
measures agreement with a ground truth abicheck has been tuned against, not a
neutral benchmark. The pilot's detail truth (header diff, compiler probe) is
tool-independent; the sweep's detail extraction uses simple header regexes
and is reliable for C seeds, approximate for C++.
