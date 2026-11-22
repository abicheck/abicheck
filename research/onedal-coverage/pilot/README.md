# oneDAL bounded ABI pilot
Linux x86-64, GCC 14.2, C++17, -O0, abicheck c0b6cb4fd8c1e83e53c0f20fb9919d9584b3e93d. L2 uses direct Clang 19.1.7. oneDAL 2025.9.0 cb26da82f4672a7aad7e8d27be7d6b82fa1f300b.

The product slice covers all pairwise field transpositions within the three base fields, three split fields, two regression leaf fields and two range fields, plus the two regression visitor slots. This gives 8 layout edits and 1 slot edit. abiforge analogues: struct_swap_fields and virtual_reorder. The pilot applies the edits to actual headers in a dedicated harness; those operators in main use their own seeds. No official oneDAL shared object was rebuilt. The visit/feed driver constructs descriptors and calls the user's visitor. The modern range wrapper returns normalize_range's result by value across the DSO boundary. The public regression models accept this visitor for DFS/BFS traversal.

All 9 break the precompiled client. ELF symbols alone abstain on all 9. ELF+ordinary GCC DWARF detects 8. ELF+DWARF+public headers detects all 9, names both moved fields in all 8 pairs, and both changed callback positions. Five control pairs include two baselines, two equivalent-type aliases and an added inline nonvirtual getter. No BREAKING on controls; risk is reported for both aliases. Results have no population-level statistical interpretation.

## Reproduce
Check out oneDAL at the revision above and install abicheck from the indicated revision. Put g++, strip and clang++ on PATH. Run here:
```bash
export ABICHECK_AST_FRONTEND=clang
python run.py /path/to/oneDAL
python range_probe.py /path/to/oneDAL
for side in old slots; do
  (cd out/$side && mkdir -p rich && g++ -std=c++17 -O0 -g -femit-class-debug-always -fPIC -shared -I. provider.cpp -o rich/libpilot.so)
done
python -m abicheck.cli compare out/old/rich/libpilot.so out/slots/rich/libpilot.so -o json=out/slots/rich.json
```
The final command returns 4. Ordinary DWARF reports NO_CHANGE/complete on the same edit. Compare readelf -wi output for TreeNodeVisitor specialization and the DW_AT_vtable_elem_location entries. Rich DWARF identifies the affected class. Headers identify both members.
