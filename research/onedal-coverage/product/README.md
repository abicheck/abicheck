# CPU contract inventory and mutation receipts

Target: conda-forge dal 2025.9.0, Linux x86-64 SysV, C++17, GCC-built consumers, Clang 19 AST, four CPU DSOs. See ../README.md for the 23 release controls. This campaign uses stripped ELF and public headers. Old-client execution is independent of the checker.

## Inventory and current coverage

169 source entry headers include daal.h and installed modern headers outside detail, backend, gpu and spmd directories. Six parameter headers independently fail because an included backend header is absent. roots.txt and unparsed.csv preserve those cases. Successful translation units yield 13,422 candidate rows. Scope follows installed paths and C++ access, so undocumented or inline declarations can occur. Legacy declarations outside the daal.h include closure, protected interfaces, variable exports, alternate macro configurations and member-template specializations require additional inventory. Exact name matching currently handles ordinary functions and methods. It undercounts constructors, destructors and dependent instantiations. Some headers parse through a shared translation unit. A successful parse does not establish standalone includability.

ledger.csv.gz joins Clang declaration USRs and obligation kinds to header locations, mangled symbols, DSO providers, direct imports from the 23 clients, and registered operator-family witnesses. Empty evidence cells mean unknown. The 2,052 distinct matched exports include 1,118 record-returning names. 63/2,052 matched names occur in client imports. Of 13,422 candidate rows, 12 have an explicit field/slot mutation witness (10 fields and 2 methods). This measures linked evidence per inventory row. It is a lower bound and gives no estimate of detection probability. Multiple operations may apply to one declaration. DSO wrappers around range in contracts.py are separate mechanism probes. The denominator for real-product coverage excludes these synthetic functions.

For the next product-specific witnesses, the return spellings services::Status and data_management::NumericTablePtr account for 708 and 186 provider-matched return rows. Exercise nontrivial copy/destruction and old-client ownership across DSOs. Resolve the six missing-root contexts and constructor/destructor ABI variants before widening the public denominator. dependencies.txt lists actual dependency SONAMEs and version needs from readelf. A version-node probe checks old-loader behavior independently; the minimum deployable oneDAL environment has not been established.

## Reproduction

Install clang==19.1.7 Python bindings, libclang 19, GCC and the old CPU environment from ../conda. Pass all 23 retained consumer executables to inventory.py PREFIX OUTPUT CLIENT... . ledger.py OUTPUT writes the compressed ledger and coverage.json. LIBCLANG overrides the libclang library path. Set ABICHECK_AST_FRONTEND=clang and put clang++ on PATH. Run contracts.py PREFIX and bash provider_move.sh here. Run visitor.py PATH_TO_PILOT_OUT after the earlier pilot.

contracts.csv records nonzero old-client results and both levels of checker evidence for each broken variant and its compatible twin. visitor.csv repeats the inherited field and virtual-slot changes with stripped binaries and three controls. provider-move.csv tests a real DT_NEEDED edge between two synthetic DSOs: moving the explicit instantiation preserves the old client when its new owner is shipped, and prevents loading when that DSO is omitted. A scalar scan of the old provider against the forwarding DSO says BREAKING in both cases. This is a scope-dependent result for the single file. Bundle-level classification remains to be tested. These mechanism fixtures do not assert that oneDAL made either change.

## Follow-up on package resolution and object lifetime

Running provider_move.sh now scores the directory entry point too. With a DT_NEEDED edge to the shipped owner, the old client exits 0. Omitting the owner gives loader exit 127. The default directory comparison returns release BREAKING, bundle NO_CHANGE and zero bundle findings for both variants. The removed export in the original member explains the release exit. A consumer-impact diagnostic still needs to distinguish the two deployment outcomes. This experiment concerns one unversioned explicit instantiation on Linux. Other consumers could depend on the original provider directly, including through handle-specific lookup.

ownership.cpp adds a release control using oneDAL error Status, Status copy/assignment/clear, NumericTablePtr ownership transfer, and a data read after the original owner goes out of scope. Compile once and run both versions:

```bash
p=/tmp/dal-2025.9.0
g++ -std=c++17 -O1 -I"$p/include" -I"$p/include/dal" ownership.cpp "$p"/lib/libonedal{_core,_thread}.so.3 -o ownership
for v in 2025.9.0 2025.11.0; do LD_LIBRARY_PATH="/tmp/dal-$v/lib" ./ownership || exit; done
```

Both runs completed 256 iterations with exit 0. This tests the exercised lifetime behavior. It does not count all functions returning those types as covered.

| Owner | Next change | Acceptance test |
|---|---|---|
| abiforge | Generate same-layout nontrivial return types with changes to copy, destructor or ownership behavior. Record whether the old client sees the right value after producer scope exit and whether a counting deleter runs exactly once. Pair with a compatible implementation change. | Baseline, broken and compatible variants run independently. Check values and lifetime events even if process exit is zero. Record an evidence limit for behavior-only changes with identical declarations. |
| abicheck | Add package-level consumer binding evidence for provider moves. Resolve the old imported name and symbol version through the new loader dependency closure, including search scope. | Distinguish shipped, absent, wrong-version and unreachable owners. Preserve the individual member finding and disclose unknown deployment search paths. |
| Inventory | Map passing consumer operations to specific exported signatures, ABI variants of constructors/destructors and pass/return obligations. Investigate each of the six rejected roots in its intended build context. | Track newly resolved rows and remaining reasons. Count each mutation against its specific obligation and target. |
