# oneDAL CPU ABI coverage on Linux x86-64

Measurements use oneDAL example sources at cb26da82f4672a7aad7e8d27be7d6b82fa1f300b, GCC 14.2 and abicheck c0b6cb4. The wording correction was replayed on the conda core pair. See pilot/README.md for the independently compiled provider and consumer, 42 comparison cells and the richer DWARF control.

| Domain | Denominator | Observation |
|---|---:|---|
| All within-record field exchanges across four specified records plus one visitor slot exchange | 9 breaking edits | Old consumer fails 9. Stripped ELF abstains on 9. GCC DWARF detects 8. Headers plus DWARF detect 9 with member attribution. |
| Identity, equivalent aliases, inline nonvirtual addition | 5 controls | No binary breaking verdict. Both aliases receive risk findings. |
| Packaged CPU runtimes 2025.9 to 2025.11 | 23 precompiled clients | All return zero and match their printed baseline. The tree visitor emits the same 67 nodes. |
| Packaged core 2025.9 to 2026.2 | 53 old core export names imported by the tree client | 7 absent, all reported. SONAME changes from .3 to .4. Loader rejects the old client before main. The new headers also omit the legacy decision-tree API. |
| Three header changes against the unchanged 2025.9 runtime | Slot swap, field swap, type alias | Wrong first callback (exit 7), corrupt feature values (exit 0), unchanged output respectively. Header scan reports BREAKING, BREAKING, COMPATIBLE_WITH_RISK. |

At the root split the valid feature index and threshold are 4 and 0.2405. Swapping their fields produces 4597832946085265408 and 1.97626e-323. Compare values and callback identity in runtime oracles. Ordinary GCC DWARF omitted visitor method records and yielded NO_CHANGE with complete assurance. Adding -femit-class-debug-always detected the class change. Header evidence named both slots.

| Cumulative clients | Distinct imports matching core exports (13,352 names) | Matching libonedal exports (3,670 names) |
|---|---:|---:|
| Tree, covariance, correlation, PCA | 66 | 2 |
| Plus 16 legacy examples | 148 | 2 |
| Plus modern linear, polynomial and RBF kernel examples | 148 | 40 |

These are name intersections. They cannot measure virtual dispatch, inline code, field access or the public fraction of the export table. Numerical examples compare printed precision. The two operator families correspond to abiforge struct_swap_fields and virtual_reorder. The product harness applies them to real declarations. The change to the checker qualifies six unsupported header-absence claims. The seventh removed name has constructor-specific wording.

## Reproduction

Create each prefix with micromamba create -p /tmp/dal-VERSION -f conda/env-VERSION.txt. Clone uxlfoundation/oneDAL at the revision above. In conda/, run python make_probe.py PATH_TO_ONEDAL to generate probe.cpp. Run bash replay.sh PATH_TO_ONEDAL to compile the clients using the old include tree and run them against the two runtime versions. inventory.py accepts the unpacked environment prefixes. mutate.py creates three header overlays under the current directory. For the packaged mutation experiment compile probe.cpp with -I<overlay> before the package include paths, then compare its output on 2025.9 with the saved baseline.

## Coverage expansion and acceptance criteria

1. Fix a product contract per release, target triple, feature configuration and package set. Enumerate public header roots and binary providers together. Inventory exported functions, by-value parameters and returns, exposed records, bases, virtual slots, enums, templates and dependency requirements. Record unresolved header-to-provider mappings and exclusions with reasons.
2. Assign each obligation a stable key (entity, change family, configuration). Record applicable abiforge operator, independent old-client oracle, compatible twin and minimum observable evidence. Preserve unknown and unsupported rows in the denominator. Record evidence per release and compiler.
3. Prioritize uncovered obligations by potential impact and distinct new coverage per build minute. Start with by-value register/return classification, inherited layout and virtual dispatch, followed by template instantiation ownership and dependency or symbol-version floors. Add semantic assertions and unaffected sibling controls. Compare exact affected entities.
4. Publish counts for applicable obligations, generated obligations, independently judged cases, detected breaks, abstentions, false cleans and false alarms. Show strata by binary, DWARF, headers and build context. Track CPU, DPC++, OS and target ABI independently. Add a small representative set to PR CI and retain dense scheduled sweeps for fault-retention checks.
5. Claim completeness only for a named finite domain after every applicable row has an independently judged witness, every expected result agrees and every reduced class has a documented equivalence criterion. Track unresolved observations separately. Product-wide completeness remains open.

The [CPU obligation ledger and stripped-header campaign](product/README.md) extend these measurements.
